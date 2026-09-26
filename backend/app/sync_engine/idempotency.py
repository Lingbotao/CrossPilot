"""订单幂等三件套里不依赖订单表的两件（M2-03）。

1. 幂等键 ``{platform}:{shop_id}:{platform_order_id}``。轮询和 Webhook 必须算出同一串。
2. 写入判定：只有传入的 ``updated_at`` 严格更新时才覆盖。
3. Redis 布隆过滤器做前置判重。误判成「见过」可以接受，最终以数据库唯一索引为准。

``sales_order.idempotency_key`` 的唯一索引随订单表一起建（M2-05）。键里的 shop_id 是雪花 ID，全局唯一。
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

_BLOOM_KEY = "bloom:orders"


class WriteAction(StrEnum):
    INSERT = "insert"
    UPDATE = "update"
    SKIP = "skip"


class DedupHint(StrEnum):
    PROBABLY_NEW = "probably_new"
    PROBABLY_SEEN = "probably_seen"


def order_idempotency_key(platform: str, shop_id: int | str, platform_order_id: str) -> str:
    platform_norm = platform.strip().lower()
    shop = str(shop_id).strip()
    order_id = platform_order_id.strip()
    if not platform_norm or not shop or not order_id:
        raise ValueError("幂等键需要 platform、shop_id 和 platform_order_id")
    return f"{platform_norm}:{shop}:{order_id}"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def decide_write(stored_updated_at: datetime | None, incoming_updated_at: datetime) -> WriteAction:
    """源数据没有变得更新时跳过，避免旧报文覆盖新状态。"""
    if stored_updated_at is None:
        return WriteAction.INSERT
    if _as_utc(incoming_updated_at) > _as_utc(stored_updated_at):
        return WriteAction.UPDATE
    return WriteAction.SKIP


def bloom_indexes(key: str, *, bit_size: int, hash_count: int) -> list[int]:
    digest = hashlib.sha256(key.encode()).digest()
    first = int.from_bytes(digest[:8], "big")
    second = int.from_bytes(digest[8:16], "big") or 1
    return [(first + i * second) % bit_size for i in range(hash_count)]


class BitStore(Protocol):
    async def enable(self, indexes: list[int]) -> None: ...

    async def read(self, indexes: list[int]) -> list[bool]: ...


class MemoryBitStore:
    def __init__(self, bit_size: int) -> None:
        self._bits = bytearray((bit_size + 7) // 8)

    async def enable(self, indexes: list[int]) -> None:
        for index in indexes:
            self._bits[index // 8] |= 1 << (index % 8)

    async def read(self, indexes: list[int]) -> list[bool]:
        return [bool(self._bits[index // 8] & (1 << (index % 8))) for index in indexes]


class RedisBitStore:
    def __init__(self, url: str, *, client: Any | None = None, key: str = _BLOOM_KEY) -> None:
        self._url = url
        self._client = client
        self._key = key

    async def _redis(self) -> Any:
        if self._client is None:
            from redis.asyncio import Redis

            self._client = Redis.from_url(self._url, decode_responses=True)
        return self._client

    async def enable(self, indexes: list[int]) -> None:
        await self._pipeline(indexes, write=True)

    async def read(self, indexes: list[int]) -> list[bool]:
        values = await self._pipeline(indexes, write=False)
        return [bool(item) for item in values]

    async def _pipeline(self, indexes: list[int], *, write: bool) -> list[int]:
        from app.sync_engine.errors import StoreUnavailable

        try:
            client = await self._redis()
            pipe = client.pipeline(transaction=False)
            for index in indexes:
                if write:
                    pipe.setbit(self._key, index, 1)
                else:
                    pipe.getbit(self._key, index)
            result = await pipe.execute()
        except _redis_errors() as exc:
            raise StoreUnavailable(str(exc)) from exc
        return [int(item) for item in result]


def _redis_errors() -> tuple[type[BaseException], ...]:
    import redis.exceptions

    return (redis.exceptions.RedisError, TimeoutError, OSError)


class BloomFilter:
    def __init__(self, store: BitStore, *, bit_size: int, hash_count: int) -> None:
        self._store = store
        self._bit_size = bit_size
        self._hash_count = hash_count

    def _indexes(self, key: str) -> list[int]:
        return bloom_indexes(key, bit_size=self._bit_size, hash_count=self._hash_count)

    async def add(self, key: str) -> None:
        await self._store.enable(self._indexes(key))

    async def might_contain(self, key: str) -> bool:
        bits = await self._store.read(self._indexes(key))
        return all(bits)


async def dedup_hint(bloom: BloomFilter, key: str) -> DedupHint:
    if await bloom.might_contain(key):
        return DedupHint.PROBABLY_SEEN
    return DedupHint.PROBABLY_NEW


async def remember(bloom: BloomFilter, key: str) -> None:
    await bloom.add(key)


__all__ = [
    "BitStore",
    "BloomFilter",
    "DedupHint",
    "MemoryBitStore",
    "RedisBitStore",
    "WriteAction",
    "bloom_indexes",
    "decide_write",
    "dedup_hint",
    "order_idempotency_key",
    "remember",
]
