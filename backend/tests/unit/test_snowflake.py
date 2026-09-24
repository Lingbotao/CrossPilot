"""Snowflake ID 生成器。"""

from __future__ import annotations

import time

from app.db.snowflake import SnowflakeGenerator, next_snowflake_id, parse_snowflake_timestamp


class TestSnowflake:
    def test_ids_are_unique(self) -> None:
        gen = SnowflakeGenerator(worker_id=1)
        ids = [gen.next_id() for _ in range(5000)]
        assert len(set(ids)) == len(ids), "生成重复 ID —— 会导致主键冲突"

    def test_ids_are_increasing(self) -> None:
        gen = SnowflakeGenerator(worker_id=1)
        ids = [gen.next_id() for _ in range(1000)]
        assert ids == sorted(ids), "ID 非单调递增 —— 会影响按 ID 排序的分页"

    def test_ids_fit_in_bigint(self) -> None:
        assert next_snowflake_id() < 2**63

    def test_worker_id_isolation(self) -> None:
        """不同节点在同一毫秒生成的 ID 不能撞车。"""
        a = SnowflakeGenerator(worker_id=1)
        b = SnowflakeGenerator(worker_id=2)
        assert a.next_id() != b.next_id()

    def test_timestamp_can_be_recovered(self) -> None:
        """能从 ID 反解创建时间 —— 排障时不用查库。"""
        before = int(time.time() * 1000)
        ts = parse_snowflake_timestamp(next_snowflake_id())
        after = int(time.time() * 1000)
        assert before - 5 <= ts <= after + 5

    def test_high_volume_within_same_millisecond(self) -> None:
        gen = SnowflakeGenerator(worker_id=3)
        ids = {gen.next_id() for _ in range(4096)}
        assert len(ids) == 4096
