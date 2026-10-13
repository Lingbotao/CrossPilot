"""M6-03：租户表业务索引首列是 tenant_id，大列表走游标。

mixin 上的单列 ``created_at`` / ``deleted_at``，以及登录、邀请令牌这类
租户绑定之前就要查的键，记在允许名单里。新的业务索引若不以 tenant_id 开头，本测试失败。
"""

from __future__ import annotations

from sqlalchemy import UniqueConstraint

from app.main import app
from app.models import TENANT_SCOPED_TABLES, Base

# 单列审计/软删索引来自 AuditMixin / SoftDeleteMixin，列表不靠它们过滤。
_MIXIN_SINGLE = frozenset({("created_at",), ("deleted_at",)})

# 这些索引服务的查询发生在租户绑定之前，或列表已有 tenant_id 前缀索引覆盖。
_ACCEPTED_INDEXES = frozenset(
    {
        ("audit_log", "ix_audit_log_user_id"),
        ("login_log", "ix_login_log_email_created_at"),
        ("login_log", "ix_login_log_user_id"),
        ("member_invitation", "ix_member_invitation_email"),
        ("member_invitation", "ix_member_invitation_expires_at"),
        ("tenant_user", "ix_tenant_user_user_id"),
    }
)

# 唯一约束对应的索引。令牌哈希在接受邀请时还没有租户上下文；凭证按店铺雪花 ID 一对一。
_ACCEPTED_UNIQUE = frozenset(
    {
        ("member_invitation", "uq_member_invitation_token_hash"),
        ("shop_credential", "uq_shop_credential_shop_id"),
    }
)

# 账本和大列表必须能用游标往后翻，不能只靠 offset。
_CURSOR_LISTS = (
    "/api/v1/orders",
    "/api/v1/inventory-flows",
    "/api/v1/audit-logs",
    "/api/v1/sync-tasks",
    "/api/v1/listings",
    "/api/v1/ads/campaigns",
    "/api/v1/ads/keywords",
    "/api/v1/purchase-orders",
    "/api/v1/suppliers",
    "/api/v1/first-mile-shipments",
)

# 管理列表行数少，页码分页可以。
_PAGE_LISTS = (
    "/api/v1/members",
    "/api/v1/shops",
)

# 配置或最近窗口，limit 封顶，不改成游标。
_CAPPED_LISTS = (
    "/api/v1/tax-rules",
    "/api/v1/exchange-rates",
    "/api/v1/landed-cost/fees",
    "/api/v1/orders/returns",
    "/api/v1/profit/settlements",
)


def _columns(index: object) -> tuple[str, ...]:
    return tuple(column.name for column in index.columns)  # type: ignore[attr-defined]


def test_tenant_business_indexes_lead_with_tenant_id() -> None:
    seen_accepted: set[tuple[str, str]] = set()
    missing_tenant_lead: list[str] = []
    offenders: list[str] = []

    for table_name in TENANT_SCOPED_TABLES:
        table = Base.metadata.tables[table_name]
        has_tenant_lead = False
        for index in table.indexes:
            columns = _columns(index)
            if columns and columns[0] == "tenant_id":
                has_tenant_lead = True
                continue
            key = (table_name, index.name)
            if columns in _MIXIN_SINGLE or key in _ACCEPTED_INDEXES:
                if key in _ACCEPTED_INDEXES:
                    seen_accepted.add(key)
                continue
            offenders.append(f"{table_name}.{index.name} {columns}")
        if not has_tenant_lead:
            missing_tenant_lead.append(table_name)

        for constraint in table.constraints:
            if not isinstance(constraint, UniqueConstraint):
                continue
            columns = tuple(column.name for column in constraint.columns)
            if columns and columns[0] == "tenant_id":
                continue
            key = (table_name, constraint.name or "")
            if key in _ACCEPTED_UNIQUE:
                seen_accepted.add(key)
                continue
            offenders.append(f"{table_name}.{constraint.name} unique {columns}")

    assert not missing_tenant_lead, missing_tenant_lead
    assert not offenders, offenders
    assert seen_accepted == _ACCEPTED_INDEXES | _ACCEPTED_UNIQUE


def _query_params(path: str) -> dict[str, dict[str, object]]:
    operation = app.openapi()["paths"][path]["get"]
    params = operation.get("parameters", [])
    return {item["name"]: item for item in params if item.get("in") == "query"}


def test_ledger_lists_use_cursor_and_admin_lists_use_pages() -> None:
    for path in _CURSOR_LISTS:
        params = _query_params(path)
        assert "cursor" in params, path
        assert "page" not in params, path

    for path in _PAGE_LISTS:
        params = _query_params(path)
        assert "page" in params and "page_size" in params, path

    for path in _CAPPED_LISTS:
        params = _query_params(path)
        assert "cursor" not in params, path
        limit = params["limit"]["schema"]
        assert isinstance(limit, dict)
        maximum = limit.get("maximum")
        assert isinstance(maximum, int) and maximum <= 200, path
