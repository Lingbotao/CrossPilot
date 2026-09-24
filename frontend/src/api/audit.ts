/** M1 审计日志只读查询接口。 */

import { api } from './client';
import type { AuditLog, AuditLogQuery, PageData } from './types';

export const auditApi = {
  list: (query: AuditLogQuery = {}) =>
    api.get<PageData<AuditLog>>('/audit-logs', {
      params: query,
    }),
};
