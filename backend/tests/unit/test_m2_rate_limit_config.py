"""平台限流配额：缺行用代码默认值，有行则整行覆盖。"""

import pytest

from app.adapters.quotas import quota_for
from app.core.errors import ParamInvalidError, PlatformUnsupportedError
from app.db.base import TenantMixin
from app.models.config import PlatformRateLimit
from app.services.rate_limit_config import StoredQuota, effective_spec, parse_quota


def test_missing_row_keeps_the_code_default() -> None:
    spec = effective_spec("shopee", None)
    assert spec == quota_for("shopee")
    assert spec.daily_quota is None


def test_stored_row_replaces_qps_and_can_set_a_daily_cap() -> None:
    spec = effective_spec(
        "shopee",
        StoredQuota(qps=2, burst=4, dimension="shop", batch_limit=10, daily_quota=30, concurrency=2),
    )
    assert spec.qps == 2
    assert spec.burst == 4
    assert spec.daily_quota == 30
    assert spec.concurrency == 2
    assert spec != quota_for("shopee")


def test_parse_quota_rejects_unknown_platform_and_bad_dimension() -> None:
    with pytest.raises(PlatformUnsupportedError):
        parse_quota(
            platform_code="ebay",
            dimension="shop",
            qps=1,
            burst=1,
            batch_limit=1,
            daily_quota=None,
            concurrency=None,
        )
    with pytest.raises(ParamInvalidError):
        parse_quota(
            platform_code="Shopee",
            dimension="global",
            qps=1,
            burst=1,
            batch_limit=1,
            daily_quota=None,
            concurrency=None,
        )


def test_rate_limit_table_is_global_config() -> None:
    assert not issubclass(PlatformRateLimit, TenantMixin)
    names = {constraint.name for constraint in PlatformRateLimit.__table__.constraints}
    assert "uq_platform_rate_limit_platform_code" in names
    assert "ck_platform_rate_limit_qps" in names
    assert "ck_platform_rate_limit_dimension" in names
    assert "ck_platform_rate_limit_daily_quota" in names
