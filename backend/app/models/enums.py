"""枚举常量表（PRD 9.1）。

PRD 规定「枚举用 SMALLINT + 代码内常量表」：比 VARCHAR 省空间、索引友好、
且避免"有人写了 'active' 有人写了 'ACTIVE'"这类脏数据。

⚠️ 枚举值一旦上线**只增不改**：改数值等于改写历史数据的语义。
"""

from __future__ import annotations

from enum import IntEnum


class TenantPlan(IntEnum):
    TRIAL = 1
    BASIC = 2
    PRO = 3
    ENTERPRISE = 4


class TenantStatus(IntEnum):
    ACTIVE = 1
    SUSPENDED = 2  # 欠费/违规暂停：可登录但只读
    DISABLED = 3  # 已停用：一律拒绝（错误码 20003）


class UserStatus(IntEnum):
    ACTIVE = 1
    DISABLED = 2


class TenantUserStatus(IntEnum):
    INVITED = 1  # 已邀请未接受
    ACTIVE = 2
    DISABLED = 3


class InvitationStatus(IntEnum):
    PENDING = 1
    ACCEPTED = 2
    EXPIRED = 3
    REVOKED = 4


class DataScopeType(IntEnum):
    """数据范围（PRD A-05）。

    ``ALL``      —— 全部店铺
    ``SELECTED`` —— 指定店铺（配合 ``shop_ids``）
    ``NONE``     —— 无数据权限（如财务只看报表不看订单）
    """

    ALL = 1
    SELECTED = 2
    NONE = 3


class ResourceType(IntEnum):
    """数据范围作用的资源类型。"""

    SHOP = 1
    WAREHOUSE = 2
    SUPPLIER = 3


class LoginResult(IntEnum):
    SUCCESS = 1
    FAILURE = 2


class AuditAction:
    """审计动作码（字符串，便于导出后人工阅读）。

    留痕范围（PRD A-07）：登录、授权、批量操作、删除、导出 —— 五类必须留。
    """

    LOGIN = "LOGIN"
    LOGOUT = "LOGOUT"
    LOGIN_FAILED = "LOGIN_FAILED"
    TENANT_REGISTER = "TENANT_REGISTER"
    MEMBER_INVITE = "MEMBER_INVITE"
    MEMBER_ROLE_CHANGE = "MEMBER_ROLE_CHANGE"
    MEMBER_REMOVE = "MEMBER_REMOVE"
    SHOP_GRANT = "SHOP_GRANT"
    SHOP_REVOKE = "SHOP_REVOKE"
    BATCH_SHIP = "BATCH_SHIP"
    BATCH_UPDATE = "BATCH_UPDATE"
    DATA_DELETE = "DATA_DELETE"
    DATA_EXPORT = "DATA_EXPORT"
    COST_UPDATE = "COST_UPDATE"
    ROLE_CHANGE = "ROLE_CHANGE"
    SYSTEM_CONFIG = "SYSTEM_CONFIG"

    ALL: tuple[str, ...] = (
        LOGIN,
        LOGOUT,
        LOGIN_FAILED,
        TENANT_REGISTER,
        MEMBER_INVITE,
        MEMBER_ROLE_CHANGE,
        MEMBER_REMOVE,
        SHOP_GRANT,
        SHOP_REVOKE,
        BATCH_SHIP,
        BATCH_UPDATE,
        DATA_DELETE,
        DATA_EXPORT,
        COST_UPDATE,
        ROLE_CHANGE,
        SYSTEM_CONFIG,
    )
