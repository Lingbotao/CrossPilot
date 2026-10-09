/** 多语言文案、术语库和敏感词。 */

import { api } from './client';
import type {
  GlossaryTermView,
  GlossaryTermWrite,
  ListingContentQuery,
  ListingContentView,
  ListingContentWrite,
  MachineDraftWrite,
  PageData,
  SensitiveHit,
  SensitiveScanWrite,
  SensitiveTermView,
  SensitiveTermWrite,
} from './types';

export const localeApi = {
  listContents: (query: ListingContentQuery) =>
    api.get<PageData<ListingContentView>>('/listing-contents', { params: query }),

  saveContent: (payload: ListingContentWrite) =>
    api.post<ListingContentView>('/listing-contents', payload, { idempotent: true }),

  machineDraft: (payload: MachineDraftWrite) =>
    api.post<ListingContentView>('/listing-contents/machine-draft', payload, { idempotent: true }),

  publishContent: (contentId: string) =>
    api.post<ListingContentView>(`/listing-contents/${encodeURIComponent(contentId)}/publish`, {}, { idempotent: true }),

  removeContent: (contentId: string) =>
    api.delete<ListingContentView>(`/listing-contents/${encodeURIComponent(contentId)}`, { idempotent: true }),

  scan: (payload: SensitiveScanWrite) => api.post<SensitiveHit[]>('/listing-contents/scan', payload),

  listGlossary: (query: { cursor?: string; limit?: number; q?: string }) =>
    api.get<PageData<GlossaryTermView>>('/glossary-terms', { params: query }),

  createGlossary: (payload: GlossaryTermWrite) =>
    api.post<GlossaryTermView>('/glossary-terms', payload, { idempotent: true }),

  removeGlossary: (termId: string) =>
    api.delete<GlossaryTermView>(`/glossary-terms/${encodeURIComponent(termId)}`, { idempotent: true }),

  listSensitive: (query: { cursor?: string; limit?: number; market?: string; lang?: string }) =>
    api.get<PageData<SensitiveTermView>>('/sensitive-terms', { params: query }),

  createSensitive: (payload: SensitiveTermWrite) =>
    api.post<SensitiveTermView>('/sensitive-terms', payload, { idempotent: true }),

  removeSensitive: (termId: string) =>
    api.delete<SensitiveTermView>(`/sensitive-terms/${encodeURIComponent(termId)}`, { idempotent: true }),
};
