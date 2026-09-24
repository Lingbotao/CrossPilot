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

  menu: {
    dashboard: '数据看板',
    shops: '店铺授权',
    orders: '订单管理',
    products: '商品库',
    inventory: '库存管理',
    compliance: '合规与算账',
    purchase: '采购管理',
    finance: '财务与利润',
    members: '成员管理',
    roles: '角色与权限',
    audit: '审计日志',
  },

  auth: {
    login: '登录',
    register: '注册新租户',
    logout: '退出登录',
    email: '邮箱',
    password: '密码',
    confirmPassword: '确认密码',
    displayName: '姓名',
    tenantCode: '租户标识',
    tenantName: '企业名称',
    tenantCodeHelp: '3–64 位小写字母、数字或连字符，用于唯一标识企业',
    passwordRule: '至少 10 位，大写字母、小写字母、数字、特殊字符中至少包含三类',
    createTenant: '创建租户',
    haveAccount: '已有账号？',
    noAccount: '还没有账号？',
    registerFailed: '注册失败，请稍后重试',
    verifyEmail: '验证邮箱',
    verifyHint: '开发 outbox 已生成验证链接，请从 API 日志复制链接完成验证。',
    verifying: '正在验证邮箱…',
    verifySuccess: '邮箱验证成功',
    verifySuccessHint: '账号已启用，现在可以登录。',
    emailNotVerified: '邮箱尚未验证；完成验证后才能绑定店铺。',
    verifyFailed: '邮箱验证失败',
    resendVerification: '重新发送验证邮件',
    resendSuccess: '验证邮件已重新发送',
    goLogin: '前往登录',
    backLogin: '返回登录',
    loginSuccess: '登录成功',
    sessionExpired: '登录状态已失效，请重新登录',
  },

  validation: {
    emailRequired: '请输入邮箱',
    emailInvalid: '邮箱格式不正确',
    passwordRequired: '请输入密码',
    confirmPasswordRequired: '请再次输入密码',
    passwordMismatch: '两次输入的密码不一致',
    tenantNameRequired: '请输入企业名称',
    tenantCodeRequired: '请输入租户标识',
    tenantCodeInvalid: '租户标识格式不正确',
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

  members: {
    title: '成员管理',
    description: '邀请成员、分配角色并限制可访问的数据范围。',
    activeMembers: '成员',
    pendingInvitations: '待接受邀请',
    invite: '邀请成员',
    sendInvite: '发送邀请',
    acceptInvitation: '接受成员邀请',
    joinTenant: '加入企业',
    acceptSuccess: '已成功加入企业',
    acceptFailed: '接受邀请失败',
    invalidInvitationLink: '邀请链接无效，缺少令牌。',
    role: '角色',
    status: '状态',
    active: '正常',
    disabled: '已停用',
    joinedAt: '加入时间',
    expiresAt: '过期时间',
    revoke: '撤销邀请',
    remove: '移除',
    removeFailed: '移除成员失败',
    removeConfirmTitle: '移除成员',
    removeConfirmHint: '这是敏感操作。请输入当前登录账号的密码进行二次确认。',
    confirmRemove: '确认移除',
    dataScope: '数据范围',
    shopScope: '店铺范围',
    scopeAll: '全部店铺',
    scopeSelected: '指定店铺',
    scopeNone: '无店铺权限',
    shopIds: '店铺 ID',
    shopIdsHelp: '输入 Snowflake ID，多个 ID 用逗号或空格分隔',
  },

  rolePage: {
    title: '角色与权限',
    description: '查看系统内置角色的权限矩阵。内置角色不可编辑或删除。',
    builtIn: '系统内置',
    descriptionLabel: '说明',
    permissions: '权限点',
  },

  audit: {
    title: '审计日志',
    description: '查询登录、授权、删除、导出及批量操作留痕。审计记录不可修改或删除。',
    action: '动作',
    resource: '资源',
    resourceId: '资源 ID',
    userId: '用户 ID',
    requestId: '追踪号',
    time: '时间',
    timeRange: '时间范围',
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
