/** 店铺授权与同步任务。 */

import { api } from './client';
import type {
  AuthUrlResponse,
  PageData,
  PlatformSiteCatalog,
  Shop,
  SyncModule,
  SyncTask,
  UnbindShopResponse,
} from './types';

export const shopsApi = {
  catalog: () => api.get<PlatformSiteCatalog[]>('/shops/catalog'),

  list: (page = 1, pageSize = 20) =>
    api.get<PageData<Shop>>('/shops', { params: { page, page_size: pageSize } }),

  authUrl: (platform: string, siteCode: string) =>
    api.post<AuthUrlResponse>(`/shops/${encodeURIComponent(platform)}/auth-url`, { site_code: siteCode }, {
      idempotent: true,
    }),

  callback: (platform: string, code: string, state: string) =>
    api.post<Shop>(`/shops/callback/${encodeURIComponent(platform)}`, { code, state }, { idempotent: true }),

  sync: (shopId: string, module: SyncModule) =>
    api.post<SyncTask>(`/shops/${encodeURIComponent(shopId)}/sync`, { module }, { idempotent: true }),

  unbind: (shopId: string, confirmationToken: string) =>
    api.delete<UnbindShopResponse>(`/shops/${encodeURIComponent(shopId)}`, {
      headers: { 'X-Confirmation-Token': confirmationToken },
      idempotent: true,
    }),
};

export const syncTasksApi = {
  list: (shopId?: string) =>
    api.get<PageData<SyncTask>>('/sync-tasks', { params: { shop_id: shopId, limit: 20 } }),
};
