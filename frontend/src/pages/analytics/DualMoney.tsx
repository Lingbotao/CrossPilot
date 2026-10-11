import { Tooltip } from 'antd';
import type { ReactNode } from 'react';

import { MoneyText } from '@/components/MoneyText';
import zhCN from '@/i18n/zh-CN';

const copy = zhCN.analyticsPage;

/** 原币与记账本位币并排。汇率在汇总时已经折进本位币，页面不反推。 */
export function DualMoney({
  value,
  currency,
  bookValue,
  bookCurrency,
  colorize = false,
}: {
  value: string | null;
  currency: string;
  bookValue: string | null;
  bookCurrency: string;
  colorize?: boolean;
}) {
  const same = currency === bookCurrency;
  let book: ReactNode = null;
  if (!same && bookValue) {
    book = (
      <div>
        <MoneyText value={bookValue} currency={bookCurrency} colorize={colorize} />
      </div>
    );
  } else if (!same) {
    book = (
      <div>
        <span style={{ color: 'rgba(0,0,0,0.45)', fontSize: 12 }}>{copy.bookMissing}</span>
      </div>
    );
  }
  return (
    <Tooltip title={same ? copy.sameCurrency : copy.fxHint}>
      <span>
        <MoneyText value={value} currency={currency} colorize={colorize} />
        {book}
      </span>
    </Tooltip>
  );
}
