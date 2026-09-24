/**
 * 中文界面文案（V1 只做中文界面 —— PRD 明确把"非中文界面"列为 Out of Scope）。
 *
 * 之所以仍然抽成字典：一是避免同一个词在不同页面出现不同翻译（如"店铺"/"商店"），
 * 二是将来加多语言时不用全项目搜字符串。
 */

export const zhCN = {
  app: {
    name: 'CrossPilot',
    slogan: '跨境电商多平台运营中台',
  },

  common: {
    confirm: '确定',
    cancel: '取消',
    save: '保存',
    search: '查询',
    reset: '重置',
    export: '导出',
    loading: '加载中…',
    empty: '暂无数据',
    noPermission: '无权限查看',
    total: '共 {{count}} 条',
    actions: '操作',
    all: '全部',
    required: '此项必填',
    retry: '重试',
  },

  auth: {
    login: '登录',
    logout: '退出登录',
    email: '邮箱',
    password: '密码',
    tenantCode: '租户标识',
    loginSuccess: '登录成功',
    sessionExpired: '登录状态已失效，请重新登录',
  },

  roles: {
    OWNER: '所有者',
    ADMIN: '管理员',
    OPS_MANAGER: '运营主管',
    OPS_STAFF: '运营专员',
    PURCHASER: '采购',
    FINANCE: '财务',
    CS: '客服',
    VIEWER: '只读访客',
  },

  cost: {
    /** ★ F4：该提示会出现在所有成本相关入口 */
    restricted: '当前角色不可见采购成本价与利润（PRD 4.3 关键设计决策）',
    purchasePrice: '采购成本价',
    landedCost: '落地成本',
    grossProfit: '毛利',
    netProfit: '净利润',
    margin: '毛利率',
  },

  order: {
    unifiedStatus: {
      PENDING_PAYMENT: '待付款',
      PAID: '已付款',
      TO_SHIP: '待发货',
      SHIPPED: '已发货',
      IN_TRANSIT: '运输中',
      DELIVERED: '已签收',
      CANCELLED: '已取消',
      RETURNING: '退货中',
      REFUNDED: '已退款',
    },
  },

  sync: {
    running: '同步中',
    success: '同步成功',
    failed: '同步失败',
    rateLimited: '触发平台限流，正在退避重试',
    successRate: '同步成功率',
  },
} as const;

export type ZhCN = typeof zhCN;
export default zhCN;
