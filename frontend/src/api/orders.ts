/** 订单列表、详情、导出、批量发货与面单。 */

import { api } from './client';
import type {
  BatchShipResult,
  FilePayload,
  LabelSize,
  OrderDetail,
  OrderListItem,
  OrderListQuery,
  PageData,
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
