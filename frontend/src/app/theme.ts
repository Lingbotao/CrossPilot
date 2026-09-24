/** Ant Design 5 主题配置。 */

import type { ThemeConfig } from 'antd';

/** 品牌主色：偏商务的靛蓝，比默认 #1677ff 更沉稳，适合长时间盯表格的后台场景。 */
export const BRAND_PRIMARY = '#2f54eb';

export const appTheme: ThemeConfig = {
  token: {
    colorPrimary: BRAND_PRIMARY,
    borderRadius: 6,
    // 后台系统信息密度高，行高收紧一档
    fontSize: 14,
    controlHeight: 34,
    fontFamily:
      "-apple-system, BlinkMacSystemFont, 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', 'Helvetica Neue', Arial, sans-serif",
    // 金额/数字列大量出现，等宽数字必须开，否则表格里数字会左右跳动
    fontFamilyCode: "'SF Mono', Menlo, Consolas, monospace",
  },
  components: {
    Layout: {
      headerHeight: 56,
      headerPadding: '0 20px',
    },
    Menu: {
      itemHeight: 40,
    },
    Table: {
      // 多平台订单列表列多、行密，紧凑一点能少滚动
      cellPaddingBlock: 10,
      headerBg: '#fafafa',
    },
  },
};

/** 中国习惯的涨跌配色（与欧美相反）：涨 = 红，跌 = 绿。 */
export const PRICE_UP_COLOR = '#cf1322';
export const PRICE_DOWN_COLOR = '#3f8600';
