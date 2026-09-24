"""错误码与统一响应信封。"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from app.core.errors import (
    AppError,
    CrossTenantError,
    ErrorCode,
    NotFoundError,
    ParamInvalidError,
)
from app.core.response import ApiResponse, fail, is_success, ok


class TestErrorCodeSegments:
    """错误码段位必须落在 PRD 10.1 规定的区间内 —— 防止随手编一个 9xxx。"""

    @pytest.mark.parametrize(
        ("code", "segment"),
        [
            (ErrorCode.PARAM_INVALID, 10),
            (ErrorCode.UNAUTHENTICATED, 20),
            (ErrorCode.PLATFORM_RATE_LIMITED, 30),
            (ErrorCode.SKU_CODE_DUPLICATED, 40),
            (ErrorCode.ORDER_NOT_FOUND, 50),
            (ErrorCode.INVENTORY_INSUFFICIENT, 60),
            (ErrorCode.EXCHANGE_RATE_MISSING, 70),
            (ErrorCode.HS_CODE_MISSING, 80),
        ],
    )
    def test_code_in_expected_segment(self, code: ErrorCode, segment: int) -> None:
        assert int(code) // 1000 == segment

    def test_all_codes_are_five_digits(self) -> None:
        for code in ErrorCode:
            if code is ErrorCode.OK:
                continue
            assert 10000 <= int(code) <= 89999, f"{code.name}={int(code)} 不在 5 位业务码区间"


class TestErrorSemantics:
    def test_cross_tenant_is_404_not_403(self) -> None:
        """★ 跨租户访问必须表现为 404 —— 403 等于确认「该资源存在，只是不属于你」。"""
        assert CrossTenantError().status == 404
        assert int(CrossTenantError().code) == int(ErrorCode.CROSS_TENANT_DENIED)

    def test_not_found_status(self) -> None:
        assert NotFoundError().status == 404

    def test_param_invalid_status(self) -> None:
        assert ParamInvalidError().status == 422

    def test_custom_message_overrides_default(self) -> None:
        err = NotFoundError("订单不存在")
        assert err.message == "订单不存在"
        assert int(err.code) == int(ErrorCode.RESOURCE_NOT_FOUND)

    def test_custom_code_override(self) -> None:
        err = AppError("x", code=ErrorCode.ORDER_NOT_FOUND)
        assert int(err.code) == int(ErrorCode.ORDER_NOT_FOUND)

    def test_data_payload_is_carried(self) -> None:
        err = AppError("授权过期", code=ErrorCode.SHOP_GRANT_EXPIRED, data={"shop_id": 10086})
        assert err.to_dict()["data"] == {"shop_id": 10086}
        assert err.status == 401


class TestResponseEnvelope:
    def test_success_envelope_shape(self) -> None:
        resp = ok({"id": 1})
        payload = resp.model_dump(mode="json")
        assert payload["code"] == 0
        assert payload["message"] == "success"
        assert payload["data"] == {"id": 1}
        assert payload["timestamp"]

    def test_failure_envelope_shape(self) -> None:
        payload = fail(int(ErrorCode.ORDER_NOT_FOUND), "订单不存在", {"order_id": 9}).model_dump(mode="json")
        assert payload["code"] == 50001
        assert payload["data"] == {"order_id": 9}
        assert is_success(payload) is False

    def test_trace_id_is_attached_from_context(self) -> None:
        from app.core.context import set_trace_id

        set_trace_id("01J8X2K3M4N5P6Q7R8S9T0")
        assert ok().trace_id == "01J8X2K3M4N5P6Q7R8S9T0"

    def test_generic_envelope_typing(self) -> None:
        class Item(BaseModel):
            name: str

        resp: ApiResponse[Item] = ok(Item(name="x"))
        assert resp.data is not None
        assert resp.data.name == "x"
