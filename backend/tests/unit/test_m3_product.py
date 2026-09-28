"""商品主数据规则：变体上限、编码重复、重量必填、采购价对无成本权限角色不可读写。"""

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app.core.errors import AppError, ErrorCode, NotFoundError, ParamInvalidError, PermissionDeniedError
from app.core.pagination import encode_cursor
from app.db.base import SoftDeleteMixin, TenantMixin
from app.models.enums import AuditAction
from app.models.product import MAX_SKUS_PER_SPU, PRODUCT_STATUSES, Sku, Spu
from app.repositories.product import _is_sku_code_conflict, _like
from app.schemas.product import SkuPatch, SkuWrite, SpuCreate, SpuPatch
from app.services.product import (
    ProductService,
    assert_distinct_sku_codes,
    assert_variant_room,
    read_spu_cursor,
    resolve_cost_write,
    visible_cost,
)


def _sku(**overrides: object) -> SkuWrite:
    payload = {
        "sku_code": "TEE-RED-M",
        "weight_g": "180",
        "length_cm": "30",
        "width_cm": "20",
        "height_cm": "2",
    }
    payload.update(overrides)
    return SkuWrite.model_validate(payload)


def test_product_tables_are_tenant_master_data() -> None:
    assert issubclass(Spu, TenantMixin)
    assert issubclass(Spu, SoftDeleteMixin)
    assert issubclass(Sku, TenantMixin)
    assert issubclass(Sku, SoftDeleteMixin)
    assert MAX_SKUS_PER_SPU == 100
    assert "DRAFT" in PRODUCT_STATUSES
    index = next(item for item in Sku.__table__.indexes if item.name == "uq_sku_tenant_id_sku_code_active")
    assert index.unique is True
    assert "deleted_at" in str(index.dialect_options["postgresql"]["where"])


def test_variant_room_rejects_the_101st_sku() -> None:
    assert_variant_room(99, 1)
    assert_variant_room(0, 100)
    with pytest.raises(AppError) as captured:
        assert_variant_room(100, 1)
    assert captured.value.code == ErrorCode.SPU_VARIANT_LIMIT
    with pytest.raises(AppError) as batch:
        assert_variant_room(0, 101)
    assert batch.value.code == ErrorCode.SPU_VARIANT_LIMIT


def test_duplicate_sku_codes_in_one_request_are_rejected() -> None:
    assert_distinct_sku_codes(["A", "B"])
    with pytest.raises(AppError) as captured:
        assert_distinct_sku_codes(["A", "A"])
    assert captured.value.code == ErrorCode.SKU_CODE_DUPLICATED


def test_sku_requires_positive_measure_strings() -> None:
    sku = _sku()
    assert sku.weight_g == Decimal("180")
    with pytest.raises(ValidationError):
        _sku(weight_g=None)
    with pytest.raises(ValidationError):
        SkuWrite.model_validate({"sku_code": "A", "length_cm": "1", "width_cm": "1", "height_cm": "1"})
    with pytest.raises(ValidationError):
        _sku(weight_g="0")
    with pytest.raises(ValidationError):
        _sku(length_cm=1.5)


def test_cost_write_is_denied_without_cost_visibility() -> None:
    with pytest.raises(PermissionDeniedError) as captured:
        resolve_cost_write(
            can_view_cost=False,
            fields_set={"purchase_price", "currency"},
            current_price=Decimal("8"),
            current_currency="CNY",
            incoming_price=Decimal("9"),
            incoming_currency="CNY",
        )
    assert captured.value.code == ErrorCode.PERMISSION_DENIED
    kept_price, kept_currency = resolve_cost_write(
        can_view_cost=False,
        fields_set=set(),
        current_price=Decimal("8"),
        current_currency="CNY",
        incoming_price=None,
        incoming_currency=None,
    )
    assert kept_price == Decimal("8")
    assert kept_currency == "CNY"


def test_cost_pair_must_be_complete_when_visible() -> None:
    price, currency = resolve_cost_write(
        can_view_cost=True,
        fields_set={"purchase_price", "currency"},
        current_price=None,
        current_currency=None,
        incoming_price=Decimal("12.340000"),
        incoming_currency="USD",
    )
    assert price == Decimal("12.340000")
    assert currency == "USD"
    with pytest.raises(ParamInvalidError):
        resolve_cost_write(
            can_view_cost=True,
            fields_set={"purchase_price"},
            current_price=None,
            current_currency=None,
            incoming_price=Decimal("1"),
            incoming_currency=None,
        )


def test_visible_cost_strips_price_without_cost_visibility() -> None:
    assert visible_cost(Decimal("3"), "CNY", can_view_cost=False) == (None, None)
    assert visible_cost(Decimal("3"), "CNY", can_view_cost=True) == (Decimal("3"), "CNY")


def test_parsers_reject_non_decimal_text_and_bad_specs() -> None:
    base = {"sku_code": "A", "length_cm": "1", "width_cm": "1", "height_cm": "1"}
    with pytest.raises(ValidationError):
        SkuWrite.model_validate({**base, "weight_g": "abc"})
    with pytest.raises(ValidationError):
        SkuWrite.model_validate({**base, "weight_g": "-1"})
    with pytest.raises(ValidationError):
        SkuWrite.model_validate({**base, "weight_g": "1", "purchase_price": "-1", "currency": "CNY"})
    with pytest.raises(ValidationError):
        SkuWrite.model_validate({**base, "weight_g": "1", "barcode": 12})
    with pytest.raises(ValidationError):
        SkuWrite.model_validate({**base, "weight_g": "1", "spec_attrs": ["red"]})
    with pytest.raises(ValidationError):
        SkuWrite.model_validate({**base, "weight_g": "1", "spec_attrs": {str(i): "a" for i in range(21)}})
    with pytest.raises(ValidationError):
        SkuWrite.model_validate({**base, "weight_g": "1", "spec_attrs": {"color": 1}})
    with pytest.raises(ValidationError):
        SkuWrite.model_validate({**base, "weight_g": "1", "spec_attrs": {" ": "red"}})
    with pytest.raises(ValidationError):
        SkuWrite.model_validate({**base, "weight_g": "1", "currency": "US"})
    with pytest.raises(ValidationError):
        SkuWrite.model_validate({**base, "weight_g": "1", "currency": "12A"})
    with pytest.raises(ValidationError):
        SpuCreate.model_validate({"title": " ", "status": "DRAFT"})
    with pytest.raises(ValidationError):
        SpuCreate.model_validate({"title": "帽子", "status": "ARCHIVED"})
    with pytest.raises(ValidationError):
        SkuPatch.model_validate({"sku_code": "  "})
    with pytest.raises(ValidationError):
        SpuPatch.model_validate({"status": "NOPE"})

    sku = SkuWrite.model_validate(
        {
            **base,
            "weight_g": "1",
            "barcode": "  ",
            "currency": "usd",
            "purchase_price": "0",
            "spec_attrs": {" Color ": " Red "},
        }
    )
    assert sku.barcode is None
    assert sku.currency == "USD"
    assert sku.purchase_price == Decimal("0")
    assert sku.spec_attrs == {"Color": "Red"}
    assert SkuPatch.model_validate({"barcode": None, "spec_attrs": None, "weight_g": None}).weight_g is None


def test_cursor_and_sku_code_conflict_helpers() -> None:
    assert read_spu_cursor(None) is None
    assert read_spu_cursor(encode_cursor({"id": "42"})) == 42
    assert read_spu_cursor(encode_cursor({})) is None
    assert read_spu_cursor(encode_cursor({"id": "abc"})) is None
    assert _like("100%_a\\b") == "%100\\%\\_a\\\\b%"
    assert _is_sku_code_conflict(IntegrityError("INSERT", {}, Exception("uq_sku_tenant_id_sku_code_active")))
    assert _is_sku_code_conflict(IntegrityError("INSERT", {}, type("Orig", (), {"pgcode": "23505"})()))
    assert not _is_sku_code_conflict(IntegrityError("INSERT", {}, Exception("other")))
    with pytest.raises(ParamInvalidError):
        assert_variant_room(0, -1)


def _stamp_spu(obj: Spu, entity_id: int) -> Spu:
    now = datetime.now(UTC)
    obj.id = entity_id
    obj.created_at = now
    obj.updated_at = now
    return obj


def _stamp_sku(obj: Sku, entity_id: int) -> Sku:
    now = datetime.now(UTC)
    obj.id = entity_id
    obj.created_at = now
    obj.updated_at = now
    return obj


def _service() -> tuple[ProductService, list[Sku]]:
    session = MagicMock()
    session.flush = AsyncMock()
    service = ProductService(session)
    stored: list[Sku] = []
    saved: dict[int, Spu] = {}

    async def add_spu(obj: Spu) -> Spu:
        stamped = _stamp_spu(obj, 11)
        saved[stamped.id] = stamped
        return stamped

    async def add_sku(obj: Sku) -> Sku:
        stamped = _stamp_sku(obj, 20 + len(stored))
        stored.append(stamped)
        return stamped

    async def get_spu(spu_id: int) -> Spu:
        if spu_id not in saved:
            raise NotFoundError()
        return saved[spu_id]

    service.spus = MagicMock()
    service.skus = MagicMock()
    service.audit = MagicMock()
    service.spus.add = AsyncMock(side_effect=add_spu)
    service.spus.get_or_404 = AsyncMock(side_effect=get_spu)
    service.skus.add = AsyncMock(side_effect=add_sku)
    service.skus.code_taken = AsyncMock(return_value=False)
    service.skus.count_for_spu = AsyncMock(return_value=0)
    service.skus.list_for_spu = AsyncMock(side_effect=lambda spu_id: [row for row in stored if row.spu_id == spu_id])
    service.skus.counts_for = AsyncMock(return_value={})
    service.audit.append_action = AsyncMock()
    return service, stored


def _measures() -> dict[str, str]:
    return {"weight_g": "10", "length_cm": "2", "width_cm": "3", "height_cm": "4"}


async def test_create_and_read_hide_purchase_price_without_cost_visibility() -> None:
    service, stored = _service()
    created = await service.create_spu(
        SpuCreate.model_validate({"title": " 帽子 ", "skus": [{"sku_code": "HAT-1", **_measures()}]}),
        tenant_id=9,
        actor_id=3,
        can_view_cost=True,
    )
    assert created.title == "帽子"
    assert created.skus[0].purchase_price is None
    stored[0].purchase_price = Decimal("8.000000")
    stored[0].currency = "CNY"
    hidden = await service.get_spu(11, can_view_cost=False)
    assert hidden.skus[0].purchase_price is None
    assert hidden.skus[0].currency is None
    visible = await service.get_spu(11, can_view_cost=True)
    assert visible.skus[0].purchase_price == Decimal("8.000000")
    assert visible.skus[0].currency == "CNY"


async def test_create_rejects_cost_write_and_duplicate_code() -> None:
    service, _stored = _service()
    payload = SpuCreate.model_validate(
        {"title": "帽子", "skus": [{"sku_code": "HAT-1", "purchase_price": "1", "currency": "CNY", **_measures()}]}
    )
    with pytest.raises(PermissionDeniedError):
        await service.create_spu(payload, tenant_id=9, actor_id=3, can_view_cost=False)
    service.skus.code_taken = AsyncMock(return_value=True)
    plain = SpuCreate.model_validate({"title": "帽子", "skus": [{"sku_code": "HAT-1", **_measures()}]})
    with pytest.raises(AppError) as captured:
        await service.create_spu(plain, tenant_id=9, actor_id=3, can_view_cost=True)
    assert captured.value.code == ErrorCode.SKU_CODE_DUPLICATED


async def test_status_change_is_audited_and_variant_limit_blocks_the_next_sku() -> None:
    service, _stored = _service()
    now = datetime.now(UTC)
    spu = Spu(id=11, tenant_id=9, title="帽子", status="DRAFT", created_at=now, updated_at=now)
    service.spus.get_or_404 = AsyncMock(return_value=spu)
    updated = await service.update_spu(
        11,
        SpuPatch.model_validate({"title": "新帽子", "status": "ON_SALE", "brand": None}),
        tenant_id=9,
        actor_id=3,
        can_view_cost=False,
    )
    assert updated.status == "ON_SALE"
    assert updated.title == "新帽子"
    service.audit.append_action.assert_awaited()
    assert service.audit.append_action.await_args.kwargs["action"] == AuditAction.PRODUCT_STATUS
    with pytest.raises(ParamInvalidError):
        await service.update_spu(
            11, SpuPatch.model_validate({"title": None}), tenant_id=9, actor_id=3, can_view_cost=False
        )

    added = await service.add_sku(
        11,
        SkuWrite.model_validate({"sku_code": "HAT-2", "barcode": "690", "spec_attrs": {"spec": "红"}, **_measures()}),
        tenant_id=9,
        actor_id=3,
        can_view_cost=True,
    )
    assert added.sku_code == "HAT-2"
    assert added.barcode == "690"
    service.skus.count_for_spu = AsyncMock(return_value=100)
    with pytest.raises(AppError) as captured:
        await service.add_sku(
            11,
            SkuWrite.model_validate({"sku_code": "HAT-2", **_measures()}),
            tenant_id=9,
            actor_id=3,
            can_view_cost=True,
        )
    assert captured.value.code == ErrorCode.SPU_VARIANT_LIMIT


async def test_update_sku_keeps_cost_hidden_and_rejects_cleared_measures() -> None:
    service, _stored = _service()
    now = datetime.now(UTC)
    sku = Sku(
        id=21,
        tenant_id=9,
        spu_id=11,
        sku_code="HAT-1",
        spec_attrs={},
        weight_g=Decimal("10"),
        length_cm=Decimal("2"),
        width_cm=Decimal("3"),
        height_cm=Decimal("4"),
        purchase_price=Decimal("8"),
        currency="CNY",
        created_at=now,
        updated_at=now,
    )
    service.skus.get_or_404 = AsyncMock(return_value=sku)
    hidden = await service.update_sku(
        21,
        SkuPatch.model_validate({"sku_code": "HAT-1", "barcode": "690", "spec_attrs": {"spec": "红"}, "weight_g": "12"}),
        actor_id=3,
        can_view_cost=False,
    )
    assert sku.barcode == "690"
    assert sku.spec_attrs == {"spec": "红"}
    assert hidden.purchase_price is None
    assert sku.purchase_price == Decimal("8")
    assert sku.weight_g == Decimal("12")
    with pytest.raises(PermissionDeniedError):
        await service.update_sku(
            21,
            SkuPatch.model_validate({"purchase_price": "9", "currency": "CNY"}),
            actor_id=3,
            can_view_cost=False,
        )
    with pytest.raises(ParamInvalidError):
        await service.update_sku(21, SkuPatch.model_validate({"length_cm": None}), actor_id=3, can_view_cost=True)
    service.skus.code_taken = AsyncMock(return_value=True)
    with pytest.raises(AppError) as captured:
        await service.update_sku(21, SkuPatch.model_validate({"sku_code": "HAT-9"}), actor_id=3, can_view_cost=True)
    assert captured.value.code == ErrorCode.SKU_CODE_DUPLICATED
    with pytest.raises(ParamInvalidError):
        await service.update_sku(21, SkuPatch.model_validate({"sku_code": None}), actor_id=3, can_view_cost=True)


async def test_list_spus_uses_cursor_and_rejects_unknown_status() -> None:
    service, _stored = _service()
    now = datetime.now(UTC)
    rows = [
        Spu(id=index, tenant_id=9, title=f"商品{index}", status="DRAFT", created_at=now, updated_at=now)
        for index in (3, 2, 1)
    ]
    service.spus.list_cursor = AsyncMock(return_value=rows)
    service.skus.counts_for = AsyncMock(return_value={3: 1, 2: 0})
    page = await service.list_spus(limit=2, cursor=encode_cursor({"id": "9"}), status="DRAFT", title=" 帽 ")
    assert page.page_info.has_more is True
    assert [item.id for item in page.items] == [3, 2]
    assert page.items[0].sku_count == 1
    service.spus.get_or_404 = AsyncMock(side_effect=NotFoundError())
    with pytest.raises(NotFoundError):
        await service.get_spu(99, can_view_cost=False)
    with pytest.raises(ParamInvalidError):
        await service.list_spus(limit=20, cursor=None, status="ARCHIVED", title=None)
