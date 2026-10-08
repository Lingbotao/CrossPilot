/** Listing 映射与类目模板。 */

import { api } from './client';
import type {
  CategoryMappingPatch,
  CategoryMappingQuery,
  CategoryMappingView,
  CategoryMappingWrite,
  ListingBatchView,
  ListingDiffView,
  ListingPatch,
  ListingPatrolResult,
  ListingQuery,
  ListingShopOption,
  ListingView,
  ListingWrite,
  PageData,
  PlatformSiteCatalog,
  PriceBatchWrite,
  PricePreview,
  PublishBatchWrite,
} from './types';

export const listingsApi = {
  list: (query: ListingQuery) => api.get<PageData<ListingView>>('/listings', { params: query }),

  shops: () => api.get<ListingShopOption[]>('/listings/shops'),

  detail: (listingId: string) => api.get<ListingView>(`/listings/${encodeURIComponent(listingId)}`),

  create: (payload: ListingWrite) => api.post<ListingView>('/listings', payload, { idempotent: true }),

  update: (listingId: string, payload: ListingPatch) =>
    api.patch<ListingView>(`/listings/${encodeURIComponent(listingId)}`, payload, { idempotent: true }),

  listTemplates: (query: CategoryMappingQuery) =>
    api.get<PageData<CategoryMappingView>>('/category-mappings', { params: query }),

  createTemplate: (payload: CategoryMappingWrite) =>
    api.post<CategoryMappingView>('/category-mappings', payload, { idempotent: true }),

  updateTemplate: (templateId: string, payload: CategoryMappingPatch) =>
    api.patch<CategoryMappingView>(`/category-mappings/${encodeURIComponent(templateId)}`, payload, {
      idempotent: true,
    }),

  catalog: () => api.get<PlatformSiteCatalog[]>('/category-mappings/catalog'),

  publishBatch: (payload: PublishBatchWrite) =>
    api.post<ListingBatchView>('/listing-batches/publish', payload, { idempotent: true }),

  previewPrices: (payload: PriceBatchWrite) =>
    api.post<PricePreview>('/listing-batches/prices/preview', payload),

  repriceBatch: (payload: PriceBatchWrite) =>
    api.post<ListingBatchView>('/listing-batches/prices', payload, { idempotent: true }),

  batch: (batchId: string) => api.get<ListingBatchView>(`/listing-batches/${encodeURIComponent(batchId)}`),

  diffs: (query: { status?: string; cursor?: string; limit?: number }) =>
    api.get<PageData<ListingDiffView>>('/listing-diffs', { params: query }),

  patrolDiffs: () => api.post<ListingPatrolResult>('/listing-diffs/patrol', {}, { idempotent: true }),

  acceptDiff: (diffId: string) =>
    api.post<ListingDiffView>(`/listing-diffs/${encodeURIComponent(diffId)}/accept`, {}, { idempotent: true }),

  dismissDiff: (diffId: string) =>
    api.post<ListingDiffView>(`/listing-diffs/${encodeURIComponent(diffId)}/dismiss`, {}, { idempotent: true }),
};
