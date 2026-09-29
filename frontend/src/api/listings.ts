/** Listing 映射与类目模板。 */

import { api } from './client';
import type {
  CategoryMappingPatch,
  CategoryMappingQuery,
  CategoryMappingView,
  CategoryMappingWrite,
  ListingPatch,
  ListingQuery,
  ListingShopOption,
  ListingView,
  ListingWrite,
  PageData,
  PlatformSiteCatalog,
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
};
