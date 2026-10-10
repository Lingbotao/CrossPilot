/** 广告只读接口。同步是拉取，不是改投放。 */

import { api } from './client';
import type {
  AdsCampaignDetail,
  AdsCampaignView,
  AdsKeywordView,
  AdsLossView,
  AdsOverviewView,
  AdsQuery,
  AdsSkuProfitView,
  AdsSyncRequest,
  AdsSyncView,
  PageData,
} from './types';

export const adsApi = {
  sync: (payload: AdsSyncRequest) => api.post<AdsSyncView>('/ads/sync', payload, { idempotent: true }),

  overview: (query: AdsQuery) => api.get<AdsOverviewView>('/ads/overview', { params: query }),

  campaigns: (query: AdsQuery) => api.get<PageData<AdsCampaignView>>('/ads/campaigns', { params: query }),

  campaign: (campaignId: string, query: AdsQuery) =>
    api.get<AdsCampaignDetail>(`/ads/campaigns/${encodeURIComponent(campaignId)}`, { params: query }),

  keywords: (query: AdsQuery) => api.get<PageData<AdsKeywordView>>('/ads/keywords', { params: query }),

  loss: (query: AdsQuery) => api.get<PageData<AdsLossView>>('/ads/loss', { params: query }),

  skuProfit: (query: AdsQuery) => api.get<AdsSkuProfitView[]>('/ads/sku-profit', { params: query }),
};
