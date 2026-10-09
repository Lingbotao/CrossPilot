/** HS 编码检索与商品绑定。 */

import { api } from './client';
import type { HsBindRequest, HsCodeHit, SpuHsBindingView } from './types';

export const complianceApi = {
  searchHsCodes: (query: { q?: string; spu_id?: string; limit?: number }) =>
    api.get<HsCodeHit[]>('/hs-codes/search', { params: query }),

  bindings: (spuId: string) =>
    api.get<SpuHsBindingView[]>(`/spus/${encodeURIComponent(spuId)}/hs-bindings`),

  bindHsCode: (spuId: string, payload: HsBindRequest) =>
    api.post<SpuHsBindingView>(`/spus/${encodeURIComponent(spuId)}/hs-code`, payload, { idempotent: true }),
};
