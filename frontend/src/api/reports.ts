import { api, http } from './client';
import type { ReportExportCreate, ReportExportView } from './types';

export const reportsApi = {
  create: (payload: ReportExportCreate, confirmationToken: string) =>
    api.post<ReportExportView>('/reports/export', payload, {
      idempotent: true,
      headers: { 'X-Confirmation-Token': confirmationToken },
    }),

  get: (exportId: string) => api.get<ReportExportView>(`/reports/exports/${encodeURIComponent(exportId)}`),

  download: async (exportId: string) => {
    const response = await http.get<Blob>(`/reports/exports/${encodeURIComponent(exportId)}/file`, {
      responseType: 'blob',
      skipErrorToast: true,
    });
    const header = String(response.headers['content-disposition'] ?? '');
    const matched = /filename="([^"]+)"/.exec(header);
    const filename = matched?.[1] ?? 'sku-profit.csv';
    const url = URL.createObjectURL(response.data);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = filename;
    anchor.click();
    URL.revokeObjectURL(url);
  },
};
