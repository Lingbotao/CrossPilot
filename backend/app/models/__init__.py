"""SQLAlchemy 模型注册表。

⚠️ **新增模型文件必须在这里 import**，否则：
1. Alembic autogenerate 看不到它 → 迁移里缺表；
2. 分区/RLS 策略生成脚本漏掉它 → 隔离失效。

按 Alembic 迁移批次分组（PRD 6 章）：
    批次 1（M0）租户与权限域 —— 已完成
    批次 2（M1）平台与授权域
    批次 3（M2）订单域 —— sales_order / order_item / shop_sync_cursor 已落地；状态机与发货单仍在后续任务
    批次 4（M3）商品域 + 库存仓储域
    批次 5（M4）合规域 + 财务域
    批次 6（M5）采购域 + 广告消息域
"""

from __future__ import annotations

from app.db.base import Base
from app.models.audit import AuditLog, LoginLog
from app.models.enums import (
    AuditAction,
    DataScopeType,
    InvitationStatus,
    LoginResult,
    ResourceType,
    ShopStatus,
    SyncStatus,
    SyncTrigger,
    TenantPlan,
    TenantStatus,
    TenantUserStatus,
    UserStatus,
)
from app.models.order import OrderItem, SalesOrder, ShopSyncCursor
from app.models.platform import Platform, PlatformApiLog, Shop, ShopCredential, ShopGroup, SyncTask
from app.models.tenant import MemberInvitation, Role, SysUser, Tenant, TenantUser, UserDataScope

__all__ = [
    "Base",
    # 租户与权限域（批次 1）
    "Tenant",
    "SysUser",
    "TenantUser",
    "Role",
    "UserDataScope",
    "MemberInvitation",
    "AuditLog",
    "LoginLog",
    # 平台与授权域（批次 2）
    "Platform",
    "Shop",
    "ShopCredential",
    "ShopGroup",
    "SyncTask",
    "PlatformApiLog",
    # 订单域（批次 3）
    "SalesOrder",
    "OrderItem",
    "ShopSyncCursor",
    # 枚举
    "TenantPlan",
    "TenantStatus",
    "UserStatus",
    "TenantUserStatus",
    "InvitationStatus",
    "DataScopeType",
    "ResourceType",
    "LoginResult",
    "AuditAction",
    "ShopStatus",
    "SyncStatus",
    "SyncTrigger",
]

# 供脚本/测试使用：当前已落地的租户表清单
TENANT_SCOPED_TABLES: tuple[str, ...] = tuple(
    sorted(t.name for t in Base.metadata.tables.values() if "tenant_id" in t.c)
)
