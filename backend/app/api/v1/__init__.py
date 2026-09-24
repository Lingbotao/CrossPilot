"""v1 接口聚合。

接口批次规划（PRD 10.3 / 开发规划 §7）：
    批次 1（M0–M1）``/auth/*`` ``/tenants/current`` ``/members/*`` ``/roles`` ``/shops/*`` ``/sync-tasks/*``
    批次 2（M2）    ``/orders/*`` ``/webhooks/{platform}``
    批次 3（M3）    ``/spus/*`` ``/listings/*`` ``/inventories/*`` ``/warehouses``
    批次 4（M4）    ``/hs-codes/search`` ``/tax-rules`` ``/certificates`` ``/landed-cost/*`` ``/profit/*``
    批次 5（M5）    ``/suppliers/*`` ``/purchase-orders/*`` ``/ads/*`` ``/cs/*`` ``/dashboard/*``
    批次 6（M6）    开放 API / 出站 Webhook —— **已暂缓至上线前**（规划 §1.4 A 类）
"""

from fastapi import APIRouter

from app.api.v1 import audit, auth, members, roles, tenants

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(tenants.router)
api_router.include_router(members.router)
api_router.include_router(roles.router)
api_router.include_router(audit.router)

__all__ = ["api_router"]
