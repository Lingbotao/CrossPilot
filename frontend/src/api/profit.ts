/** 汇率、SKU 利润与瀑布。 */

import { api } from './client';
import type {
  ExchangeRateCreate,
  ExchangeRateView,
  ProfitMaterializeRequest,
  ProfitMaterializeView,
  ProfitRowView,
  SettlementDetail,
  SettlementSummary,
  WaterfallView,
} from './types';

export const profitApi = {
  rates: (basis?: string) =>
    api.get<ExchangeRateView[]>('/exchange-rates', { params: basis ? { basis } : {} }),

  createRate: (payload: ExchangeRateCreate) =>
    api.post<ExchangeRateView>('/exchange-rates', payload, { idempotent: true }),

  lockRate: (rateId: string) =>
    api.post<ExchangeRateView>(`/exchange-rates/${encodeURIComponent(rateId)}/lock`, {}, { idempotent: true }),

  materialize: (payload: ProfitMaterializeRequest) =>
    api.post<ProfitMaterializeView>('/profit/materialize', payload, { idempotent: true }),

  sku: (params: { date_from: string; date_to: string; grain: string; currency?: string }) =>
    api.get<ProfitRowView[]>('/profit/sku', { params }),

  waterfall: (params: { date_from: string; date_to: string; currency?: string }) =>
    api.get<WaterfallView>('/profit/waterfall', { params }),

  settlements: () => api.get<SettlementSummary[]>('/profit/settlements'),

  settlement: (settlementId: string) =>
    api.get<SettlementDetail>(`/profit/settlements/${encodeURIComponent(settlementId)}`),

  importSettlement: (payload: {
    shop_id: string;
    platform_settlement_id: string;
    period_start: string;
    period_end: string;
    currency: string;
    file: File;
  }) => {
    const body = new FormData();
    body.append('shop_id', payload.shop_id);
    body.append('platform_settlement_id', payload.platform_settlement_id);
    body.append('period_start', payload.period_start);
    body.append('period_end', payload.period_end);
    body.append('currency', payload.currency);
    body.append('file', payload.file);
    return api.post<SettlementDetail>('/profit/settlements', body, { idempotent: true });
  },
};
