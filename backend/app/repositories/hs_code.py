"""HS 词典检索与商品绑定。词典无租户列；绑定表走租户过滤。"""

from __future__ import annotations

from sqlalchemy import case, func, literal, or_, select
from sqlalchemy.sql import Select

from app.models.hs_code import HsCode, SpuHsBinding
from app.repositories.base import BaseRepository


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def search_statement(q: str, *, limit: int) -> Select[tuple[HsCode]]:
    """编码前缀命中排在前面，描述按 pg_trgm 相似度排序。

    ``%`` 运算符走 ``ix_hs_code_description_trgm``。子串匹配用来接住
    短中文片段，短片段常常达不到 trigram 的默认阈值。
    """

    needle = q.strip()
    prefix = f"{_escape_like(needle)}%"
    contains = f"%{_escape_like(needle)}%"
    similarity = func.similarity(HsCode.description, needle)
    code_prefix = HsCode.code.like(prefix, escape="\\")
    # 用户输入 10 位本国编码时，仍能命中库里的 6 位子目。
    code_covered = literal(needle).like(func.concat(HsCode.code, "%"))
    fuzzy = HsCode.description.op("%")(needle)
    score = func.greatest(similarity, case((or_(code_prefix, code_covered), 1), else_=0))
    return (
        select(HsCode)
        .where(
            or_(
                code_prefix,
                code_covered,
                fuzzy,
                similarity > 0.05,
                HsCode.description.ilike(contains, escape="\\"),
            )
        )
        .order_by(score.desc(), HsCode.code.asc())
        .limit(limit)
    )


class HsCodeRepository(BaseRepository[HsCode]):
    model = HsCode

    async def search(self, q: str, *, limit: int) -> list[HsCode]:
        stmt = search_statement(q, limit=limit)
        return list((await self.session.execute(stmt)).scalars().all())


class SpuHsBindingRepository(BaseRepository[SpuHsBinding]):
    model = SpuHsBinding

    async def get_for_market(self, spu_id: int, market: str) -> SpuHsBinding | None:
        return await self.get_by(spu_id=spu_id, market=market)

    async def list_for_spu(self, spu_id: int) -> list[SpuHsBinding]:
        stmt = self.base_select().where(SpuHsBinding.spu_id == spu_id).order_by(SpuHsBinding.market.asc())
        return list((await self.session.execute(stmt)).scalars().all())
