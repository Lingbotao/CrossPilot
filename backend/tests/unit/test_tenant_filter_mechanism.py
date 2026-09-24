"""★ 多租户隔离机制测试（第一道防线：ORM 自动注入）。

**这个文件不需要 PostgreSQL** —— 它验证的是 ORM 层机制本身，
用内存 SQLite + 一个最小租户表即可复现真实运行行为。

覆盖的行为（每一条对应一个真实事故场景）：

| 用例 | 防止的事故 |
|---|---|
| SELECT 自动追加租户条件 | 漏写 where → 列表页显示别的租户数据 |
| 无租户上下文时拒绝查询 | 定时任务/脚本忘了建上下文 → 全库扫描 |
| 批量 UPDATE 无租户条件被拒绝 | 一条 ``update(Sku).values(...)`` 改掉所有租户 |
| 带租户条件的批量 UPDATE 只影响本租户 | 条件写错 → 他租户数据被改 |
| INSERT 自动填充 tenant_id | 忘了赋值 → NOT NULL 报错或被塞进错租户 |

真正的「租户 A 查租户 B 的订单返回 404」端到端用例在
``tests/security/test_tenant_isolation.py``（需要 PostgreSQL）。
"""

from __future__ import annotations

import pytest
from sqlalchemy import String, select, update
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.core.context import tenant_context
from app.core.errors import UnauthenticatedError
from app.db.base import Base, PKMixin, TenantMixin
from app.db.tenant_filter import SKIP_FLAG, UnscopedTenantWriteError

TENANT_A = 1001
TENANT_B = 1002


class DemoTicket(Base, PKMixin, TenantMixin):
    """测试专用租户表：字段最少，能在 SQLite 上建表。"""

    __tablename__ = "demo_ticket"

    title: Mapped[str] = mapped_column(String(64), nullable=False)


@pytest.fixture
def seeded(sqlite_engine) -> None:
    """写入两个租户各一条数据。"""
    with Session(sqlite_engine) as session:
        with tenant_context(TENANT_A):
            session.add(DemoTicket(title="A-1"))
            session.commit()
        with tenant_context(TENANT_B):
            session.add(DemoTicket(title="B-1"))
            session.commit()


class TestSelectIsolation:
    def test_auto_injects_tenant_filter(self, sqlite_engine, seeded) -> None:
        with Session(sqlite_engine) as session, tenant_context(TENANT_A):
            rows = session.execute(select(DemoTicket)).scalars().all()
        assert [r.title for r in rows] == ["A-1"], "SELECT 未自动追加租户条件"

    def test_other_tenant_sees_only_its_own(self, sqlite_engine, seeded) -> None:
        with Session(sqlite_engine) as session, tenant_context(TENANT_B):
            rows = session.execute(select(DemoTicket)).scalars().all()
        assert [r.title for r in rows] == ["B-1"]

    def test_get_by_id_across_tenant_returns_none(self, sqlite_engine, seeded) -> None:
        """跨租户按 ID 查询必须拿不到 —— 这是「404 而非 403」的实现基础。"""
        with Session(sqlite_engine) as session, tenant_context(TENANT_A):
            target_id = session.execute(select(DemoTicket.id).where(DemoTicket.title == "B-1")).scalar_one_or_none()
        assert target_id is None, "租户 A 竟然能查到租户 B 的记录 ID"

    def test_missing_tenant_context_is_rejected(self, sqlite_engine, seeded) -> None:
        """fail-closed：没有租户上下文就不许查租户表。"""
        with Session(sqlite_engine) as session, pytest.raises(UnauthenticatedError):
            session.execute(select(DemoTicket)).scalars().all()

    def test_skip_flag_allows_cross_tenant(self, sqlite_engine, seeded) -> None:
        """逃生舱：系统级任务显式跳过过滤后能看到全部数据。"""
        with Session(sqlite_engine) as session:
            stmt = select(DemoTicket).execution_options(**{SKIP_FLAG: True})
            rows = session.execute(stmt).scalars().all()
        assert len(rows) == 2


class TestBulkWriteGuard:
    def test_unscoped_update_is_rejected(self, sqlite_engine, seeded) -> None:
        """★ 最危险的一种写法：没有 where 的 update 会改掉所有租户。"""
        with Session(sqlite_engine) as session, tenant_context(TENANT_A), pytest.raises(UnscopedTenantWriteError):
            session.execute(update(DemoTicket).values(title="HACKED"))

    def test_scoped_update_only_touches_own_tenant(self, sqlite_engine, seeded) -> None:
        with Session(sqlite_engine) as session, tenant_context(TENANT_A):
            session.execute(update(DemoTicket).where(DemoTicket.tenant_id == TENANT_A).values(title="A-updated"))
            session.commit()

        with Session(sqlite_engine) as session:
            stmt = select(DemoTicket.title).execution_options(**{SKIP_FLAG: True})
            all_titles = set(session.execute(stmt).scalars().all())
        assert all_titles == {"A-updated", "B-1"}, "跨租户批量写越界了"


class TestInsertAutofill:
    def test_tenant_id_filled_automatically(self, sqlite_engine) -> None:
        with Session(sqlite_engine) as session, tenant_context(TENANT_A):
            ticket = DemoTicket(title="auto")
            session.add(ticket)
            session.commit()
            assert ticket.tenant_id == TENANT_A

    def test_explicit_tenant_id_is_respected(self, sqlite_engine) -> None:
        with Session(sqlite_engine) as session, tenant_context(TENANT_A):
            ticket = DemoTicket(title="explicit", tenant_id=TENANT_A)
            session.add(ticket)
            session.commit()
            assert ticket.tenant_id == TENANT_A

    def test_no_tenant_context_does_not_silently_pick_one(self, sqlite_engine) -> None:
        """没有上下文时不能"猜"一个租户 —— 必须让 NOT NULL 约束把它拦下来。"""
        from sqlalchemy.exc import IntegrityError

        with Session(sqlite_engine) as session, pytest.raises(IntegrityError):
            session.add(DemoTicket(title="orphan"))
            session.commit()
