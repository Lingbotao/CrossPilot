"""HS 编码库：词典不是租户表，绑定按市场更新，检索语句走 trigram。"""

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

from app.core.errors import NotFoundError, ParamInvalidError
from app.db.base import SoftDeleteMixin, TenantMixin
from app.models import TENANT_SCOPED_TABLES
from app.models.enums import AuditAction
from app.models.hs_code import HS_NOMENCLATURE_SOURCE, HS_SEED, HsCode, SpuHsBinding
from app.models.product import Spu
from app.repositories.hs_code import search_statement
from app.schemas.hs_code import HsBindRequest
from app.services.hs_code import HsCodeService, recommend_text, resolve_search_text

_MIGRATION = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0017_hs_code.py"


def test_dictionary_is_global_and_binding_is_tenant_master_data() -> None:
    assert not issubclass(HsCode, TenantMixin)
    assert not issubclass(HsCode, SoftDeleteMixin)
    assert issubclass(SpuHsBinding, TenantMixin)
    assert not issubclass(SpuHsBinding, SoftDeleteMixin)
    assert "spu_hs_binding" in TENANT_SCOPED_TABLES
    assert "hs_code" not in TENANT_SCOPED_TABLES
    index = next(item for item in HsCode.__table__.indexes if item.name == "ix_hs_code_description_trgm")
    assert index.dialect_options["postgresql"]["using"] == "gin"
    assert index.dialect_options["postgresql"]["ops"] == {"description": "gin_trgm_ops"}
    assert AuditAction.HS_BIND in AuditAction.ALL


def test_seed_rows_cite_the_nomenclature_and_keep_real_headings() -> None:
    codes = [row.code for row in HS_SEED]
    assert len(codes) == len(set(codes))
    assert HS_NOMENCLATURE_SOURCE.startswith("WCO Harmonized System")
    shirt = next(row for row in HS_SEED if row.code == "610910")
    assert "T恤" in shirt.description
    assert shirt.level == 6
    assert shirt.chapter == "61"
    for row in HS_SEED:
        assert row.level == 6
        assert row.description.strip()
        assert row.chapter == row.code[:2]
    text = _MIGRATION.read_text(encoding="utf-8")
    assert "pg_trgm" in text
    assert "gin_trgm_ops" in text
    assert "HS_SEED" in text


def test_search_statement_ranks_with_trigram_similarity() -> None:
    compiled = str(search_statement("棉制T恤", limit=20).compile(dialect=postgresql.dialect()))
    lowered = compiled.lower()
    assert "similarity" in lowered
    assert "order by" in lowered
    assert "description %%" in compiled


def test_blank_basis_and_unknown_market_are_rejected() -> None:
    with pytest.raises(ValidationError):
        HsBindRequest.model_validate({"market": "US", "hs_code_id": "12", "basis": "   "})
    with pytest.raises(ValidationError):
        HsBindRequest.model_validate({"market": "US", "hs_code_id": "12", "basis": 1})
    with pytest.raises(ValidationError):
        HsBindRequest.model_validate({"market": "ZZ", "hs_code_id": "12", "basis": "棉质"})
    payload = HsBindRequest.model_validate({"market": "gb", "hs_code_id": "12", "basis": " 棉质针织 "})
    assert payload.market == "GB"
    assert payload.basis == "棉质针织"
    assert payload.hs_code_id == 12


def test_recommend_text_uses_material_and_purpose() -> None:
    assert recommend_text(" 帽子 ", None, "  ") == "帽子"
    assert recommend_text("T恤", "棉", "日常穿着") == "T恤 棉 日常穿着"
    assert resolve_search_text(" 610910 ", "别的") == "610910"
    assert resolve_search_text(" ", "棉 T恤") == "棉 T恤"
    with pytest.raises(ParamInvalidError):
        resolve_search_text("  ", None)


def _stamp(row: HsCode | SpuHsBinding, entity_id: int) -> None:
    now = datetime.now(UTC)
    row.id = entity_id
    row.created_at = now
    row.updated_at = now


def _hs(entity_id: int, code: str, description: str) -> HsCode:
    now = datetime.now(UTC)
    return HsCode(
        id=entity_id,
        code=code,
        description=description,
        parent_code=None,
        chapter=code[:2],
        level=6,
        source=HS_NOMENCLATURE_SOURCE,
        created_at=now,
        updated_at=now,
    )


def _service() -> HsCodeService:
    session = MagicMock()
    session.flush = AsyncMock()
    service = HsCodeService(session)
    service.codes = MagicMock()
    service.bindings = MagicMock()
    service.spus = MagicMock()
    service.audit = MagicMock()
    service.audit.append_action = AsyncMock()
    service.bindings.assert_tenant_owned = MagicMock()
    return service


def _spu() -> Spu:
    now = datetime.now(UTC)
    return Spu(
        id=11,
        tenant_id=9,
        title="棉质T恤",
        material="棉",
        purpose="日常穿着",
        status="DRAFT",
        created_at=now,
        updated_at=now,
    )


async def test_search_keeps_repository_rank_order() -> None:
    service = _service()
    shirt = _hs(2, "610910", "棉制针织或钩编的T恤衫、汗衫及其他背心")
    furniture = _hs(14, "940360", "其他木家具")
    service.codes.search = AsyncMock(return_value=[shirt, furniture])
    hits = await service.search(q="棉制T恤", spu_id=None, limit=20)
    assert [item.code for item in hits] == ["610910", "940360"]
    dumped = hits[0].model_dump()
    assert set(dumped) == {"id", "code", "description", "chapter", "level", "source"}


async def test_search_uses_product_text_when_query_is_blank() -> None:
    service = _service()
    service.spus.get_or_404 = AsyncMock(return_value=_spu())
    service.codes.search = AsyncMock(return_value=[_hs(2, "610910", "棉制针织或钩编的T恤衫、汗衫及其他背心")])
    hits = await service.search(q="  ", spu_id=11, limit=5)
    assert hits[0].code == "610910"
    text = service.codes.search.await_args.args[0]
    assert "棉质T恤" in text
    assert "棉" in text
    assert "日常穿着" in text


async def test_search_rejects_an_overlong_query_and_lists_market_bindings() -> None:
    service = _service()
    with pytest.raises(ParamInvalidError):
        await service.search(q="棉" * 129, spu_id=None, limit=5)

    now = datetime.now(UTC)
    hs = _hs(2, "610910", "棉制针织或钩编的T恤衫、汗衫及其他背心")
    binding = SpuHsBinding(
        id=5,
        tenant_id=9,
        spu_id=11,
        market="US",
        hs_code_id=2,
        basis="棉质针织",
        created_at=now,
        updated_at=now,
        updated_by=7,
    )
    service.spus.get_or_404 = AsyncMock(return_value=_spu())
    service.bindings.list_for_spu = AsyncMock(return_value=[binding])
    service.codes.get_or_404 = AsyncMock(return_value=hs)
    views = await service.list_bindings(11)
    assert len(views) == 1
    assert views[0].code == "610910"
    assert views[0].basis == "棉质针织"
    assert views[0].model_dump()["updated_by"] == "7"


async def test_foreign_spu_and_unknown_code_are_not_found() -> None:
    service = _service()
    service.spus.get_or_404 = AsyncMock(side_effect=NotFoundError())
    with pytest.raises(NotFoundError):
        await service.search(q="棉", spu_id=99, limit=10)
    with pytest.raises(NotFoundError):
        await service.list_bindings(99)

    service.spus.get_or_404 = AsyncMock(return_value=_spu())
    service.codes.get = AsyncMock(return_value=None)
    payload = HsBindRequest.model_validate({"market": "US", "hs_code_id": "404", "basis": "棉质"})
    with pytest.raises(NotFoundError):
        await service.bind(11, payload, tenant_id=9, actor_id=7)


async def test_rebind_same_market_updates_row_and_audits_the_change() -> None:
    service = _service()
    now = datetime.now(UTC)
    old = _hs(1, "940360", "其他木家具")
    new = _hs(2, "610910", "棉制针织或钩编的T恤衫、汗衫及其他背心")
    binding = SpuHsBinding(
        id=5,
        tenant_id=9,
        spu_id=11,
        market="US",
        hs_code_id=1,
        basis="先按家具",
        created_at=now,
        updated_at=now,
        updated_by=3,
    )
    service.spus.get_or_404 = AsyncMock(return_value=_spu())
    service.codes.get = AsyncMock(side_effect=lambda code_id: {1: old, 2: new}.get(code_id))
    service.bindings.get_for_market = AsyncMock(return_value=binding)
    payload = HsBindRequest.model_validate({"market": "us", "hs_code_id": "2", "basis": " 棉质针织T恤 "})
    view = await service.bind(11, payload, tenant_id=9, actor_id=7)
    assert binding.hs_code_id == 2
    assert binding.basis == "棉质针织T恤"
    assert binding.updated_by == 7
    assert view.code == "610910"
    assert view.market == "US"
    kwargs = service.audit.append_action.await_args.kwargs
    assert kwargs["action"] == AuditAction.HS_BIND
    assert kwargs["resource"] == "spu"
    assert kwargs["resource_id"] == 11
    assert kwargs["before"] == {
        "market": "US",
        "hs_code_id": "1",
        "hs_code": "940360",
        "basis": "先按家具",
    }
    assert kwargs["after"]["hs_code"] == "610910"
    assert kwargs["after"]["basis"] == "棉质针织T恤"
    service.bindings.add.assert_not_called()


async def test_first_bind_inserts_and_audits_without_before() -> None:
    service = _service()
    hs = _hs(2, "851713", "智能手机")

    async def add(row: SpuHsBinding) -> SpuHsBinding:
        _stamp(row, 8)
        return row

    service.spus.get_or_404 = AsyncMock(return_value=_spu())
    service.codes.get = AsyncMock(return_value=hs)
    service.bindings.get_for_market = AsyncMock(return_value=None)
    service.bindings.add = AsyncMock(side_effect=add)
    payload = HsBindRequest.model_validate({"market": "SG", "hs_code_id": "2", "basis": "电子产品"})
    view = await service.bind(11, payload, tenant_id=9, actor_id=7)
    assert view.model_dump()["id"] == "8"
    assert view.code == "851713"
    assert view.basis == "电子产品"
    kwargs = service.audit.append_action.await_args.kwargs
    assert kwargs["before"] is None
    assert kwargs["after"]["market"] == "SG"
    assert kwargs["after"]["hs_code"] == "851713"
