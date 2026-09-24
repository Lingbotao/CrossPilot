"""ID 生成器（PRD 9.1）。

为什么不用自增 ID：
1. 自增 ID 会**暴露业务规模**（竞争对手下两单就知道你卖了多少）；
2. 分库分表后自增无法保证全局唯一；
3. 自增 ID 可枚举 → 配合越权漏洞可以遍历他租户数据。

Snowflake 结构（64 位）：1 位符号 + 41 位毫秒时间 + 10 位机器号 + 12 位序列
    - 41 位时间戳：约 69 年
    - 10 位机器号：1024 个节点（K8s 下用 StatefulSet 序号或 Pod 名哈希注入）
    - 12 位序列：单节点每毫秒 4096 个 ID
"""

from __future__ import annotations

import os
import threading
import time

# 2025-01-01T00:00:00Z，自定义纪元可延长可用年限
_EPOCH_MS = 1_735_689_600_000

_WORKER_BITS = 10
_SEQ_BITS = 12
_MAX_WORKER = (1 << _WORKER_BITS) - 1
_MAX_SEQ = (1 << _SEQ_BITS) - 1


class SnowflakeGenerator:
    """线程安全的 Snowflake 生成器。

    时钟回拨处理：不抛异常、不回退时间戳（回退会产生重复 ID），
    而是**沿用上一毫秒并递增序列**，宁可短暂抖一下也不出重复主键。
    """

    def __init__(self, worker_id: int | None = None) -> None:
        env_worker = int(os.getenv("SNOWFLAKE_WORKER_ID", "1"))
        self._worker_id = (worker_id if worker_id is not None else env_worker) & _MAX_WORKER
        self._lock = threading.Lock()
        self._last_ms = -1
        self._seq = 0

    @property
    def worker_id(self) -> int:
        return self._worker_id

    def next_id(self) -> int:
        with self._lock:
            now = int(time.time() * 1000)
            if now < self._last_ms:
                now = self._last_ms
            if now == self._last_ms:
                self._seq += 1
                if self._seq > _MAX_SEQ:
                    now = self._block_until(self._last_ms + 1)
                    self._seq = 0
            else:
                self._seq = 0
            self._last_ms = now
            return ((now - _EPOCH_MS) << (_WORKER_BITS + _SEQ_BITS)) | (self._worker_id << _SEQ_BITS) | self._seq

    @staticmethod
    def _block_until(target_ms: int) -> int:
        current = int(time.time() * 1000)
        while current < target_ms:
            time.sleep(0.0005)
            current = int(time.time() * 1000)
        return current


_generator = SnowflakeGenerator()


def next_snowflake_id() -> int:
    """作为 ``mapped_column(default=...)`` 的默认值使用。"""
    return _generator.next_id()


def parse_snowflake_timestamp(snowflake_id: int) -> int:
    """从 ID 反解出毫秒时间戳 —— 排障时非常有用（不用查库就知道创建时间）。"""
    return (snowflake_id >> (_WORKER_BITS + _SEQ_BITS)) + _EPOCH_MS
