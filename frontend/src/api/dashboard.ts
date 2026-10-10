/** 经营看板。刷新写入预聚合表，页面只读汇总。 */

import { api } from './client';
import type {
  DashboardAdsRoiView,
  DashboardCostLineView,
  DashboardFulfillmentView,
  DashboardInventoryView,
  DashboardOverviewView,
  DashboardPlatformView,
  DashboardQuery,
  DashboardRankingView,
  DashboardRebuildView,
  DashboardTrendView,
} from './types';

export const dashboardApi = {
  rebuild: (payload: { date_from: string; date_to: string }) =>
    api.post<DashboardRebuildView>('/dashboard/rebuild', payload, { idempotent: true }),

  overview: (query: DashboardQuery) => api.get<DashboardOverviewView>('/dashboard/overview', { params: query }),

  platforms: (query: DashboardQuery) =>
    api.get<DashboardPlatformView[]>('/dashboard/platform-comparison', { params: query }),

  trends: (query: DashboardQuery) => api.get<DashboardTrendView>('/dashboard/trends', { params: query }),

  ranking: (query: DashboardQuery) => api.get<DashboardRankingView>('/dashboard/sku-ranking', { params: query }),

  costs: (query: DashboardQuery) => api.get<DashboardCostLineView[]>('/dashboard/cost-structure', { params: query }),

  inventory: (query: Pick<DashboardQuery, 'date_from' | 'date_to'>) =>
    api.get<DashboardInventoryView>('/dashboard/inventory-health', { params: query }),

  adsRoi: (query: DashboardQuery) => api.get<DashboardAdsRoiView[]>('/dashboard/ads-roi', { params: query }),

  fulfillment: (query: DashboardQuery) => api.get<DashboardFulfillmentView[]>('/dashboard/fulfillment', { params: query }),
};
