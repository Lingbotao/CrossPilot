"""M2-03 幂等键、更新时间判定和布隆过滤器。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.sync_engine.idempotency import (
    BloomFilter,
    DedupHint,
    MemoryBitStore,
    RedisBitStore,
    WriteAction,
    decide_write,
    dedup_hint,
    order_idempotency_key,
    remember,
)
from app.sync_engine.policy import SyncPolicy


def test_poll_and_webhook_share_one_key() -> None:
    assert order_idempotency_key("Shopee", 10, " ABC ") == "shopee:10:ABC"
    assert order_idempotency_key("shopee", "10", "ABC") == order_idempotency_key("SHOPEE", 10, "ABC")


def test_idempotency_key_rejects_blanks() -> None:
    with pytest.raises(ValueError):
        order_idempotency_key(" ", 1, "x")
    with pytest.raises(ValueError):
        order_idempotency_key("shopee", "", "x")
    with pytest.raises(ValueError):
        order_idempotency_key("shopee", 1, "  ")


def test_write_only_when_source_is_newer() -> None:
    moment = datetime(2026, 1, 1, 12, tzinfo=UTC)
    assert decide_write(None, moment) is WriteAction.INSERT
    assert decide_write(moment, moment) is WriteAction.SKIP
    assert decide_write(moment, moment - timedelta(seconds=1)) is WriteAction.SKIP
    assert decide_write(moment, moment + timedelta(seconds=1)) is WriteAction.UPDATE
    assert decide_write(datetime(2026, 1, 1, 12), moment) is WriteAction.SKIP


async def test_bloom_hints_new_then_seen() -> None:
    policy = SyncPolicy()
    bloom = BloomFilter(
        MemoryBitStore(policy.bloom_bit_size),
        bit_size=policy.bloom_bit_size,
        hash_count=policy.bloom_hash_count,
    )
    key = order_idempotency_key("shopee", 10, "ABC")
    assert await bloom.might_contain(key) is False
    assert await dedup_hint(bloom, key) is DedupHint.PROBABLY_NEW
    await remember(bloom, key)
    assert await dedup_hint(bloom, key) is DedupHint.PROBABLY_SEEN


class _BitPipe:
    def __init__(self, bits: set[int]) -> None:
        self._bits = bits
        self._ops: list[tuple[str, int]] = []

    def setbit(self, key: str, offset: int, value: int) -> _BitPipe:
        del key, value
        self._ops.append(("set", offset))
        return self

    def getbit(self, key: str, offset: int) -> _BitPipe:
        del key
        self._ops.append(("get", offset))
        return self

    async def execute(self) -> list[int]:
        result: list[int] = []
        for op, offset in self._ops:
            if op == "set":
                self._bits.add(offset)
                result.append(1)
            else:
                result.append(1 if offset in self._bits else 0)
        return result


class _BitRedis:
    def __init__(self) -> None:
        self.bits: set[int] = set()

    def pipeline(self, transaction: bool = False) -> _BitPipe:
        del transaction
        return _BitPipe(self.bits)


async def test_redis_bloom_roundtrip() -> None:
    bloom = BloomFilter(RedisBitStore("redis://unused", client=_BitRedis()), bit_size=128, hash_count=3)
    assert await bloom.might_contain("shopee:1:A") is False
    await bloom.add("shopee:1:A")
    assert await bloom.might_contain("shopee:1:A") is True
