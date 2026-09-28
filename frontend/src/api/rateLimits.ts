/** 平台限流配额。读和改都要 system 权限。 */

import { api } from './client';
import type { PlatformRateLimit, UpsertPlatformRateLimit } from './types';

export const rateLimitApi = {
  list: () => api.get<PlatformRateLimit[]>('/platform-rate-limits'),
  save: (body: UpsertPlatformRateLimit) =>
    api.put<PlatformRateLimit>('/platform-rate-limits', body, { idempotent: true }),
};
