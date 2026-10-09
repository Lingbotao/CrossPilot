/** 汇率、SKU 利润与瀑布。 */

import { api } from './client';
import type {
  ExchangeRateCreate,
  ExchangeRateView,
  ProfitMaterializeRequest,
  ProfitMaterializeView,
  ProfitRowView,
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
};
