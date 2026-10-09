/** HS 编码、税率版本与认证台账。 */

import { api } from './client';
import type {
  CertGapView,
  CertRequirementCreate,
  CertRequirementView,
  CertificateCreate,
  CertificateImportResult,
  CertificateView,
  ComplianceAlertView,
  ComplianceReportView,
  HsBindRequest,
  HsCodeHit,
  SpuHsBindingView,
  TaxRuleCreate,
  TaxRuleView,
} from './types';

export const complianceApi = {
  searchHsCodes: (query: { q?: string; spu_id?: string; limit?: number }) =>
    api.get<HsCodeHit[]>('/hs-codes/search', { params: query }),

  bindings: (spuId: string) =>
    api.get<SpuHsBindingView[]>(`/spus/${encodeURIComponent(spuId)}/hs-bindings`),

  bindHsCode: (spuId: string, payload: HsBindRequest) =>
    api.post<SpuHsBindingView>(`/spus/${encodeURIComponent(spuId)}/hs-code`, payload, { idempotent: true }),

  taxRules: (query?: { country?: string; tax_type?: string }) =>
    api.get<TaxRuleView[]>('/tax-rules', { params: query }),

  createTaxRule: (payload: TaxRuleCreate) =>
    api.post<TaxRuleView>('/tax-rules', payload, { idempotent: true }),

  retireTaxRule: (ruleId: string) =>
    api.post<TaxRuleView>(`/tax-rules/${encodeURIComponent(ruleId)}/retire`, {}, { idempotent: true }),

  certificates: (query?: { sku_id?: string; spu_id?: string; market?: string }) =>
    api.get<CertificateView[]>('/certificates', { params: query }),

  createCertificate: (payload: CertificateCreate) =>
    api.post<CertificateView>('/certificates', payload, { idempotent: true }),

  importCertificates: (file: File) => {
    const body = new FormData();
    body.append('file', file);
    return api.post<CertificateImportResult>('/certificates/import', body, { idempotent: true, skipErrorToast: true });
  },

  uploadCertificate: (certificateId: string, file: File) => {
    const body = new FormData();
    body.append('file', file);
    return api.post<CertificateView>(`/certificates/${encodeURIComponent(certificateId)}/file`, body, {
      idempotent: true,
    });
  },

  gaps: (query: { sku_id: string; market: string; category_code: string }) =>
    api.get<CertGapView[]>('/certificates/gaps', { params: query }),

  requirements: (query?: { market?: string }) =>
    api.get<CertRequirementView[]>('/cert-requirements', { params: query }),

  createRequirement: (payload: CertRequirementCreate) =>
    api.post<CertRequirementView>('/cert-requirements', payload, { idempotent: true }),

  retireRequirement: (ruleId: string) =>
    api.post<CertRequirementView>(`/cert-requirements/${encodeURIComponent(ruleId)}/retire`, {}, { idempotent: true }),

  alerts: () => api.get<ComplianceAlertView[]>('/compliance-alerts'),

  report: () => api.get<ComplianceReportView>('/compliance-report'),
};
