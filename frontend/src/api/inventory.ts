/** 库存台账、仓库、流水、回传和补货建议。 */

import { api } from './client';
import type {
  InventoryAdjustWrite,
  InventoryFlowView,
  InventoryPushLogView,
  InventoryView,
  PageData,
  ReplenishmentView,
  SafetyStockView,
  SafetyStockWrite,
  SpreadsheetFile,
  WarehouseView,
  WarehouseWrite,
} from './types';

export const inventoryApi = {
  listWarehouses: (query: { cursor?: string; limit?: number }) =>
    api.get<PageData<WarehouseView>>('/warehouses', { params: query }),

  createWarehouse: (payload: WarehouseWrite) =>
    api.post<WarehouseView>('/warehouses', payload, { idempotent: true }),

  removeWarehouse: (warehouseId: string) =>
    api.delete<WarehouseView>(`/warehouses/${encodeURIComponent(warehouseId)}`, { idempotent: true }),

  list: (query: { cursor?: string; limit?: number; sku_id?: string; warehouse_id?: string }) =>
    api.get<PageData<InventoryView>>('/inventories', { params: query }),

  adjust: (payload: InventoryAdjustWrite) =>
    api.post<InventoryView>('/inventories/adjust', payload, { idempotent: true }),

  push: (skuId: string) =>
    api.post<InventoryPushLogView[]>('/inventories/push', { sku_id: skuId }, { idempotent: true }),

  listFlows: (query: {
    cursor?: string;
    limit?: number;
    sku_id?: string;
    ref_type?: string;
    ref_id?: string;
    flow_type?: string;
  }) => api.get<PageData<InventoryFlowView>>('/inventory-flows', { params: query }),

  exportFlows: (query: { sku_id?: string; ref_type?: string; ref_id?: string }) =>
    api.get<SpreadsheetFile>('/inventory-flows/export', { params: query }),

  listPushLogs: (query: { cursor?: string; limit?: number; sku_id?: string }) =>
    api.get<PageData<InventoryPushLogView>>('/inventory-push-logs', { params: query }),

  listSafety: (query: { cursor?: string; limit?: number }) =>
    api.get<PageData<SafetyStockView>>('/platform-safety-stocks', { params: query }),

  saveSafety: (payload: SafetyStockWrite) =>
    api.put<SafetyStockView>('/platform-safety-stocks', payload, { idempotent: true }),

  replenishments: (windowDays: number) =>
    api.get<ReplenishmentView[]>('/inventory-replenishments', { params: { window_days: windowDays } }),
};
