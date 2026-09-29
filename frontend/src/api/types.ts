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
  WEBHOOK_REJECTED: 30007,

  // 40xxx 商品
  SKU_CODE_DUPLICATED: 40001,
  COMPLIANCE_CHECK_FAILED: 40002,
  LISTING_STATE_INVALID: 40003,
  SPU_VARIANT_LIMIT: 40004,
  LISTING_PLATFORM_SKU_TAKEN: 40005,
  CATEGORY_TEMPLATE_DUPLICATED: 40006,
  /** 店铺授权过期 —— 前端据此引导用户重新授权 */
  SHOP_GRANT_EXPIRED: 40201,

  // 50xxx 订单
  ORDER_NOT_FOUND: 50001,
  ORDER_STATE_INVALID: 50002,
  BATCH_SHIP_PARTIAL_FAILED: 50003,
  ORDER_PLATFORM_REJECTED: 50005,
  RETURN_STATE_INVALID: 50006,

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
  ORDER_WRITE: 'order:write',
  ORDER_RULE: 'order:rule',
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

export type ShopHealth = 'green' | 'yellow' | 'red';
export type SyncModule = 'order' | 'product' | 'inventory';

export interface Shop {
  id: string;
  platform_code: string;
  site_code: string;
  shop_name: string;
  platform_shop_id: string;
  status: number;
  health: ShopHealth;
  health_reason: string | null;
  last_sync_at: string | null;
  last_sync_status: number | null;
  last_error: string | null;
  auth_expires_at: string | null;
  data_retain_until: string | null;
}

export interface PlatformSiteCatalog {
  code: string;
  name: string;
  sites: string[];
}

export interface AuthUrlResponse {
  url: string;
  state: string;
  platform: string;
  site_code: string;
}

export interface SyncTask {
  id: string;
  shop_id: string;
  module: string;
  trigger_type: number;
  status: number;
  started_at: string | null;
  finished_at: string | null;
  since: string | null;
  until: string | null;
  stats: {
    pulled?: number;
    first_order?: {
      platform_order_id: string;
      unified_status: string;
      currency: string;
      total_amount: string;
    } | null;
  };
  error: string | null;
  created_at: string;
}

export interface UnbindShopResponse {
  status: string;
  data_retain_until: string;
}

/** 统一订单状态（PRD 九态）。 */
export const UnifiedStatus = {
  PENDING: 'PENDING',
  PAID: 'PAID',
  SHIPPED: 'SHIPPED',
  DELIVERED: 'DELIVERED',
  COMPLETED: 'COMPLETED',
  CANCELLED: 'CANCELLED',
  REFUNDING: 'REFUNDING',
  REFUNDED: 'REFUNDED',
  RETURNED: 'RETURNED',
} as const;

export type UnifiedStatusValue = (typeof UnifiedStatus)[keyof typeof UnifiedStatus];

export type StatusChangeSource = 'SYSTEM' | 'WEBHOOK' | 'MANUAL';

export interface OrderStatusMapping {
  id: string;
  platform_code: string;
  platform_status: string;
  unified_status: UnifiedStatusValue;
  updated_at: string;
}

export interface UpsertOrderStatusMapping {
  platform_code: string;
  platform_status: string;
  unified_status: UnifiedStatusValue;
}

export type RateLimitSource = 'table' | 'default';

export interface PlatformRateLimit {
  id: string | null;
  platform_code: string;
  dimension: string;
  qps: number;
  burst: number;
  batch_limit: number;
  daily_quota: number | null;
  concurrency: number | null;
  source: RateLimitSource;
  updated_at: string | null;
}

export interface UpsertPlatformRateLimit {
  platform_code: string;
  dimension: string;
  qps: number;
  burst: number;
  batch_limit: number;
  daily_quota: number | null;
  concurrency: number | null;
}

export interface OrderStatusLog {
  id: string;
  order_id: string;
  from_status: UnifiedStatusValue | null;
  to_status: UnifiedStatusValue;
  platform_status: string;
  operator_id: string | null;
  source: StatusChangeSource;
  remark: string | null;
  created_at: string;
}

/** ---------- M2 订单工作台 ---------- */

export type LabelSize = 'A6' | '100x150';

export interface OrderListItem {
  id: string;
  shop_id: string;
  shop_name: string;
  site_code: string;
  platform_code: string;
  platform_order_id: string;
  unified_status: UnifiedStatusValue;
  platform_status: string;
  currency: string;
  total_amount: string;
  paid_at: string | null;
  created_at: string;
  buyer_name: string | null;
  shipment_status: string | null;
  failure_reason: string | null;
  review_status: string;
  exceptions: string[];
  ship_deadline: string | null;
}

export interface OrderParty {
  name: string | null;
  phone: string | null;
  country: string | null;
}

export interface OrderAddress extends OrderParty {
  state: string | null;
  city: string | null;
  line1: string | null;
  postal_code: string | null;
}

export interface OrderLineItem {
  id: string;
  platform_sku_id: string;
  platform_product_id: string;
  item_name: string;
  quantity: number;
  unit_price: string;
  currency: string;
}

export interface OrderFeeLine {
  id: string;
  fee_type: string;
  amount: string;
  currency: string;
  source: string;
}

export interface OrderShipment {
  carrier: string | null;
  tracking_no: string | null;
  status: string | null;
  failure_reason: string | null;
  attempt: number | null;
  shipped_at: string | null;
}

export interface OrderTimelineEntry {
  id: string;
  from_status: UnifiedStatusValue | null;
  to_status: UnifiedStatusValue;
  platform_status: string;
  operator_id: string | null;
  source: StatusChangeSource;
  remark: string | null;
  created_at: string;
}

export interface OrderDetail extends OrderListItem {
  item_amount: string;
  shipping_amount: string;
  tax_amount: string;
  discount_amount: string;
  shipped_at: string | null;
  buyer: OrderParty;
  ship_to: OrderAddress;
  items: OrderLineItem[];
  fees: OrderFeeLine[];
  shipment: OrderShipment;
  timeline: OrderTimelineEntry[];
  tracking_no: string | null;
  carrier: string | null;
}

export interface OrderListQuery {
  cursor?: string | null;
  limit?: number;
  platform_code?: string;
  shop_id?: string;
  site_code?: string;
  unified_status?: string;
  created_from?: string;
  created_to?: string;
  amount_min?: string;
  amount_max?: string;
  sku?: string;
  q?: string;
  fields?: string;
  queue?: 'to_ship' | 'exception';
  exception_kind?: string;
}

export interface ShipItemResult {
  order_id: string;
  platform_order_id: string;
  ok: boolean;
  tracking_no: string | null;
  message: string;
}

export interface BatchShipResult {
  succeeded: number;
  failed: number;
  results: ShipItemResult[];
}

export interface LabelSkip {
  order_id: string;
  platform_order_id: string | null;
  message: string;
}

export interface FilePayload {
  filename: string;
  content_type: string;
  content_base64: string;
  row_count: number;
  truncated: boolean;
  skipped: LabelSkip[];
}

export interface ReviewRule {
  id: string;
  currency: string;
  amount_gt: string;
  enabled: boolean;
}

export interface ReturnOrderView {
  id: string;
  order_id: string;
  platform_order_id: string;
  reason: string;
  status: string;
  refund_amount: string;
  currency: string;
  restock_flag: boolean;
  restock_sellable: boolean;
  restock_status: string;
  created_at: string;
}

export interface OrderFreshness {
  shop_id: string;
  shop_name: string;
  platform_code: string;
  last_sync_at: string | null;
  message: string;
}

export interface OrderAddressWrite {
  name?: string;
  phone?: string;
  country: string;
  state?: string;
  city?: string;
  line1?: string;
  postal_code?: string;
}

/** ---------- 商品主数据（M3-01） ---------- */

export const ProductStatus = {
  DRAFT: 'DRAFT',
  ON_SALE: 'ON_SALE',
  STOPPED: 'STOPPED',
  OUT_OF_STOCK: 'OUT_OF_STOCK',
  VIOLATION_OFF: 'VIOLATION_OFF',
} as const;

export type ProductStatusValue = (typeof ProductStatus)[keyof typeof ProductStatus];

export interface SpuListQuery {
  cursor?: string;
  limit?: number;
  status?: ProductStatusValue;
  q?: string;
}

export interface SpuListItem {
  id: string;
  title: string;
  brand: string | null;
  status: ProductStatusValue;
  sku_count: number;
  created_at: string;
  updated_at: string;
}

export interface SkuView {
  id: string;
  spu_id: string;
  sku_code: string;
  barcode: string | null;
  spec_attrs: Record<string, string>;
  weight_g: string;
  length_cm: string;
  width_cm: string;
  height_cm: string;
  purchase_price: string | null;
  currency: string | null;
  created_at: string;
  updated_at: string;
}

export interface SpuDetail {
  id: string;
  title: string;
  brand: string | null;
  material: string | null;
  purpose: string | null;
  status: ProductStatusValue;
  skus: SkuView[];
  created_at: string;
  updated_at: string;
}

export interface SkuWrite {
  sku_code: string;
  barcode?: string | null;
  spec_attrs?: Record<string, string>;
  weight_g: string;
  length_cm: string;
  width_cm: string;
  height_cm: string;
  purchase_price?: string | null;
  currency?: string | null;
}

export interface SpuCreate {
  title: string;
  brand?: string | null;
  material?: string | null;
  purpose?: string | null;
  status?: ProductStatusValue;
  skus?: SkuWrite[];
}

export interface SpuPatch {
  title?: string;
  brand?: string | null;
  material?: string | null;
  purpose?: string | null;
  status?: ProductStatusValue;
}

export interface SkuPatch {
  sku_code?: string;
  barcode?: string | null;
  spec_attrs?: Record<string, string>;
  weight_g?: string;
  length_cm?: string;
  width_cm?: string;
  height_cm?: string;
  purchase_price?: string | null;
  currency?: string | null;
}

/** ---------- Listing 映射与类目模板（M3-02） ---------- */

export const ListingStatus = {
  DRAFT: 'DRAFT',
  LINKED: 'LINKED',
  UNLISTED: 'UNLISTED',
} as const;

export type ListingStatusValue = (typeof ListingStatus)[keyof typeof ListingStatus];

export interface AttrTemplateItem {
  key: string;
  label: string;
  required: boolean;
}

export interface CategoryMappingView {
  id: string;
  platform_code: string;
  site_code: string;
  platform_category_id: string;
  local_category_code: string;
  name: string;
  attrs_template: AttrTemplateItem[];
  created_at: string;
  updated_at: string;
}

export interface CategoryMappingWrite {
  platform_code: string;
  site_code: string;
  platform_category_id: string;
  local_category_code: string;
  name: string;
  attrs_template?: AttrTemplateItem[];
}

export interface CategoryMappingPatch {
  platform_code?: string;
  site_code?: string;
  platform_category_id?: string;
  local_category_code?: string;
  name?: string;
  attrs_template?: AttrTemplateItem[];
}

export interface CategoryMappingQuery {
  cursor?: string;
  limit?: number;
  platform_code?: string;
  site_code?: string;
  q?: string;
}

export interface ListingView {
  id: string;
  sku_id: string;
  sku_code: string;
  shop_id: string;
  shop_name: string;
  platform_code: string;
  site_code: string;
  category_mapping_id: string | null;
  local_category_code: string | null;
  template_name: string | null;
  platform_product_id: string | null;
  platform_sku_id: string | null;
  price: string | null;
  currency: string | null;
  attr_values: Record<string, string>;
  status: ListingStatusValue;
  created_at: string;
  updated_at: string;
}

export interface ListingWrite {
  sku_id: string;
  shop_id: string;
  category_mapping_id?: string | null;
  platform_product_id?: string | null;
  platform_sku_id?: string | null;
  price?: string | null;
  currency?: string | null;
  attr_values?: Record<string, string>;
  status?: ListingStatusValue | null;
}

export interface ListingPatch {
  sku_id?: string;
  shop_id?: string;
  category_mapping_id?: string | null;
  platform_product_id?: string | null;
  platform_sku_id?: string | null;
  price?: string | null;
  currency?: string | null;
  attr_values?: Record<string, string>;
  status?: ListingStatusValue | null;
}

export interface ListingShopOption {
  id: string;
  shop_name: string;
  platform_code: string;
  site_code: string;
}

export interface ListingQuery {
  cursor?: string;
  limit?: number;
  sku_id?: string;
  shop_id?: string;
  platform_product_id?: string;
  status?: ListingStatusValue;
}
