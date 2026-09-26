"""分级队列（M2-02）。

``high`` 留给用户手动触发，``sync.{platform}`` 把平台故障隔离开，
``dlq`` 只声明、不自动消费。本机 ``make worker`` 用一个进程把除死信以外的队列都听上。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

PLATFORMS = ("amazon", "shopee", "lazada", "tiktok")


@dataclass(frozen=True, slots=True)
class WorkerPool:
    name: str
    queues: tuple[str, ...]
    concurrency: int

    def queue_arg(self) -> str:
        return ",".join(self.queues)


WORKER_POOLS: tuple[WorkerPool, ...] = (
    WorkerPool("high", ("high",), 8),
    WorkerPool(
        "sync",
        ("sync", "sync.amazon", "sync.shopee", "sync.lazada", "sync.tiktok"),
        4,
    ),
    WorkerPool("batch", ("batch",), 4),
    WorkerPool("report", ("report",), 2),
)

DECLARED_QUEUES: tuple[str, ...] = (
    *(queue for pool in WORKER_POOLS for queue in pool.queues),
    "dlq",
)


def task_queue(task_name: str, *, platform: str | None = None) -> str:
    if task_name.startswith("sync.manual."):
        return "high"
    if task_name.startswith("sync."):
        if platform in PLATFORMS:
            return f"sync.{platform}"
        return "sync"
    if task_name.startswith("batch."):
        return "batch"
    if task_name.startswith("report."):
        return "report"
    return "sync"


def local_dev_queues() -> str:
    names = [queue for pool in WORKER_POOLS for queue in pool.queues]
    return ",".join(names)


class SyncTaskRouter:
    """Celery ``task_routes`` 入口。手动同步进 high，带 platform 的同步进平台队列。"""

    def route_for_task(self, task: str, args: Any = None, kwargs: Any = None) -> dict[str, str]:
        del args
        platform: str | None = None
        if isinstance(kwargs, dict):
            raw = kwargs.get("platform")
            if isinstance(raw, str):
                platform = raw
        return {"queue": task_queue(task, platform=platform)}


__all__ = [
    "DECLARED_QUEUES",
    "PLATFORMS",
    "WORKER_POOLS",
    "SyncTaskRouter",
    "WorkerPool",
    "local_dev_queues",
    "task_queue",
]
