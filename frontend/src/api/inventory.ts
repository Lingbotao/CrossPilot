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
  StockTakingCountLine,
  StockTakingView,
  TransferCreate,
  TransferView,
  TurnoverDimension,
  TurnoverView,
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

  turnover: (dimension: TurnoverDimension, windowDays: number) =>
    api.get<TurnoverView[]>('/inventories/turnover', { params: { dimension, window_days: windowDays } }),

  listTransfers: (query: { cursor?: string; limit?: number }) =>
    api.get<PageData<TransferView>>('/inventories/transfers', { params: query }),

  createTransfer: (payload: TransferCreate) =>
    api.post<TransferView>('/inventories/transfer', payload, { idempotent: true }),

  shipTransfer: (transferId: string) =>
    api.post<TransferView>(`/inventories/transfer/${encodeURIComponent(transferId)}/ship`, {}, { idempotent: true }),

  receiveTransfer: (transferId: string) =>
    api.post<TransferView>(`/inventories/transfer/${encodeURIComponent(transferId)}/receive`, {}, { idempotent: true }),

  cancelTransfer: (transferId: string) =>
    api.post<TransferView>(`/inventories/transfer/${encodeURIComponent(transferId)}/cancel`, {}, { idempotent: true }),

  listTakings: (query: { cursor?: string; limit?: number }) =>
    api.get<PageData<StockTakingView>>('/inventories/stock-takings', { params: query }),

  createTaking: (warehouseId: string) =>
    api.post<StockTakingView>('/inventories/stock-taking', { warehouse_id: warehouseId }, { idempotent: true }),

  recordCounts: (takingId: string, lines: StockTakingCountLine[]) =>
    api.patch<StockTakingView>(`/inventories/stock-taking/${encodeURIComponent(takingId)}`, { lines }, { idempotent: true }),

  postTaking: (takingId: string) =>
    api.post<StockTakingView>(`/inventories/stock-taking/${encodeURIComponent(takingId)}/post`, {}, { idempotent: true }),

  cancelTaking: (takingId: string) =>
    api.post<StockTakingView>(
      `/inventories/stock-taking/${encodeURIComponent(takingId)}/cancel`,
      {},
      { idempotent: true },
    ),
};
