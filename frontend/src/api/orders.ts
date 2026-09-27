/** 订单列表、详情、导出、批量发货与面单。 */

import { api } from './client';
import type {
  BatchShipResult,
  FilePayload,
  LabelSize,
  OrderAddressWrite,
  OrderDetail,
  OrderFreshness,
  OrderListItem,
  OrderListQuery,
  PageData,
  ReturnOrderView,
  ReviewRule,
} from './types';

export const ordersApi = {
  list: (query: OrderListQuery) => api.get<PageData<OrderListItem>>('/orders', { params: query }),

  detail: (orderId: string) => api.get<OrderDetail>(`/orders/${encodeURIComponent(orderId)}`),

  exportFile: (query: OrderListQuery) =>
    api.get<FilePayload>('/orders/export', { params: query }),

  batchShip: (orderIds: string[], carrier: string) =>
    api.post<BatchShipResult>('/orders/batch-ship', { order_ids: orderIds, carrier }, { idempotent: true }),

  labels: (orderIds: string[], size: LabelSize) =>
    api.post<FilePayload>('/orders/labels', { order_ids: orderIds, size }, { idempotent: true }),

  freshness: () => api.get<OrderFreshness[]>('/orders/freshness'),

  reviewRules: () => api.get<ReviewRule[]>('/orders/review-rules'),

  saveReviewRule: (currency: string, amountGt: string, enabled: boolean) =>
    api.put<ReviewRule>('/orders/review-rules', { currency, amount_gt: amountGt, enabled }, { idempotent: true }),

  decideReview: (orderId: string, decision: 'approve' | 'reject') =>
    api.post<OrderDetail>(`/orders/${encodeURIComponent(orderId)}/review`, { decision }, { idempotent: true }),

  changeAddress: (orderId: string, address: OrderAddressWrite) =>
    api.post<OrderDetail>(`/orders/${encodeURIComponent(orderId)}/address`, address, { idempotent: true }),

  returns: () => api.get<ReturnOrderView[]>('/orders/returns'),

  createReturn: (orderId: string, reason: string, refundAmount: string, restockFlag: boolean, restockSellable: boolean) =>
    api.post<ReturnOrderView>(
      `/orders/${encodeURIComponent(orderId)}/returns`,
      { reason, refund_amount: refundAmount, restock_flag: restockFlag, restock_sellable: restockSellable },
      { idempotent: true },
    ),

  transitionReturn: (returnId: string, action: 'approve' | 'reject' | 'refund') =>
    api.post<ReturnOrderView>(`/orders/returns/${encodeURIComponent(returnId)}/${action}`, {}, { idempotent: true }),
};

export function downloadBase64(filename: string, contentType: string, contentBase64: string) {
  const binary = atob(contentBase64);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  const blob = new Blob([bytes], { type: contentType });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}
