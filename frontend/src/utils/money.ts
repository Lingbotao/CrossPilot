/**
 * 金额格式化（★ C4 约束的前端侧落地）。
 *
 * ## 为什么不能用 Number / parseFloat
 *
 * 后端金额是 `NUMERIC(20,6)`，以字符串传输（如 `"12345678901234.567890"`）。
 * JS 的 `Number` 是 IEEE-754 双精度，安全整数上限只有 2^53-1 ≈ 9e15。
 * 一旦 `parseFloat`，会出现两类事故：
 *
 * 1. **大额订单直接丢位** —— `12345678901234.567890` 尾数会被抹掉；
 * 2. **累加误差** —— 成本/利润是多笔相加，0.1+0.2 !== 0.3 这类误差会逐级放大，
 *    最终与平台账单对不上（PRD 验收项 9 要求偏差 ≤2%）。
 *
 * 所以这里所有运算都是**纯字符串运算**：只做四舍五入、进位、加千分位。
 * 需要真正算术（乘法/汇率换算）时，一律交给后端计算 —— 前端只负责展示。
 */

/** ISO 4217 货币符号。缺省时回退为货币代码本身（如 `MYR`）。 */
export const CURRENCY_SYMBOLS: Readonly<Record<string, string>> = {
  CNY: '¥',
  USD: '$',
  EUR: '€',
  GBP: '£',
  JPY: '¥',
  HKD: 'HK$',
  SGD: 'S$',
  MYR: 'RM',
  THB: '฿',
  IDR: 'Rp',
  PHP: '₱',
  VND: '₫',
  TWD: 'NT$',
  BRL: 'R$',
  MXN: 'MX$',
  AUD: 'A$',
  CAD: 'C$',
  KRW: '₩',
};

const DECIMAL_PATTERN = /^-?\d+(\.\d+)?$/;

export class InvalidMoneyError extends Error {
  constructor(raw: string) {
    super(`无法解析的金额字符串：${JSON.stringify(raw)}`);
    this.name = 'InvalidMoneyError';
  }
}

/** 校验并去掉多余空白；不合法直接抛错（由调用方决定兜底策略）。 */
export function normalizeDecimalString(raw: string): string {
  const trimmed = raw.trim();
  if (!DECIMAL_PATTERN.test(trimmed)) {
    throw new InvalidMoneyError(raw);
  }
  // 去掉整数部分多余的前导零："007.10" → "7.10"；保留 "0.xx"
  const negative = trimmed.startsWith('-');
  const unsigned = negative ? trimmed.slice(1) : trimmed;
  const [intPart = '0', fracPart] = unsigned.split('.');
  const strippedInt = intPart.replace(/^0+(?=\d)/, '');
  const rebuilt = fracPart === undefined ? strippedInt : `${strippedInt}.${fracPart}`;
  return negative ? `-${rebuilt}` : rebuilt;
}

/** 整数字符串 +1（纯字符串进位，不经过 Number）。 */
function incrementIntegerString(intStr: string): string {
  const digits = intStr.split('');
  let i = digits.length - 1;
  while (i >= 0) {
    if (digits[i] === '9') {
      digits[i] = '0';
      i -= 1;
    } else {
      digits[i] = String(Number(digits[i]) + 1);
      return digits.join('');
    }
  }
  return `1${digits.join('')}`;
}

const ZERO_PATTERN = /^0(\.0*)?$/;

function isZero(value: string): boolean {
  return ZERO_PATTERN.test(value);
}

/**
 * 四舍五入到指定小数位，返回定长字符串。
 *
 * 舍入规则：**按绝对值 half-up，正负对称** —— ``0.005 → 0.01``、``-0.005 → -0.01``。
 * 财务口径上这是通行做法；「负零」（``-0.001 → -0.00``）会被收敛成 ``0.00``，
 * 避免界面出现没有意义的 ``-0.00``。
 *
 * 只处理"舍入"这一个操作，不做任何浮点运算 —— 逐位比较与逐位进位。
 */
export function roundDecimalString(value: string, decimals = 2): string {
  if (!Number.isInteger(decimals) || decimals < 0) {
    throw new RangeError(`decimals 必须是非负整数，收到 ${decimals}`);
  }

  const normalized = normalizeDecimalString(value);
  const negative = normalized.startsWith('-');
  const unsigned = negative ? normalized.slice(1) : normalized;

  const dotIndex = unsigned.indexOf('.');
  const intPart = dotIndex === -1 ? unsigned : unsigned.slice(0, dotIndex);
  const fracRaw = dotIndex === -1 ? '' : unsigned.slice(dotIndex + 1);

  // 多取一位用于判断进位
  const frac = fracRaw.padEnd(decimals + 1, '0');
  const kept = frac.slice(0, decimals);
  const roundUp = Number(frac.charAt(decimals)) >= 5;

  let intOut = intPart;
  let fracOut = kept;

  if (roundUp) {
    if (decimals === 0) {
      intOut = incrementIntegerString(intPart);
    } else {
      const digits = kept.split('');
      let carry = true;
      for (let i = digits.length - 1; i >= 0 && carry; i -= 1) {
        if (digits[i] === '9') {
          digits[i] = '0';
        } else {
          digits[i] = String(Number(digits[i]) + 1);
          carry = false;
        }
      }
      fracOut = digits.join('');
      if (carry) {
        intOut = incrementIntegerString(intPart);
      }
    }
  }

  const body = decimals === 0 ? intOut : `${intOut}.${fracOut}`;
  // "-0.00" 这种没有意义的负零统一收敛成 "0.00"
  return negative && !isZero(body) ? `-${body}` : body;
}

/** 给整数部分加千分位（对纯数字字符串做正则分组，不转 Number）。 */
export function groupThousands(intStr: string): string {
  return intStr.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
}

export interface FormatMoneyOptions {
  currency?: string;
  /** 展示小数位。财务口径一般 2 位；成本明细可用 4 位 */
  decimals?: number;
  showSymbol?: boolean;
  showCode?: boolean;
  grouping?: boolean;
  /** 金额为空/非法时的占位符 */
  fallback?: string;
}

/**
 * 把后端返回的 Decimal 字符串格式化为可读金额。
 *
 * @example
 * formatMoney('12345678901234.567890')             // '¥12,345,678,901,234.57'
 * formatMoney('0.100000', { decimals: 4 })         // '¥0.1000'
 * formatMoney('-3.5', { currency: 'USD' })         // '-$3.50'
 */
export function formatMoney(value: string | null | undefined, options: FormatMoneyOptions = {}): string {
  const {
    currency = 'CNY',
    decimals = 2,
    showSymbol = true,
    showCode = false,
    grouping = true,
    fallback = '—',
  } = options;

  if (value === null || value === undefined || value === '') {
    return fallback;
  }

  let rounded: string;
  try {
    rounded = roundDecimalString(value, decimals);
  } catch (error) {
    // 金额解析失败意味着后端契约被破坏，必须留痕而不是静默显示占位符
    console.error('[money] 金额格式非法，已降级显示占位符', { value, error });
    return fallback;
  }

  const negative = rounded.startsWith('-');
  const unsigned = negative ? rounded.slice(1) : rounded;
  const [intPart = '0', fracPart] = unsigned.split('.');

  const intDisplay = grouping ? groupThousands(intPart) : intPart;
  const numberPart = fracPart === undefined ? intDisplay : `${intDisplay}.${fracPart}`;
  const symbol = showSymbol ? (CURRENCY_SYMBOLS[currency] ?? '') : '';
  const codeSuffix = showCode ? ` ${currency}` : '';

  return `${negative ? '-' : ''}${symbol}${numberPart}${codeSuffix}`;
}

/** 拼接「金额 + 币种」用于导出/复制，不做符号替换，保证可再解析。 */
export function toPlainMoney(value: string | null | undefined, decimals = 2, fallback = ''): string {
  if (value === null || value === undefined || value === '') {
    return fallback;
  }
  try {
    return roundDecimalString(value, decimals);
  } catch {
    return fallback;
  }
}

/** 是否为负金额（用于涨跌/亏损染色）。 */
export function isNegativeMoney(value: string | null | undefined): boolean {
  if (!value) return false;
  try {
    return normalizeDecimalString(value).startsWith('-');
  } catch {
    return false;
  }
}
