import type { Dayjs } from 'dayjs';
import dayjs from 'dayjs';

export const ANALYTICS_PLATFORMS = [
  { value: 'amazon', label: 'Amazon' },
  { value: 'shopee', label: 'Shopee' },
  { value: 'lazada', label: 'Lazada' },
  { value: 'tiktok', label: 'TikTok Shop' },
] as const;

export function defaultAnalyticsRange(): [Dayjs, Dayjs] {
  const end = dayjs();
  return [end.subtract(6, 'day'), end];
}

export interface AnalyticsFilterValues {
  range: [Dayjs, Dayjs];
  shop_id?: string;
  platform_code?: string;
}

export function analyticsQuery(values: AnalyticsFilterValues) {
  return {
    date_from: values.range[0].format('YYYY-MM-DD'),
    date_to: values.range[1].format('YYYY-MM-DD'),
    shop_id: values.shop_id?.trim() || undefined,
    platform_code: values.platform_code || undefined,
  };
}

export function percentLabel(value: string | null | undefined): string {
  return value ? `${value}%` : '—';
}
