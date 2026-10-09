"""合规提醒日扫描。任务本身不带租户，进入每个租户前显式建立上下文。"""

from __future__ import annotations

import asyncio
from typing import Any

from app.core.logging import configure_logging
from app.services.compliance_alert import scan_compliance_alerts
from app.tasks.celery_app import celery_app


@celery_app.task(name="compliance.scan_alerts")
def scan_alerts() -> dict[str, Any]:
    configure_logging()
    return asyncio.run(scan_compliance_alerts())
