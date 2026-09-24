/**
 * 金额展示组件。
 *
 * 两条硬约定：
 * 1. **入参是后端返回的 Decimal 字符串**，组件内部全程做字符串运算，
 *    绝不 `parseFloat`（理由见 `utils/money.ts` 顶部说明）；
 * 2. 涉及成本/利润的金额，调用方必须先过 `<CostGuard>` ——
 *    F4 规定这类数据对 OPS_STAFF 默认不可见。
 */

import { InfoCircleOutlined } from '@ant-design/icons';
import { Skeleton, Tooltip, Typography } from 'antd';
import type { CSSProperties, ReactNode } from 'react';

import { formatMoney, isNegativeMoney } from '@/utils/money';

export interface ExchangeRateHint {
  from: string;
  to: string;
  rate: string;
  source?: string;
  effective_date?: string;
}

export interface MoneyTextProps {
  /** 后端 Decimal 字符串，如 "12.340000" */
  value: string | null | undefined;
  currency?: string;
  /** 成本类金额建议用 4 位，避免小额被四舍五入成 0 */
  decimals?: number;
  showSymbol?: boolean;
  showCode?: boolean;
  /**
   * 按正负染色。★ 中国市场习惯：**涨/盈利为红，跌/亏损为绿**（与欧美相反）。
   */
  colorize?: boolean;
  /** 汇率提示：有值时会展示一个 ⓘ 图标，鼠标悬停显示换算来源与生效日期 */
  rate?: ExchangeRateHint | null;
  /** 是否强调（加粗、放大一号），用于卡片主指标 */
  strong?: boolean;
  loading?: boolean;
  prefix?: ReactNode;
  suffix?: ReactNode;
  style?: CSSProperties;
  className?: string;
}

const COLOR_PROFIT = '#cf1322'; // 红 —— 中国习惯的"涨/盈利"
const COLOR_LOSS = '#3f8600'; // 绿 —— "跌/亏损"

export function MoneyText({
  value,
  currency = 'CNY',
  decimals = 2,
  showSymbol = true,
  showCode = false,
  colorize = false,
  rate = null,
  strong = false,
  loading = false,
  prefix,
  suffix,
  style,
  className,
}: MoneyTextProps) {
  if (loading) {
    return <Skeleton.Input active size="small" style={{ width: 88, minWidth: 88 }} />;
  }

  const text = formatMoney(value, { currency, decimals, showSymbol, showCode });

  let color: string | undefined;
  if (colorize && value !== null && value !== undefined && value !== '') {
    color = isNegativeMoney(value) ? COLOR_LOSS : COLOR_PROFIT;
  }

  const content = (
    <Typography.Text
      className={className}
      strong={strong}
      style={{ color, fontVariantNumeric: 'tabular-nums', ...style }}
    >
      {prefix}
      {text}
      {suffix}
    </Typography.Text>
  );

  if (!rate) {
    return content;
  }

  const rateTip = (
    <div style={{ fontSize: 12, lineHeight: 1.7 }}>
      <div>
        汇率 1 {rate.from} = {rate.rate} {rate.to}
      </div>
      {rate.source ? <div>来源：{rate.source}</div> : null}
      {rate.effective_date ? <div>生效日期：{rate.effective_date}</div> : null}
      <div style={{ opacity: 0.75 }}>换算由后端完成，前端仅展示</div>
    </div>
  );

  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
      {content}
      <Tooltip title={rateTip}>
        <InfoCircleOutlined style={{ color: 'rgba(0,0,0,0.35)', fontSize: 12, cursor: 'help' }} />
      </Tooltip>
    </span>
  );
}

export default MoneyText;
