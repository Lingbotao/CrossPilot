/** 通用格式化工具（日期、百分比、数字）。 */

import dayjs from 'dayjs';
import utcPlugin from 'dayjs/plugin/utc';
import timezonePlugin from 'dayjs/plugin/timezone';
import relativeTimePlugin from 'dayjs/plugin/relativeTime';
import 'dayjs/locale/zh-cn';

dayjs.extend(utcPlugin);
dayjs.extend(timezonePlugin);
dayjs.extend(relativeTimePlugin);
dayjs.locale('zh-cn');

/** 后端统一返回 UTC（ISO 8601 带时区），展示时再转用户时区。 */
export const DEFAULT_DISPLAY_TZ = 'Asia/Shanghai';

export function formatDateTime(
  value: string | null | undefined,
  format = 'YYYY-MM-DD HH:mm:ss',
  tz = DEFAULT_DISPLAY_TZ,
): string {
  if (!value) return '—';
  const parsed = dayjs(value);
  if (!parsed.isValid()) return '—';
  return parsed.tz(tz).format(format);
}

export function formatDate(value: string | null | undefined, tz = DEFAULT_DISPLAY_TZ): string {
  return formatDateTime(value, 'YYYY-MM-DD', tz);
}

export function fromNow(value: string | null | undefined): string {
  if (!value) return '—';
  const parsed = dayjs(value);
  return parsed.isValid() ? parsed.fromNow() : '—';
}

/** 时长（毫秒 → 人类可读）。同步耗时、接口延迟都用它。 */
export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return '—';
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  const minutes = Math.floor(ms / 60_000);
  const seconds = Math.round((ms % 60_000) / 1000);
  return `${minutes}m${seconds}s`;
}

/** 百分比。入参是后端算好的字符串（如 "0.1234" 表示 12.34%）。 */
export function formatPercent(
  value: string | number | null | undefined,
  options: { decimals?: number; alreadyScaled?: boolean } = {},
): string {
  const { decimals = 2, alreadyScaled = false } = options;
  if (value === null || value === undefined || value === '') return '—';
  const num = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(num)) return '—';
  const scaled = alreadyScaled ? num : num * 100;
  return `${scaled.toFixed(decimals)}%`;
}

export function formatCount(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—';
  return value.toLocaleString('zh-CN');
}

/** 大数字缩写（看板卡片用）：1284560 → 128.5万 */
export function abbreviateNumber(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—';
  const abs = Math.abs(value);
  if (abs >= 100_000_000) return `${(value / 100_000_000).toFixed(2)}亿`;
  if (abs >= 10_000) return `${(value / 10_000).toFixed(2)}万`;
  return value.toLocaleString('zh-CN');
}
