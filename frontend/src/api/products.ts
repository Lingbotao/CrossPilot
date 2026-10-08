/** 商品库：SPU / SKU 主数据。 */

import { api } from './client';
import type {
  PageData,
  ProductImageType,
  ProductImageView,
  ProductImportResult,
  SkuPatch,
  SkuView,
  SkuWrite,
  SpreadsheetFile,
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

  exportFile: (query: Pick<SpuListQuery, 'status' | 'q'>) =>
    api.get<SpreadsheetFile>('/spus/export', { params: query }),

  importTemplate: () => api.get<SpreadsheetFile>('/spus/import-template'),

  importFile: (file: File) => {
    const body = new FormData();
    body.append('file', file);
    return api.post<ProductImportResult>('/spus/import', body, { idempotent: true, skipErrorToast: true });
  },

  images: (spuId: string) =>
    api.get<ProductImageView[]>('/product-images', { params: { spu_id: spuId } }),

  uploadImage: (spuId: string, imageType: ProductImageType, file: File) => {
    const body = new FormData();
    body.append('spu_id', spuId);
    body.append('image_type', imageType);
    body.append('file', file);
    return api.post<ProductImageView>('/product-images', body, { idempotent: true });
  },

  removeImage: (imageId: string) =>
    api.delete<ProductImageView>(`/product-images/${encodeURIComponent(imageId)}`, { idempotent: true }),
};
