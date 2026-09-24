"""适配器层统一错误与「可否重试」判定。

为什么把「可重试性」单独抽出来：
    同步引擎的重试策略、退避曲线、死信队列投递，全都取决于这个判定。
    如果每个平台自己决定"这个错要不要重试"，重试逻辑就会散落各处 ——
    而重试逻辑写错的后果是**重复入库**或**永久丢单**，都属于 P0 事故。

判定规则（与 PRD 8.4 对齐）：
| 情况 | 判定 | 处理 |
|---|---|---|
| HTTP 429 / 平台限流码 | ``RETRY_AFTER`` | 按平台返回的 Retry-After 退避，不消耗重试次数 |
| HTTP 5xx / 超时 / 连接错误 | ``RETRY`` | 指数退避重试，超限进死信队列 |
| 401 / 令牌过期 | ``REFRESH_TOKEN`` | 先刷新令牌再重试一次，仍失败则标记店铺需重新授权 |
| 4xx 业务错误（参数非法等） | ``FAIL_FAST`` | 不重试，直接记失败原因（重试一万次也不会成功） |
| 网络不可达 / DNS 失败 | ``RETRY`` | 视为基础设施抖动 |
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class RetryDecision(StrEnum):
    """ "重试决策" —— 同步引擎据此路由任务。"""

    RETRY = "retry"
    RETRY_AFTER = "retry_after"
    REFRESH_TOKEN = "refresh_token"
    FAIL_FAST = "fail_fast"
    DEAD_LETTER = "dead_letter"


class AdapterError(Exception):
    """平台调用错误的统一表示。

    ``platform_code`` 保留平台原始错误码 —— 排障与提工单都靠它（PRD 17 章要求
    记录每次调用的 request_id / trace_id 便于向平台提工单）。
    """

    def __init__(
        self,
        message: str,
        *,
        platform: str = "",
        http_status: int | None = None,
        platform_code: str | None = None,
        decision: RetryDecision = RetryDecision.FAIL_FAST,
        retry_after_seconds: int | None = None,
        request_id: str | None = None,
        raw: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.platform = platform
        self.http_status = http_status
        self.platform_code = platform_code
        self.decision = decision
        self.retry_after_seconds = retry_after_seconds
        self.request_id = request_id
        self.raw = raw or {}

    @property
    def retryable(self) -> bool:
        return self.decision in {RetryDecision.RETRY, RetryDecision.RETRY_AFTER, RetryDecision.REFRESH_TOKEN}

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "http_status": self.http_status,
            "platform_code": self.platform_code,
            "decision": str(self.decision),
            "request_id": self.request_id,
            "message": str(self),
        }


def classify_platform_error(
    *,
    platform: str,
    http_status: int | None,
    platform_code: str | None = None,
    retry_after_seconds: int | None = None,
) -> RetryDecision:
    """把「HTTP 状态 + 平台业务码」翻译成重试决策。

    各平台可在自己的适配器里覆盖本函数（平台业务码千差万别），
    但**默认必须走这套判定** —— 否则就是"每个平台一套重试逻辑"。
    """
    if http_status == 429:
        return RetryDecision.RETRY_AFTER
    if http_status in (401, 403):
        return RetryDecision.REFRESH_TOKEN
    if http_status is not None and http_status >= 500:
        return RetryDecision.RETRY
    if http_status is None:
        # 连接层异常（超时/DNS/SSL）—— 视为可重试
        return RetryDecision.RETRY
    if retry_after_seconds is not None:
        return RetryDecision.RETRY_AFTER
    return RetryDecision.FAIL_FAST


__all__ = ["AdapterError", "RetryDecision", "classify_platform_error"]
