/** 平台状态映射与订单状态时间轴。映射表是全局配置，写入需要 system:write。 */

import { api } from './client';
import type { OrderStatusLog, OrderStatusMapping, UpsertOrderStatusMapping } from './types';

export const orderStatusApi = {
  listMappings: (platformCode?: string) =>
    api.get<OrderStatusMapping[]>('/order-status-mappings', {
      params: platformCode ? { platform_code: platformCode } : undefined,
    }),
  upsertMapping: (body: UpsertOrderStatusMapping) =>
    api.put<OrderStatusMapping>('/order-status-mappings', body, { idempotent: true }),
  listLogs: (orderId: string) => api.get<OrderStatusLog[]>(`/orders/${orderId}/status-logs`),
};
