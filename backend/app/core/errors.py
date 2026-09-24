"""统一错误码与异常体系（PRD 10.1）。

错误码规范：5 位数字，前 2 位为模块，后 3 位为具体错误。

| 码段  | 模块        | 示例                                              |
|-------|-------------|---------------------------------------------------|
| 10xxx | 通用        | 10001 参数校验失败，10002 资源不存在              |
| 20xxx | 认证与租户  | 20001 未登录，20002 权限不足，20003 租户已停用    |
| 30xxx | 平台与同步  | 30001 平台不支持，30002 授权失败，30003 平台限流  |
| 40xxx | 商品        | 40001 SKU 编码重复，40002 合规校验未通过          |
| 50xxx | 订单        | 50001 订单不存在，50002 状态不允许该操作          |
| 60xxx | 库存        | 60001 库存不足，60002 库存同步失败                |
| 70xxx | 财务        | 70001 汇率缺失，70002 对账不平                    |
| 80xxx | 合规        | 80001 缺失 HS 编码，80002 认证已过期              |
"""

from __future__ import annotations

from enum import IntEnum
from typing import Any


class ErrorCode(IntEnum):
    """全量错误码。新增错误必须先在此登记，禁止散落魔法数字。"""

    OK = 0

    # ---- 10xxx 通用 ----
    PARAM_INVALID = 10001
    RESOURCE_NOT_FOUND = 10002
    OPERATION_CONFLICT = 10003
    TOO_MANY_REQUESTS = 10004
    IDEMPOTENCY_CONFLICT = 10005
    INTERNAL_ERROR = 10099

    # ---- 20xxx 认证与租户 ----
    UNAUTHENTICATED = 20001
    PERMISSION_DENIED = 20002
    TENANT_DISABLED = 20003
    ACCOUNT_LOCKED = 20004
    CROSS_TENANT_DENIED = 20005
    TOKEN_EXPIRED = 20006

    # ---- 30xxx 平台与同步 ----
    PLATFORM_UNSUPPORTED = 30001
    GRANT_FAILED = 30002
    PLATFORM_RATE_LIMITED = 30003
    PLATFORM_API_ERROR = 30004
    PLATFORM_CREDENTIAL_EXPIRED = 30005
    SYNC_TASK_FAILED = 30006

    # ---- 40xxx 商品 ----
    SKU_CODE_DUPLICATED = 40001
    COMPLIANCE_CHECK_FAILED = 40002
    LISTING_STATE_INVALID = 40003
    SHOP_GRANT_EXPIRED = 40201  # PRD 10.2 前端约定：40201 引导重新授权

    # ---- 50xxx 订单 ----
    ORDER_NOT_FOUND = 50001
    ORDER_STATE_INVALID = 50002
    BATCH_SHIP_PARTIAL_FAILED = 50003

    # ---- 60xxx 库存 ----
    INVENTORY_INSUFFICIENT = 60001
    INVENTORY_SYNC_FAILED = 60002
    INVENTORY_CONFLICT = 60003

    # ---- 70xxx 财务 ----
    EXCHANGE_RATE_MISSING = 70001
    SETTLEMENT_MISMATCH = 70002
    LANDED_COST_PARAM_MISSING = 70003

    # ---- 80xxx 合规 ----
    HS_CODE_MISSING = 80001
    CERTIFICATE_EXPIRED = 80002
    MARKET_RESTRICTED = 80003


# 默认 HTTP 状态码映射：业务码 → HTTP 码
_DEFAULT_HTTP_STATUS: dict[int, int] = {
    ErrorCode.OK: 200,
    ErrorCode.PARAM_INVALID: 422,
    ErrorCode.RESOURCE_NOT_FOUND: 404,
    ErrorCode.OPERATION_CONFLICT: 409,
    ErrorCode.TOO_MANY_REQUESTS: 429,
    ErrorCode.IDEMPOTENCY_CONFLICT: 409,
    ErrorCode.INTERNAL_ERROR: 500,
    ErrorCode.UNAUTHENTICATED: 401,
    ErrorCode.PERMISSION_DENIED: 403,
    ErrorCode.TENANT_DISABLED: 403,
    ErrorCode.ACCOUNT_LOCKED: 423,
    # 跨租户一律按「不存在」处理，避免通过状态码探测他租户资源是否存在（PRD 13.2 第 4 条）
    ErrorCode.CROSS_TENANT_DENIED: 404,
    ErrorCode.TOKEN_EXPIRED: 401,
    ErrorCode.PLATFORM_UNSUPPORTED: 400,
    ErrorCode.GRANT_FAILED: 400,
    ErrorCode.PLATFORM_RATE_LIMITED: 429,
    ErrorCode.PLATFORM_API_ERROR: 502,
    ErrorCode.PLATFORM_CREDENTIAL_EXPIRED: 401,
    ErrorCode.SYNC_TASK_FAILED: 500,
    ErrorCode.SKU_CODE_DUPLICATED: 409,
    ErrorCode.COMPLIANCE_CHECK_FAILED: 400,
    ErrorCode.LISTING_STATE_INVALID: 409,
    ErrorCode.SHOP_GRANT_EXPIRED: 401,
    ErrorCode.ORDER_NOT_FOUND: 404,
    ErrorCode.ORDER_STATE_INVALID: 409,
    ErrorCode.BATCH_SHIP_PARTIAL_FAILED: 207,
    ErrorCode.INVENTORY_INSUFFICIENT: 409,
    ErrorCode.INVENTORY_SYNC_FAILED: 502,
    ErrorCode.INVENTORY_CONFLICT: 409,
    ErrorCode.EXCHANGE_RATE_MISSING: 422,
    ErrorCode.SETTLEMENT_MISMATCH: 409,
    ErrorCode.LANDED_COST_PARAM_MISSING: 422,
    ErrorCode.HS_CODE_MISSING: 422,
    ErrorCode.CERTIFICATE_EXPIRED: 409,
    ErrorCode.MARKET_RESTRICTED: 403,
}


class AppError(Exception):
    """业务异常基类。

    约定：**可预期的业务失败一律抛 AppError**，不要返回错误 dict，
    也不要用 HTTPException（那会绕过统一响应封装）。
    不可预期异常由全局 handler 兜底为 10099，且不向前端泄露堆栈。
    """

    code: ErrorCode = ErrorCode.INTERNAL_ERROR
    message: str = "服务内部错误"
    http_status: int | None = None

    def __init__(
        self,
        message: str | None = None,
        *,
        code: ErrorCode | None = None,
        data: dict[str, Any] | None = None,
        http_status: int | None = None,
    ) -> None:
        if code is not None:
            self.code = code
        if message is not None:
            self.message = message
        if http_status is not None:
            self.http_status = http_status
        self.data = data or {}
        super().__init__(self.message)

    @property
    def status(self) -> int:
        if self.http_status is not None:
            return self.http_status
        return _DEFAULT_HTTP_STATUS.get(int(self.code), 400)

    def to_dict(self) -> dict[str, Any]:
        return {"code": int(self.code), "message": self.message, "data": self.data}


class ParamInvalidError(AppError):
    code = ErrorCode.PARAM_INVALID
    message = "参数校验失败"


class NotFoundError(AppError):
    code = ErrorCode.RESOURCE_NOT_FOUND
    message = "资源不存在"


class ConflictError(AppError):
    code = ErrorCode.OPERATION_CONFLICT
    message = "操作冲突"


class UnauthenticatedError(AppError):
    code = ErrorCode.UNAUTHENTICATED
    message = "未登录或令牌无效"


class TokenExpiredError(AppError):
    code = ErrorCode.TOKEN_EXPIRED
    message = "令牌已过期"


class PermissionDeniedError(AppError):
    code = ErrorCode.PERMISSION_DENIED
    message = "权限不足"


class TenantDisabledError(AppError):
    code = ErrorCode.TENANT_DISABLED
    message = "租户已停用"


class AccountLockedError(AppError):
    code = ErrorCode.ACCOUNT_LOCKED
    message = "账号已被锁定，请稍后再试"


class CrossTenantError(AppError):
    """跨租户访问。对外一律表现为 404 —— 不暴露他租户资源是否存在。"""

    code = ErrorCode.CROSS_TENANT_DENIED
    message = "资源不存在"


class PlatformUnsupportedError(AppError):
    code = ErrorCode.PLATFORM_UNSUPPORTED
    message = "平台不支持"


class ComplianceError(AppError):
    code = ErrorCode.COMPLIANCE_CHECK_FAILED
    message = "合规校验未通过"


class InventoryInsufficientError(AppError):
    code = ErrorCode.INVENTORY_INSUFFICIENT
    message = "库存不足"


class ExchangeRateMissingError(AppError):
    code = ErrorCode.EXCHANGE_RATE_MISSING
    message = "汇率缺失"


class HSCodeMissingError(AppError):
    code = ErrorCode.HS_CODE_MISSING
    message = "缺失 HS 编码"


__all__ = [
    "AccountLockedError",
    "AppError",
    "ComplianceError",
    "ConflictError",
    "CrossTenantError",
    "ErrorCode",
    "ExchangeRateMissingError",
    "HSCodeMissingError",
    "InventoryInsufficientError",
    "NotFoundError",
    "ParamInvalidError",
    "PermissionDeniedError",
    "PlatformUnsupportedError",
    "TenantDisabledError",
    "TokenExpiredError",
    "UnauthenticatedError",
]
