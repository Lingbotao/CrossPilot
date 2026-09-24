/**
 * 金额工具测试（前端 CI 门禁的一部分）。
 *
 * 重点覆盖 **C4 约束**：金额全程不能经过 Number/parseFloat，
 * 金额超过 2^53-1 时尾数不能丢。
 */

import { describe, expect, it, vi } from 'vitest';

import {
  formatMoney,
  groupThousands,
  isNegativeMoney,
  normalizeDecimalString,
  roundDecimalString,
  toPlainMoney,
} from '@/utils/money';

describe('normalizeDecimalString', () => {
  it('去掉前导零但保留 0.xx', () => {
    expect(normalizeDecimalString('007.10')).toBe('7.10');
    expect(normalizeDecimalString('0.10')).toBe('0.10');
    expect(normalizeDecimalString('000')).toBe('0');
  });

  it('保留负号', () => {
    expect(normalizeDecimalString('-004.5')).toBe('-4.5');
  });

  it('拒绝非法输入（不静默转 0）', () => {
    expect(() => normalizeDecimalString('12.3.4')).toThrow();
    expect(() => normalizeDecimalString('abc')).toThrow();
    expect(() => normalizeDecimalString('')).toThrow();
  });
});

describe('roundDecimalString（纯字符串四舍五入）', () => {
  it('基本四舍五入', () => {
    expect(roundDecimalString('1.005', 2)).toBe('1.01');
    expect(roundDecimalString('1.004', 2)).toBe('1.00');
    expect(roundDecimalString('2.5', 0)).toBe('3');
    expect(roundDecimalString('2.4', 0)).toBe('2');
  });

  it('进位能穿透整数部分', () => {
    expect(roundDecimalString('9.99', 1)).toBe('10.0');
    expect(roundDecimalString('99.999', 2)).toBe('100.00');
    expect(roundDecimalString('9.5', 0)).toBe('10');
  });

  it('★ 超出 JS 安全整数范围仍不丢精度', () => {
    // 2^53 - 1 = 9007199254740991；下面这个数远超它
    expect(roundDecimalString('12345678901234.567890', 2)).toBe('12345678901234.57');
    expect(roundDecimalString('9007199254740993.005', 2)).toBe('9007199254740993.01');
    expect(roundDecimalString('12345678901234567890.5', 0)).toBe('12345678901234567891');
  });

  it('负零收敛为 0（避免展示 "-0.00"）', () => {
    expect(roundDecimalString('-0.001', 2)).toBe('0.00');
    expect(roundDecimalString('-0.004', 2)).toBe('0.00');
    expect(roundDecimalString('-0.0000001', 6)).toBe('0.000000');
  });

  it('负数保留符号，且与正数对称（按绝对值 half-up）', () => {
    expect(roundDecimalString('-1.005', 2)).toBe('-1.01');
    // 与 '1.005' → '1.01' 必须对称：half-up 作用在绝对值上，不是"向零舍入"
    expect(roundDecimalString('-0.005', 2)).toBe('-0.01');
    expect(roundDecimalString('-9.99', 1)).toBe('-10.0');
    expect(roundDecimalString('-99.999', 2)).toBe('-100.00');
  });

  it('decimals 参数校验', () => {
    expect(() => roundDecimalString('1.23', -1)).toThrow(RangeError);
    expect(() => roundDecimalString('1.23', 1.5)).toThrow(RangeError);
  });
});

describe('groupThousands', () => {
  it('按千分位分组', () => {
    expect(groupThousands('1234567')).toBe('1,234,567');
    expect(groupThousands('999')).toBe('999');
    expect(groupThousands('1000')).toBe('1,000');
  });
});

describe('formatMoney', () => {
  it('带符号与千分位', () => {
    expect(formatMoney('12345678901234.567890')).toBe('¥12,345,678,901,234.57');
    expect(formatMoney('1234.5')).toBe('¥1,234.50');
  });

  it('货币符号与代码', () => {
    expect(formatMoney('3.5', { currency: 'USD' })).toBe('$3.50');
    expect(formatMoney('3.5', { currency: 'MYR' })).toBe('RM3.50');
    expect(formatMoney('3.5', { currency: 'USD', showCode: true })).toBe('$3.50 USD');
    // 未登记的币种不应展示错误符号，而是只显示数字
    expect(formatMoney('3.5', { currency: 'XYZ' })).toBe('3.50');
  });

  it('小数位可配（成本类用 4 位，避免小额被抹成 0）', () => {
    expect(formatMoney('0.100000', { decimals: 4 })).toBe('¥0.1000');
    expect(formatMoney('0.000400', { decimals: 4 })).toBe('¥0.0004');
  });

  it('空值返回占位符', () => {
    expect(formatMoney(null)).toBe('—');
    expect(formatMoney(undefined)).toBe('—');
    expect(formatMoney('')).toBe('—');
  });

  it('非法金额降级为占位符并留痕（不抛错导致白屏）', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    expect(formatMoney('not-a-number')).toBe('—');
    expect(spy).toHaveBeenCalled();
    spy.mockRestore();
  });

  it('可关闭千分位', () => {
    expect(formatMoney('1234567.89', { grouping: false })).toBe('¥1234567.89');
  });

  it('负数金额', () => {
    expect(formatMoney('-1234.5')).toBe('-¥1,234.50');
  });
});

describe('toPlainMoney / isNegativeMoney', () => {
  it('toPlainMoney 输出可再解析的纯数字串', () => {
    expect(toPlainMoney('1234.567')).toBe('1234.57');
    expect(toPlainMoney(null)).toBe('');
    expect(toPlainMoney('bad', 2, '—')).toBe('—');
  });

  it('isNegativeMoney 用于涨跌染色（中国习惯：涨红跌绿）', () => {
    expect(isNegativeMoney('-1.00')).toBe(true);
    expect(isNegativeMoney('1.00')).toBe(false);
    expect(isNegativeMoney('-0.001')).toBe(true);
    expect(isNegativeMoney(null)).toBe(false);
    expect(isNegativeMoney('bad')).toBe(false);
  });
});
