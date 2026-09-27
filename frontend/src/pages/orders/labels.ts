import zhCN from '@/i18n/zh-CN';

const copy = zhCN.orderPage;

export function reviewLabel(code: string) {
  const labels: Record<string, string> = {
    AUTO_PASSED: copy.reviewAuto,
    PENDING: copy.reviewPending,
    APPROVED: copy.reviewApproved,
    REJECTED: copy.reviewRejected,
  };
  return labels[code] ?? code;
}

export function exceptionLabel(code: string) {
  const labels: Record<string, string> = {
    SKU_UNMATCHED: copy.skuUnmatched,
    ADDRESS_INVALID: copy.addressInvalid,
    SHIP_DUE_SOON: copy.shipDueSoon,
    SHIP_OVERDUE: copy.shipOverdue,
    REVIEW_PENDING: copy.reviewPending,
  };
  return labels[code] ?? code;
}
