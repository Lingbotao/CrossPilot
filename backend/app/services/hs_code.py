"""HS 检索与按市场绑定。检索结果不带税率。"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ParamInvalidError
from app.models.enums import AuditAction
from app.models.hs_code import HsCode, SpuHsBinding
from app.repositories.hs_code import HsCodeRepository, SpuHsBindingRepository
from app.repositories.identity import AuditLogRepository
from app.repositories.product import SpuRepository
from app.schemas.hs_code import HsBindRequest, HsCodeHit, SpuHsBindingView

_QUERY_LIMIT = 128


def recommend_text(title: str, material: str | None, purpose: str | None) -> str:
    """用商品标题、材质、用途拼推荐检索词。空字段不参与。"""

    parts = [title.strip()]
    for extra in (material, purpose):
        if extra and extra.strip():
            parts.append(extra.strip())
    return " ".join(part for part in parts if part)


def resolve_search_text(q: str | None, recommend: str | None) -> str:
    typed = (q or "").strip()
    if typed:
        return typed
    hinted = (recommend or "").strip()
    if hinted:
        return hinted
    raise ParamInvalidError("请输入检索词，或选择商品以推荐编码")


def _snapshot(market: str, hs: HsCode, basis: str) -> dict[str, str]:
    return {
        "market": market,
        "hs_code_id": str(hs.id),
        "hs_code": hs.code,
        "basis": basis,
    }


class HsCodeService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.codes = HsCodeRepository(session)
        self.bindings = SpuHsBindingRepository(session)
        self.spus = SpuRepository(session)
        self.audit = AuditLogRepository(session)

    async def search(self, *, q: str | None, spu_id: int | None, limit: int) -> list[HsCodeHit]:
        recommend: str | None = None
        if spu_id is not None:
            spu = await self.spus.get_or_404(spu_id)
            recommend = recommend_text(spu.title, spu.material, spu.purpose)
        text = resolve_search_text(q, recommend)
        if len(text) > _QUERY_LIMIT:
            raise ParamInvalidError("检索词过长")
        rows = await self.codes.search(text, limit=limit)
        return [HsCodeHit.model_validate(row) for row in rows]

    async def list_bindings(self, spu_id: int) -> list[SpuHsBindingView]:
        await self.spus.get_or_404(spu_id)
        rows = await self.bindings.list_for_spu(spu_id)
        views: list[SpuHsBindingView] = []
        for row in rows:
            hs = await self.codes.get_or_404(row.hs_code_id)
            views.append(_view(row, hs))
        return views

    async def bind(
        self,
        spu_id: int,
        payload: HsBindRequest,
        *,
        tenant_id: int,
        actor_id: int,
    ) -> SpuHsBindingView:
        basis = payload.basis.strip()
        if not basis:
            raise ParamInvalidError("绑定依据必须填写")
        spu = await self.spus.get_or_404(spu_id)
        hs = await self.codes.get(payload.hs_code_id)
        if hs is None:
            raise NotFoundError()
        existing = await self.bindings.get_for_market(spu.id, payload.market)
        before: dict[str, str] | None = None
        if existing is None:
            row = SpuHsBinding(
                spu_id=spu.id,
                market=payload.market,
                hs_code_id=hs.id,
                basis=basis,
                created_by=actor_id,
                updated_by=actor_id,
            )
            await self.bindings.add(row)
        else:
            old = await self.codes.get(existing.hs_code_id)
            if old is None:
                raise NotFoundError()
            before = _snapshot(existing.market, old, existing.basis)
            existing.hs_code_id = hs.id
            existing.basis = basis
            existing.updated_by = actor_id
            self.bindings.assert_tenant_owned(existing)
            await self.session.flush()
            row = existing
        await self.audit.append_action(
            tenant_id=tenant_id,
            user_id=actor_id,
            action=AuditAction.HS_BIND,
            resource="spu",
            resource_id=spu.id,
            before=before,
            after=_snapshot(payload.market, hs, basis),
        )
        return _view(row, hs)


def _view(row: SpuHsBinding, hs: HsCode) -> SpuHsBindingView:
    return SpuHsBindingView(
        id=row.id,
        spu_id=row.spu_id,
        market=row.market,
        hs_code_id=hs.id,
        code=hs.code,
        description=hs.description,
        source=hs.source,
        basis=row.basis,
        updated_by=row.updated_by,
        updated_at=row.updated_at,
    )
