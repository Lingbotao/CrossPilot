/** 落地成本计算与费用规则。 */

import { api } from './client';
import type {
  LandedCostCalcRequest,
  LandedCostCalcView,
  LandedCostCompareView,
  LandedCostFeeCreate,
  LandedCostFeeView,
  LandedCostPricingRequest,
  LandedCostPricingView,
  LandedCostToggleView,
  LandedCostToggleWrite,
} from './types';

export const landedCostApi = {
  fees: (market?: string) =>
    api.get<LandedCostFeeView[]>('/landed-cost/fees', { params: market ? { market } : {} }),

  createFee: (payload: LandedCostFeeCreate) =>
    api.post<LandedCostFeeView>('/landed-cost/fees', payload, { idempotent: true }),

  retireFee: (feeId: string) =>
    api.post<LandedCostFeeView>(`/landed-cost/fees/${encodeURIComponent(feeId)}/retire`, {}, { idempotent: true }),

  calculate: (payload: LandedCostCalcRequest) =>
    api.post<LandedCostCalcView>('/landed-cost/calculations', payload, { idempotent: true }),

  compare: (payload: { left: LandedCostCalcRequest; right: LandedCostCalcRequest }) =>
    api.post<LandedCostCompareView>('/landed-cost/comparisons', payload, { idempotent: true }),

  toggles: (market?: string) =>
    api.get<LandedCostToggleView[]>('/landed-cost/toggles', { params: market ? { market } : {} }),

  setToggle: (payload: LandedCostToggleWrite) =>
    api.put<LandedCostToggleView>('/landed-cost/toggles', payload, { idempotent: true }),

  price: (payload: LandedCostPricingRequest) =>
    api.post<LandedCostPricingView>('/landed-cost/pricing', payload, { idempotent: true }),
};
