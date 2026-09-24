"""开发种子数据脚本。

    make seed            # 等价于 cd backend && .venv/bin/python -m scripts.seed_dev

写入内容：
- 1 个演示租户（``demo``）
- 1 个所有者账号（``owner@example.com`` / 密码见下方常量）
- 8 个内置角色（权限点由 ``app.core.permissions`` 生成，**不手写**）
- 该所有者的数据范围记录（全部店铺）

⚠️ 幂等：重复执行不会产生重复数据。
⚠️ 仅用于开发环境 —— 脚本会拒绝在 ``APP_ENV=prod`` 下运行。
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.context import set_tenant_id, set_user_id
from app.core.logging import configure_logging, get_logger
from app.core.permissions import ROLE_DESCRIPTIONS_ZH, ROLE_NAMES_ZH, RoleCode, permission_codes_for_role
from app.core.security import hash_password
from app.db.base import Base
from app.models.enums import (
    DataScopeType,
    ResourceType,
    TenantPlan,
    TenantStatus,
    TenantUserStatus,
    UserStatus,
)
from app.models.tenant import Role, SysUser, Tenant, TenantUser, UserDataScope

log = get_logger(__name__)

DEMO_TENANT_CODE = "demo"
DEMO_TENANT_NAME = "演示租户（CrossPilot）"
DEMO_OWNER_EMAIL = "owner@example.com"
DEMO_OWNER_PASSWORD = "Demo@CrossPilot2026"  # noqa: S105 - 开发种子密码，生产不会执行本脚本
DEMO_USER_EMAIL = "ops@example.com"


async def _get_or_create_tenant(session: AsyncSession) -> Tenant:
    existing = (await session.execute(select(Tenant).where(Tenant.code == DEMO_TENANT_CODE))).scalar_one_or_none()
    if existing is not None:
        log.info("tenant_exists", tenant_id=existing.id, code=DEMO_TENANT_CODE)
        return existing

    tenant = Tenant(
        code=DEMO_TENANT_CODE,
        name=DEMO_TENANT_NAME,
        plan=int(TenantPlan.PRO),
        status=int(TenantStatus.ACTIVE),
        default_currency="CNY",
        timezone="Asia/Shanghai",
    )
    session.add(tenant)
    await session.flush()
    log.info("tenant_created", tenant_id=tenant.id)
    return tenant


async def _get_or_create_user(session: AsyncSession, email: str, display_name: str) -> SysUser:
    existing = (await session.execute(select(SysUser).where(SysUser.email == email))).scalar_one_or_none()
    if existing is not None:
        return existing
    user = SysUser(
        email=email,
        display_name=display_name,
        password_hash=hash_password(DEMO_OWNER_PASSWORD),
        status=int(UserStatus.ACTIVE),
    )
    session.add(user)
    await session.flush()
    log.info("user_created", user_id=user.id, email=email)
    return user


async def _seed_roles(session: AsyncSession, tenant_id: int) -> None:
    """写入 8 个内置角色。权限点来自代码常量 —— 保证与后端校验口径完全一致。"""
    existing_codes = set((await session.execute(select(Role.code).where(Role.tenant_id == tenant_id))).scalars().all())
    for role_code in RoleCode:
        if role_code.value in existing_codes:
            continue
        session.add(
            Role(
                tenant_id=tenant_id,
                code=role_code.value,
                name=ROLE_NAMES_ZH[role_code],
                is_system=True,
                permission_codes=permission_codes_for_role(role_code.value),
                description=ROLE_DESCRIPTIONS_ZH[role_code],
            )
        )
    await session.flush()
    log.info("roles_seeded", tenant_id=tenant_id, count=len(RoleCode))


async def _get_or_create_membership(
    session: AsyncSession, tenant_id: int, user_id: int, role_code: RoleCode, nickname: str
) -> TenantUser:
    existing = (await session.execute(select(TenantUser).where(TenantUser.user_id == user_id))).scalar_one_or_none()
    if existing is not None:
        return existing

    from datetime import UTC, datetime

    membership = TenantUser(
        tenant_id=tenant_id,
        user_id=user_id,
        role_code=role_code.value,
        status=int(TenantUserStatus.ACTIVE),
        nickname=nickname,
        joined_at=datetime.now(UTC),
    )
    session.add(membership)
    await session.flush()
    return membership


async def _seed_data_scope(session: AsyncSession, tenant_id: int, membership: TenantUser) -> None:
    existing = (
        await session.execute(select(UserDataScope).where(UserDataScope.tenant_user_id == membership.id))
    ).scalar_one_or_none()
    if existing is not None:
        return
    session.add(
        UserDataScope(
            tenant_id=tenant_id,
            tenant_user_id=membership.id,
            resource_type=int(ResourceType.SHOP),
            scope_type=int(DataScopeType.ALL),
            shop_ids=[],
        )
    )
    await session.flush()


async def seed() -> None:
    if settings.is_prod:
        log.error("seed_refused_in_production")
        sys.exit(1)

    from app.db.session import owner_session_scope

    # 必须用 owner 会话（表 owner，绕过 RLS），不能用运行时的 app 角色：
    # tenant 表的 INSERT 策略允许"注册期无租户上下文插入"，但 ORM flush 会带
    # RETURNING，而 INSERT ... RETURNING 还要过 SELECT 策略 —— 无上下文时必然失败。
    # 详见 app/db/session.py::owner_session_scope 的说明。
    async with owner_session_scope() as session:
        # 第 1 步：建租户。此刻还没有租户上下文。
        tenant = await _get_or_create_tenant(session)

        # 第 2 步：立刻建立租户上下文并绑定 RLS —— 之后所有租户表写入才能通过。
        set_tenant_id(tenant.id)
        await session.execute(text("SELECT set_config('app.current_tenant', :tid, true)"), {"tid": str(tenant.id)})

        owner = await _get_or_create_user(session, DEMO_OWNER_EMAIL, "演示所有者")
        ops = await _get_or_create_user(session, DEMO_USER_EMAIL, "演示运营专员")

        await _seed_roles(session, tenant.id)

        set_user_id(owner.id)
        owner_membership = await _get_or_create_membership(session, tenant.id, owner.id, RoleCode.OWNER, "老板")
        ops_membership = await _get_or_create_membership(session, tenant.id, ops.id, RoleCode.OPS_STAFF, "运营一号")
        await _seed_data_scope(session, tenant.id, owner_membership)
        await _seed_data_scope(session, tenant.id, ops_membership)

    log.info(
        "seed_done",
        tenant_code=DEMO_TENANT_CODE,
        owner=DEMO_OWNER_EMAIL,
        ops=DEMO_USER_EMAIL,
        note="密码见 scripts/seed_dev.py 常量；两个账号用于验证 F4 成本可见性差异",
    )


def _assert_schema_ready() -> None:
    """提前给出可读的报错，而不是让用户对着一堆 UndefinedTable 猜。"""
    log.info("checking_schema", hint="若报 relation 不存在，请先执行 `make migrate`")
    _ = Base.metadata.tables.keys()


def main() -> None:
    configure_logging(force=True)
    _assert_schema_ready()
    asyncio.run(seed())
    print("\n✅ 种子数据写入完成")
    print(f"   租户代码: {DEMO_TENANT_CODE}")
    print(f"   所有者  : {DEMO_OWNER_EMAIL} / {DEMO_OWNER_PASSWORD}")
    print(f"   运营专员: {DEMO_USER_EMAIL} / {DEMO_OWNER_PASSWORD}  ← 用于验证「成本不可见」")


if __name__ == "__main__":
    main()
