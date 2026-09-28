/** 商品库：SPU / SKU 主数据。 */

import { api } from './client';
import type {
  PageData,
  SkuPatch,
  SkuView,
  SkuWrite,
  SpuCreate,
  SpuDetail,
  SpuListItem,
  SpuListQuery,
  SpuPatch,
} from './types';

export const productsApi = {
  list: (query: SpuListQuery) => api.get<PageData<SpuListItem>>('/spus', { params: query }),

  detail: (spuId: string) => api.get<SpuDetail>(`/spus/${encodeURIComponent(spuId)}`),

  create: (payload: SpuCreate) => api.post<SpuDetail>('/spus', payload, { idempotent: true }),

  update: (spuId: string, payload: SpuPatch) =>
    api.patch<SpuDetail>(`/spus/${encodeURIComponent(spuId)}`, payload, { idempotent: true }),

  addSku: (spuId: string, payload: SkuWrite) =>
    api.post<SkuView>(`/spus/${encodeURIComponent(spuId)}/skus`, payload, { idempotent: true }),

  updateSku: (skuId: string, payload: SkuPatch) =>
    api.patch<SkuView>(`/skus/${encodeURIComponent(skuId)}`, payload, { idempotent: true }),
};
