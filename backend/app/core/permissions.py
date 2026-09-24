"""权限点定义与角色矩阵（PRD 4.3 / 4.4）。

**这份文件是权限的唯一事实来源**：

- 后端 ``app.core.deps.require_permission`` 按它强制校验（前端隐藏不算安全边界）；
- 前端 ``PermissionGuard`` 通过 ``GET /tenants/current`` 拿到权限点列表后按同一份码值渲染；
- 数据库 ``role.permission_codes`` 的种子数据由它生成，不手写。

★ 硬规则（F4 / PRD 4.3 关键设计决策）：
    **采购成本价与利润数据默认对 OPS_STAFF 不可见**。
    一线运营看到成本价，容易在议价或离职后带来风险，这是行业惯例。
    实现方式：``OPS_STAFF`` **不在** ``cost:read`` / ``finance:read`` 的授权集合里，
    并且 cost 类字段的序列化由 ``app.core.deps.require_cost_visibility`` 兜底。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final


class RoleCode(StrEnum):
    """8 个内置角色（租户可自定义，但系统角色不可删改）。"""

    OWNER = "OWNER"
    ADMIN = "ADMIN"
    OPS_MANAGER = "OPS_MANAGER"
    OPS_STAFF = "OPS_STAFF"
    PURCHASER = "PURCHASER"
    FINANCE = "FINANCE"
    CS = "CS"
    VIEWER = "VIEWER"


ROLE_NAMES_ZH: Final[dict[RoleCode, str]] = {
    RoleCode.OWNER: "所有者",
    RoleCode.ADMIN: "管理员",
    RoleCode.OPS_MANAGER: "运营主管",
    RoleCode.OPS_STAFF: "运营专员",
    RoleCode.PURCHASER: "采购",
    RoleCode.FINANCE: "财务",
    RoleCode.CS: "客服",
    RoleCode.VIEWER: "只读访客",
}

ROLE_DESCRIPTIONS_ZH: Final[dict[RoleCode, str]] = {
    RoleCode.OWNER: "租户最高权限，可管理设置、成员与应用内全部数据",
    RoleCode.ADMIN: "除转让所有权外与所有者一致",
    RoleCode.OPS_MANAGER: "运营全流程管理，含店铺授权与看板",
    RoleCode.OPS_STAFF: "日常运营执行；**不可见采购成本价与利润**",
    RoleCode.PURCHASER: "采购与供应商、成本维护；不可见订单与财务",
    RoleCode.FINANCE: "财务报表与利润核算；订单只读",
    RoleCode.CS: "客服：订单读 + 发货处理（受数据范围限制）",
    RoleCode.VIEWER: "全模块只读",
}


class Perm(StrEnum):
    """权限点：``<模块>:<动作>``。新增权限点必须同步登记在这里。"""

    # 租户与成员
    TENANT_READ = "tenant:read"
    TENANT_WRITE = "tenant:write"
    MEMBER_READ = "member:read"
    MEMBER_WRITE = "member:write"
    # 店铺与授权
    SHOP_READ = "shop:read"
    SHOP_GRANT = "shop:grant"
    # 商品
    PRODUCT_READ = "product:read"
    PRODUCT_WRITE = "product:write"
    # 订单
    ORDER_READ = "order:read"
    ORDER_SHIP = "order:ship"
    # 库存
    INVENTORY_READ = "inventory:read"
    INVENTORY_WRITE = "inventory:write"
    # 采购
    PURCHASE_READ = "purchase:read"
    PURCHASE_WRITE = "purchase:write"
    # ★ 成本（F4 硬规则）
    COST_READ = "cost:read"
    # 广告
    ADS_READ = "ads:read"
    # 合规
    COMPLIANCE_READ = "compliance:read"
    COMPLIANCE_WRITE = "compliance:write"
    # 财务
    LANDED_COST_READ = "landed_cost:read"
    LANDED_COST_CALC = "landed_cost:calc"
    FINANCE_READ = "finance:read"
    REPORT_EXPORT = "report:export"
    # 看板
    DASHBOARD_READ = "dashboard:read"
    # 系统
    AUDIT_READ = "audit:read"
    SYSTEM_READ = "system:read"
    SYSTEM_WRITE = "system:write"


_ALL = frozenset(Perm)

ROLE_PERMISSIONS: Final[dict[RoleCode, frozenset[Perm]]] = {
    RoleCode.OWNER: _ALL,
    # 管理员与所有者一致（所有权转让属 F1 单独流程，不靠权限点区分）
    RoleCode.ADMIN: _ALL,
    RoleCode.OPS_MANAGER: frozenset(
        {
            Perm.SHOP_READ,
            Perm.SHOP_GRANT,
            Perm.PRODUCT_READ,
            Perm.PRODUCT_WRITE,
            Perm.ORDER_READ,
            Perm.ORDER_SHIP,
            Perm.INVENTORY_READ,
            Perm.INVENTORY_WRITE,
            Perm.PURCHASE_READ,  # 👁 只读
            Perm.COST_READ,
            Perm.ADS_READ,
            Perm.COMPLIANCE_READ,
            Perm.COMPLIANCE_WRITE,
            Perm.LANDED_COST_READ,
            Perm.LANDED_COST_CALC,
            Perm.FINANCE_READ,  # 👁 只读
            Perm.REPORT_EXPORT,
            Perm.DASHBOARD_READ,
        }
    ),
    RoleCode.OPS_STAFF: frozenset(
        {
            # 注意：不含 COST_READ / FINANCE_READ —— 这是 F4 硬规则，不是疏漏
            Perm.PRODUCT_READ,
            Perm.PRODUCT_WRITE,
            Perm.ORDER_READ,
            Perm.ORDER_SHIP,
            Perm.INVENTORY_READ,
            Perm.INVENTORY_WRITE,
            Perm.ADS_READ,
            Perm.COMPLIANCE_READ,
            Perm.COMPLIANCE_WRITE,
            Perm.LANDED_COST_READ,
            Perm.LANDED_COST_CALC,
            Perm.DASHBOARD_READ,
        }
    ),
    RoleCode.PURCHASER: frozenset(
        {
            Perm.PRODUCT_READ,
            Perm.INVENTORY_READ,
            Perm.INVENTORY_WRITE,
            Perm.PURCHASE_READ,
            Perm.PURCHASE_WRITE,
            Perm.COST_READ,
            Perm.COMPLIANCE_READ,  # 👁
            Perm.LANDED_COST_READ,
            Perm.LANDED_COST_CALC,
            Perm.DASHBOARD_READ,  # 👁
        }
    ),
    RoleCode.FINANCE: frozenset(
        {
            Perm.PRODUCT_READ,  # 👁
            Perm.ORDER_READ,
            Perm.INVENTORY_READ,  # 👁
            Perm.PURCHASE_READ,  # 👁
            Perm.COST_READ,
            Perm.ADS_READ,  # 👁
            Perm.LANDED_COST_READ,  # 👁
            Perm.FINANCE_READ,
            Perm.REPORT_EXPORT,
            Perm.DASHBOARD_READ,
        }
    ),
    RoleCode.CS: frozenset(
        {
            Perm.PRODUCT_READ,  # 👁
            Perm.ORDER_READ,
            Perm.ORDER_SHIP,  # ⚠️ 部分受限（受数据范围约束）
        }
    ),
    RoleCode.VIEWER: frozenset(
        {
            Perm.SHOP_READ,
            Perm.PRODUCT_READ,
            Perm.ORDER_READ,
            Perm.INVENTORY_READ,
            Perm.ADS_READ,
            Perm.COMPLIANCE_READ,
            Perm.FINANCE_READ,
            Perm.DASHBOARD_READ,
        }
    ),
}

# 角色默认可见成本的角色集合（供字段级脱敏兜底使用）
COST_VISIBLE_ROLES: Final[frozenset[RoleCode]] = frozenset(
    {RoleCode.OWNER, RoleCode.ADMIN, RoleCode.OPS_MANAGER, RoleCode.PURCHASER, RoleCode.FINANCE}
)


def permissions_for_role(role_code: str) -> frozenset[Perm]:
    """未知角色返回空集（fail-closed）—— 宁可少给权限，不可多给。"""
    try:
        return ROLE_PERMISSIONS[RoleCode(role_code)]
    except (ValueError, KeyError):
        return frozenset()


def permission_codes_for_role(role_code: str) -> list[str]:
    """数据库种子数据用：返回排序后的权限点字符串列表。"""
    return sorted(p.value for p in permissions_for_role(role_code))


def role_has_permission(role_code: str, permission: str) -> bool:
    return permission in {p.value for p in permissions_for_role(role_code)}


def role_can_view_cost(role_code: str) -> bool:
    """F4：采购成本价与利润是否对该角色可见。"""
    try:
        return RoleCode(role_code) in COST_VISIBLE_ROLES
    except ValueError:
        return False


SYSTEM_ROLE_CODES: Final[frozenset[str]] = frozenset(r.value for r in RoleCode)
