"""刊登前 L1/L2 与体检分类。

只吃已经查好的绑定、规则和证书，不访问数据库，也不写法定禁售清单。
未传入的认证要求视为「运营还没配置」，不当成缺失。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.services.compliance_windows import CERT_FAR_DAYS, cert_gaps, cert_window

LEVEL_L1 = "L1"
LEVEL_L2 = "L2"
CODE_HS_MISSING = "HS_MISSING"
CODE_CERT_MISSING = "CERT_MISSING"
CODE_CERT_EXPIRED = "CERT_EXPIRED"
CODE_CERT_EXPIRING = "CERT_EXPIRING"
_WARN_WINDOWS = frozenset({"D7", "D30", "D60"})


@dataclass(frozen=True)
class PublishFinding:
    level: str
    code: str
    market: str | None
    cert_type: str | None
    days_left: int | None
    spu_id: int | None = None
    sku_id: int | None = None


@dataclass(frozen=True)
class SpuFact:
    spu_id: int
    title: str
    category_code: str | None
    hs_markets: frozenset[str]


@dataclass(frozen=True)
class SkuFact:
    sku_id: int
    spu_id: int
    sku_code: str


@dataclass(frozen=True)
class CertFact:
    sku_id: int
    market: str
    cert_type: str
    expires_on: date


@dataclass(frozen=True)
class RuleFact:
    market: str
    category_code: str
    cert_type: str


def publish_findings(
    *,
    market: str,
    hs_bound: bool,
    required: set[str],
    held_until: dict[str, date],
    today: date,
) -> list[PublishFinding]:
    """一个 SKU 发到一个站点。L1 拦截，仍有效但落在 60 天内的证书只提醒。"""

    findings: list[PublishFinding] = []
    if not hs_bound:
        findings.append(PublishFinding(LEVEL_L1, CODE_HS_MISSING, market, None, None))
    findings.extend(_cert_findings(market, required, held_until, today))
    return findings


def catalog_findings(
    *,
    spus: list[SpuFact],
    skus: list[SkuFact],
    certs: list[CertFact],
    rules: list[RuleFact],
    today: date,
) -> list[PublishFinding]:
    """体检清单。未绑定任何 HS 的商品算一条红灯；过期证书即使没有要求规则也列出。"""

    skus_by_spu: dict[int, list[SkuFact]] = {}
    sku_spu: dict[int, int] = {}
    for sku in skus:
        skus_by_spu.setdefault(sku.spu_id, []).append(sku)
        sku_spu[sku.sku_id] = sku.spu_id
    required_by: dict[tuple[str, str], set[str]] = {}
    for rule in rules:
        required_by.setdefault((rule.market, rule.category_code), set()).add(rule.cert_type)
    held = _latest_certs(certs)
    findings: list[PublishFinding] = []
    covered: set[tuple[int, str, str, str]] = set()
    for spu in spus:
        if not spu.hs_markets:
            findings.append(PublishFinding(LEVEL_L1, CODE_HS_MISSING, None, None, None, spu_id=spu.spu_id))
        if not spu.category_code:
            continue
        markets = sorted({market for market, category in required_by if category == spu.category_code})
        for sku in skus_by_spu.get(spu.spu_id, []):
            for market in markets:
                required = required_by[(market, spu.category_code)]
                held_until = {
                    cert_type: held[(sku.sku_id, market, cert_type)]
                    for cert_type in required
                    if (sku.sku_id, market, cert_type) in held
                }
                for item in _cert_findings(market, required, held_until, today, spu_id=spu.spu_id, sku_id=sku.sku_id):
                    findings.append(item)
                    if item.cert_type is not None:
                        covered.add((sku.sku_id, market, item.cert_type, item.code))
    for (sku_id, market, cert_type), expires_on in held.items():
        if sku_id not in sku_spu:
            continue
        window = cert_window(expires_on, today)
        if window == "EXPIRED":
            key = (sku_id, market, cert_type, CODE_CERT_EXPIRED)
            if key in covered:
                continue
            findings.append(
                PublishFinding(
                    LEVEL_L1,
                    CODE_CERT_EXPIRED,
                    market,
                    cert_type,
                    None,
                    spu_id=sku_spu[sku_id],
                    sku_id=sku_id,
                )
            )
            continue
        if window in _WARN_WINDOWS:
            key = (sku_id, market, cert_type, CODE_CERT_EXPIRING)
            if key in covered:
                continue
            findings.append(
                PublishFinding(
                    LEVEL_L2,
                    CODE_CERT_EXPIRING,
                    market,
                    cert_type,
                    (expires_on - today).days,
                    spu_id=sku_spu[sku_id],
                    sku_id=sku_id,
                )
            )
    return findings


def _cert_findings(
    market: str,
    required: set[str],
    held_until: dict[str, date],
    today: date,
    *,
    spu_id: int | None = None,
    sku_id: int | None = None,
) -> list[PublishFinding]:
    findings: list[PublishFinding] = []
    for cert_type, reason in cert_gaps(required, held_until, today):
        code = CODE_CERT_EXPIRED if reason == "EXPIRED" else CODE_CERT_MISSING
        findings.append(PublishFinding(LEVEL_L1, code, market, cert_type, None, spu_id=spu_id, sku_id=sku_id))
    for cert_type in sorted(required):
        expires_on = held_until.get(cert_type)
        if expires_on is None:
            continue
        window = cert_window(expires_on, today)
        if window in _WARN_WINDOWS and (expires_on - today).days <= CERT_FAR_DAYS:
            findings.append(
                PublishFinding(
                    LEVEL_L2,
                    CODE_CERT_EXPIRING,
                    market,
                    cert_type,
                    (expires_on - today).days,
                    spu_id=spu_id,
                    sku_id=sku_id,
                )
            )
    return findings


def _latest_certs(certs: list[CertFact]) -> dict[tuple[int, str, str], date]:
    held: dict[tuple[int, str, str], date] = {}
    for cert in certs:
        key = (cert.sku_id, cert.market, cert.cert_type)
        current = held.get(key)
        if current is None or cert.expires_on > current:
            held[key] = cert.expires_on
    return held


__all__ = [
    "CODE_CERT_EXPIRED",
    "CODE_CERT_EXPIRING",
    "CODE_CERT_MISSING",
    "CODE_HS_MISSING",
    "LEVEL_L1",
    "LEVEL_L2",
    "CertFact",
    "PublishFinding",
    "RuleFact",
    "SkuFact",
    "SpuFact",
    "catalog_findings",
    "publish_findings",
]
