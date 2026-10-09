"""HS 编码库与商品按市场绑定（M4-01）。

``hs_code`` 是平台词典，没有 ``tenant_id``，也不做 RLS。税率不在这张表里，
留给 M4-02 的版本化税率；这里不填、不猜任何税率。

``spu_hs_binding`` 是租户主数据。同一商品在同一市场只保留一行，再次绑定是更新。
绑定依据、操作人和时间写入审计日志，本表不软删除。
"""

from __future__ import annotations

from typing import NamedTuple

from sqlalchemy import BigInteger, CheckConstraint, ForeignKeyConstraint, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import AuditMixin, Base, PKMixin, TenantMixin
from app.models.locale import CONTENT_MARKETS

_MARKET_SQL = ", ".join(f"'{item}'" for item in CONTENT_MARKETS)

# 公开税则原文的出处。种子行必须带上它，没有出处的编码不入库。
HS_NOMENCLATURE_SOURCE = "WCO Harmonized System Nomenclature 2022; 中华人民共和国进出口税则（2022）"


class HsSeed(NamedTuple):
    code: str
    description: str
    parent_code: str | None
    chapter: str
    level: int


# 只收录能对上上述出处的 6 位子目。描述用税则中文品名，不缩写成营销文案。
HS_SEED: tuple[HsSeed, ...] = (
    HsSeed("090121", "已焙炒未浸除咖啡碱的咖啡", "0901", "09", 6),
    HsSeed("330499", "其他美容品或化妆品及护肤品", "3304", "33", 6),
    HsSeed("392410", "塑料制餐具及厨房用具", "3924", "39", 6),
    HsSeed("420221", "以皮革或再生皮革作面的手提包", "4202", "42", 6),
    HsSeed("610462", "棉制针织或钩编的女式长裤、护胸背带工装裤、马裤及短裤", "6104", "61", 6),
    HsSeed("610910", "棉制针织或钩编的T恤衫、汗衫及其他背心", "6109", "61", 6),
    HsSeed("611020", "棉制针织或钩编的套头衫、开襟衫、马甲及类似品", "6110", "61", 6),
    HsSeed("620342", "棉制男式长裤、护胸背带工装裤、马裤及短裤", "6203", "62", 6),
    HsSeed("620462", "棉制女式长裤、护胸背带工装裤、马裤及短裤", "6204", "62", 6),
    HsSeed("630260", "棉制毛巾织物或类似毛圈织物的盥洗及厨房用织物制品", "6302", "63", 6),
    HsSeed("640399", "其他鞋靴，外底用橡胶、塑料、皮革或再生皮革制成，鞋面用皮革制成", "6403", "64", 6),
    HsSeed("850760", "锂离子蓄电池", "8507", "85", 6),
    HsSeed("851713", "智能手机", "8517", "85", 6),
    HsSeed("940360", "其他木家具", "9403", "94", 6),
    HsSeed(
        "950300",
        "三轮车、踏板车、踏板汽车及类似的带轮玩具；玩偶车；玩偶；其他玩具；缩小的模型及类似的娱乐用模型；各种智力玩具",
        "9503",
        "95",
        6,
    ),
)


def hs_seed_id(seq: int) -> int:
    """种子行使用固定雪花区间，重复迁移时主键不变。"""
    if seq < 1:
        raise ValueError("种子序号从 1 开始")
    return 8_400_000_000_000_000_000 + seq


class HsCode(Base, PKMixin, AuditMixin):
    """全球 HS 词典。应用角色只读，写入只发生在迁移种子。"""

    __tablename__ = "hs_code"

    code: Mapped[str] = mapped_column(String(16), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    parent_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    chapter: Mapped[str] = mapped_column(String(2), nullable=False)
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint("code", name="uq_hs_code_code"),
        CheckConstraint("code ~ '^[0-9]{4,10}$'", name="code"),
        CheckConstraint("level IN (2, 4, 6, 8, 10)", name="level"),
        CheckConstraint("char_length(chapter) = 2 AND chapter ~ '^[0-9]{2}$'", name="chapter"),
        CheckConstraint("char_length(btrim(description)) > 0", name="description"),
        CheckConstraint("char_length(btrim(source)) > 0", name="source"),
        Index("ix_hs_code_chapter", "chapter"),
        Index(
            "ix_hs_code_description_trgm",
            "description",
            postgresql_using="gin",
            postgresql_ops={"description": "gin_trgm_ops"},
        ),
    )


class SpuHsBinding(Base, PKMixin, TenantMixin, AuditMixin):
    """一个 SPU 在一个市场绑定一个 HS 编码。"""

    __tablename__ = "spu_hs_binding"

    spu_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    market: Mapped[str] = mapped_column(String(2), nullable=False)
    hs_code_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    basis: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(["spu_id"], ["spu.id"], name="fk_spu_hs_binding_spu_id_spu"),
        ForeignKeyConstraint(["hs_code_id"], ["hs_code.id"], name="fk_spu_hs_binding_hs_code_id_hs_code"),
        UniqueConstraint(
            "tenant_id",
            "spu_id",
            "market",
            name="uq_spu_hs_binding_tenant_id_spu_id_market",
        ),
        CheckConstraint(f"market IN ({_MARKET_SQL})", name="market"),
        CheckConstraint("char_length(btrim(basis)) > 0", name="basis"),
        Index("ix_spu_hs_binding_tenant_id_spu_id", "tenant_id", "spu_id"),
    )


__all__ = [
    "HS_NOMENCLATURE_SOURCE",
    "HS_SEED",
    "HsCode",
    "HsSeed",
    "SpuHsBinding",
    "hs_seed_id",
]
