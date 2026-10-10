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

from app.api.v1 import (
    ads,
    audit,
    auth,
    compliance,
    dashboard,
    hs_codes,
    inventory,
    landed_cost,
    listings,
    locale,
    members,
    order_status,
    orders,
    products,
    profit,
    purchase,
    rate_limits,
    roles,
    shops,
    sync_tasks,
    tenants,
    webhooks,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(tenants.router)
api_router.include_router(members.router)
api_router.include_router(roles.router)
api_router.include_router(audit.router)
api_router.include_router(shops.router)
api_router.include_router(sync_tasks.router)
api_router.include_router(order_status.router)
api_router.include_router(rate_limits.router)
api_router.include_router(orders.router)
api_router.include_router(products.router)
api_router.include_router(listings.router)
api_router.include_router(locale.router)
api_router.include_router(inventory.router)
api_router.include_router(hs_codes.router)
api_router.include_router(compliance.router)
api_router.include_router(landed_cost.router)
api_router.include_router(profit.router)
api_router.include_router(purchase.suppliers)
api_router.include_router(purchase.orders)
api_router.include_router(purchase.transit)
api_router.include_router(purchase.shipments)
api_router.include_router(ads.router)
api_router.include_router(dashboard.router)
api_router.include_router(webhooks.router)

__all__ = ["api_router"]
