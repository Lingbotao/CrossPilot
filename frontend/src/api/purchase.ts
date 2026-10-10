/** 供应商、采购单、在途和头程。 */

import { api } from './client';
import type {
  InTransitView,
  PageData,
  PurchaseOrderCreate,
  PurchaseOrderView,
  ReceiptCreate,
  ReceiptView,
  ReplenishmentConvert,
  ShipmentCreate,
  ShipmentView,
  SupplierView,
  SupplierWrite,
} from './types';

export const purchaseApi = {
  listSuppliers: (query: { cursor?: string; limit?: number }) =>
    api.get<PageData<SupplierView>>('/suppliers', { params: query }),

  createSupplier: (payload: SupplierWrite) => api.post<SupplierView>('/suppliers', payload, { idempotent: true }),

  replaceSkus: (supplierId: string, links: { sku_id: string; is_default: boolean }[]) =>
    api.put<SupplierView>(`/suppliers/${encodeURIComponent(supplierId)}/skus`, { links }, { idempotent: true }),

  listOrders: (query: { cursor?: string; limit?: number }) =>
    api.get<PageData<PurchaseOrderView>>('/purchase-orders', { params: query }),

  createOrder: (payload: PurchaseOrderCreate) =>
    api.post<PurchaseOrderView>('/purchase-orders', payload, { idempotent: true }),

  transition: (orderId: string, action: 'submit' | 'approve' | 'reject' | 'close' | 'cancel') =>
    api.post<PurchaseOrderView>(`/purchase-orders/${encodeURIComponent(orderId)}/${action}`, {}, { idempotent: true }),

  receive: (orderId: string, payload: ReceiptCreate) =>
    api.post<ReceiptView>(`/purchase-orders/${encodeURIComponent(orderId)}/receipts`, payload, { idempotent: true }),

  inTransit: () => api.get<InTransitView>('/purchase-in-transit'),

  listShipments: (query: { cursor?: string; limit?: number }) =>
    api.get<PageData<ShipmentView>>('/first-mile-shipments', { params: query }),

  createShipment: (payload: ShipmentCreate) =>
    api.post<ShipmentView>('/first-mile-shipments', payload, { idempotent: true }),

  postShipment: (shipmentId: string) =>
    api.post<ShipmentView>(`/first-mile-shipments/${encodeURIComponent(shipmentId)}/post`, {}, { idempotent: true }),

  fromReplenishment: (payload: ReplenishmentConvert) =>
    api.post<PurchaseOrderView[]>('/purchase-orders/from-replenishment', payload, { idempotent: true }),
};
