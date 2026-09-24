/**
 * 与后端契约一一对应的类型定义（PRD 10.1）。
 *
 * ⚠️ 这里的错误码必须与 ``backend/app/core/errors.py`` 的 ``ErrorCode`` 保持一致。
 * 后端是唯一事实来源；本文件只是它的镜像。发现不一致时**改这里**，不要改后端。
 * （M1 起会由后端导出 OpenAPI 自动生成，届时本文件改为生成产物。）
 */

/** 统一响应信封：所有接口都返回这个结构，`code === 0` 表示成功。 */
export interface ApiResponse<T> {
  code: number;
  message: string;
  data: T | null;
  trace_id: string | null;
  timestamp: string;
}

/** 分页元信息：游标分页填 cursor/has_more；页码分页额外填 total/page/page_size。 */
export interface PageInfo {
  cursor: string | null;
  has_more: boolean;
  total: number | null;
  page: number | null;
  page_size: number | null;
}

export interface PageData<T> {
  items: T[];
  page_info: PageInfo;
}

/** 错误码段位（前 2 位为模块）。 */
export const ErrorCode = {
  OK: 0,

  // 10xxx 通用
  PARAM_INVALID: 10001,
  RESOURCE_NOT_FOUND: 10002,
  OPERATION_CONFLICT: 10003,
  TOO_MANY_REQUESTS: 10004,
  IDEMPOTENCY_CONFLICT: 10005,
  CONFIRMATION_REQUIRED: 10006,
  CONFIRMATION_INVALID: 10007,
  INTERNAL_ERROR: 10099,

  // 20xxx 认证与租户
  UNAUTHENTICATED: 20001,
  PERMISSION_DENIED: 20002,
  TENANT_DISABLED: 20003,
  ACCOUNT_LOCKED: 20004,
  CROSS_TENANT_DENIED: 20005,
  TOKEN_EXPIRED: 20006,
  EMAIL_NOT_VERIFIED: 20007,
  EMAIL_ALREADY_REGISTERED: 20008,
  TENANT_CODE_TAKEN: 20009,
  VERIFICATION_EXPIRED: 20010,
  INVITATION_EXPIRED: 20011,
  INVITATION_INVALID: 20012,
  LAST_ADMIN_PROTECTED: 20013,

  // 30xxx 平台与同步
  PLATFORM_UNSUPPORTED: 30001,
  GRANT_FAILED: 30002,
  PLATFORM_RATE_LIMITED: 30003,
  PLATFORM_API_ERROR: 30004,
  PLATFORM_CREDENTIAL_EXPIRED: 30005,
  SYNC_TASK_FAILED: 30006,

  // 40xxx 商品
  SKU_CODE_DUPLICATED: 40001,
  COMPLIANCE_CHECK_FAILED: 40002,
  LISTING_STATE_INVALID: 40003,
  /** 店铺授权过期 —— 前端据此引导用户重新授权 */
  SHOP_GRANT_EXPIRED: 40201,

  // 50xxx 订单
  ORDER_NOT_FOUND: 50001,
  ORDER_STATE_INVALID: 50002,
  BATCH_SHIP_PARTIAL_FAILED: 50003,

  // 60xxx 库存
  INVENTORY_INSUFFICIENT: 60001,
  INVENTORY_SYNC_FAILED: 60002,
  INVENTORY_CONFLICT: 60003,

  // 70xxx 财务
  EXCHANGE_RATE_MISSING: 70001,
  SETTLEMENT_MISMATCH: 70002,
  LANDED_COST_PARAM_MISSING: 70003,

  // 80xxx 合规
  HS_CODE_MISSING: 80001,
  CERTIFICATE_EXPIRED: 80002,
  MARKET_RESTRICTED: 80003,
} as const;

export type ErrorCodeValue = (typeof ErrorCode)[keyof typeof ErrorCode];

/** 角色码（与 backend/app/core/permissions.py 的 RoleCode 对齐）。 */
export const RoleCode = {
  OWNER: 'OWNER',
  ADMIN: 'ADMIN',
  OPS_MANAGER: 'OPS_MANAGER',
  OPS_STAFF: 'OPS_STAFF',
  PURCHASER: 'PURCHASER',
  FINANCE: 'FINANCE',
  CS: 'CS',
  VIEWER: 'VIEWER',
} as const;

export type RoleCodeValue = (typeof RoleCode)[keyof typeof RoleCode];

/** 权限点（与后端的 Perm 枚举对齐）。前端只用于渲染控制，不是安全边界。 */
export const Perm = {
  TENANT_READ: 'tenant:read',
  TENANT_WRITE: 'tenant:write',
  MEMBER_READ: 'member:read',
  MEMBER_WRITE: 'member:write',
  SHOP_READ: 'shop:read',
  SHOP_GRANT: 'shop:grant',
  PRODUCT_READ: 'product:read',
  PRODUCT_WRITE: 'product:write',
  ORDER_READ: 'order:read',
  ORDER_SHIP: 'order:ship',
  INVENTORY_READ: 'inventory:read',
  INVENTORY_WRITE: 'inventory:write',
  PURCHASE_READ: 'purchase:read',
  PURCHASE_WRITE: 'purchase:write',
  COST_READ: 'cost:read',
  ADS_READ: 'ads:read',
  COMPLIANCE_READ: 'compliance:read',
  COMPLIANCE_WRITE: 'compliance:write',
  LANDED_COST_READ: 'landed_cost:read',
  LANDED_COST_CALC: 'landed_cost:calc',
  FINANCE_READ: 'finance:read',
  REPORT_EXPORT: 'report:export',
  DASHBOARD_READ: 'dashboard:read',
  AUDIT_READ: 'audit:read',
  SYSTEM_READ: 'system:read',
  SYSTEM_WRITE: 'system:write',
} as const;

export type PermValue = (typeof Perm)[keyof typeof Perm];

/** ---------- 认证与租户 ---------- */

export interface TenantBrief {
  id: string;
  code: string;
  name: string;
  role_code: RoleCodeValue;
}

export interface LoginRequest {
  email: string;
  password: string;
  /** 用户属于多个租户时必填 */
  tenant_code?: string;
}

export interface LoginResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
  tenant: TenantBrief;
  available_tenants: TenantBrief[];
}

export interface RefreshResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

export interface CurrentUser {
  id: string;
  email: string;
  display_name: string | null;
  avatar_url: string | null;
  last_login_at: string | null;
  email_verified_at: string | null;
}

export interface TenantCurrent {
  id: string;
  code: string;
  name: string;
  plan: number;
  status: number;
  default_currency: string;
  timezone: string;
  role_code: RoleCodeValue;
  role_name: string;
  /** 权限点列表，供 PermissionGuard 使用 */
  permissions: string[];
  /** ★ F4：是否可见采购成本价与利润 */
  can_view_cost: boolean;
  data_scope: Record<string, { scope_type: number; shop_ids: string[] }>;
}

export interface MeResponse {
  user: CurrentUser;
  tenant: TenantCurrent;
}

/** ---------- M1 认证、成员、角色与审计 ---------- */

export interface RegisterRequest {
  email: string;
  password: string;
  tenant_code: string;
  tenant_name: string;
  display_name?: string;
}

export interface RegisterResponse {
  tenant: TenantBrief;
  verification_sent: boolean;
}

export interface VerifyEmailRequest {
  token: string;
}

export interface ResendVerificationRequest {
  email: string;
}

export interface ConfirmPasswordRequest {
  password: string;
  action: string;
}

export interface ConfirmPasswordResponse {
  confirmation_token: string;
  expires_in: number;
}

export const MemberStatus = {
  INVITED: 1,
  ACTIVE: 2,
  DISABLED: 3,
} as const;

export type MemberStatusValue = (typeof MemberStatus)[keyof typeof MemberStatus];

export const InvitationStatus = {
  PENDING: 1,
  ACCEPTED: 2,
  EXPIRED: 3,
  REVOKED: 4,
} as const;

export type InvitationStatusValue = (typeof InvitationStatus)[keyof typeof InvitationStatus];

export const DataScopeType = {
  ALL: 1,
  SELECTED: 2,
  NONE: 3,
} as const;

export type DataScopeTypeValue = (typeof DataScopeType)[keyof typeof DataScopeType];

export const ResourceType = {
  SHOP: 1,
  WAREHOUSE: 2,
  SUPPLIER: 3,
} as const;

export type ResourceTypeValue = (typeof ResourceType)[keyof typeof ResourceType];

export interface DataScope {
  scope_type: DataScopeTypeValue;
  shop_ids: string[];
}

export interface Member {
  id: string;
  user_id: string;
  email: string;
  display_name: string | null;
  role_code: RoleCodeValue;
  status: MemberStatusValue;
  joined_at: string | null;
  data_scope: Record<string, DataScope>;
}

export interface MemberInvitation {
  id: string;
  email: string;
  role_code: RoleCodeValue;
  expires_at: string;
  status: InvitationStatusValue;
}

export interface MemberListResponse {
  members: Member[];
  invitations: MemberInvitation[];
}

export interface InviteMemberRequest {
  email: string;
  role_code: Exclude<RoleCodeValue, 'OWNER'>;
}

export interface AcceptInvitationRequest {
  token: string;
  password: string;
  display_name?: string;
}

export interface AcceptedInvitationResponse {
  tenant_id: string;
  member_id: string;
}

export interface UpdateMemberRoleRequest {
  role_code: Exclude<RoleCodeValue, 'OWNER'>;
}

export interface UpdateDataScopeRequest {
  resource_type: ResourceTypeValue;
  scope_type: DataScopeTypeValue;
  /** Snowflake ID 必须以字符串传递，禁止转成 JS number。 */
  shop_ids: string[];
}

export interface Role {
  code: RoleCodeValue;
  name: string;
  is_system: boolean;
  permission_codes: string[];
  description: string | null;
}

export interface AuditLog {
  id: string;
  created_at: string;
  user_id: string | null;
  action: string;
  resource: string;
  resource_id: string | null;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  ip: string | null;
  request_id: string | null;
}

export interface AuditLogQuery {
  cursor?: string;
  limit?: number;
  action?: string;
  resource?: string;
  user_id?: string;
  created_from?: string;
  created_to?: string;
}
