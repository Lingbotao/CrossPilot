/**
 * 全局认证与租户上下文状态。
 *
 * 用 zustand 而不是 Context：这个 store 会被 Layout、路由守卫、
 * 几乎每个页面组件读取，Context 会导致整棵树重渲染。
 */

import { create } from 'zustand';

import { authApi } from '@/api/auth';
import { ApiError } from '@/api/client';
import { tokenStore } from '@/api/tokenStore';
import type { CurrentUser, LoginRequest, TenantBrief, TenantCurrent } from '@/api/types';

type AuthStatus = 'idle' | 'loading' | 'authenticated' | 'unauthenticated';

interface AuthState {
  status: AuthStatus;
  user: CurrentUser | null;
  tenant: TenantCurrent | null;
  availableTenants: TenantBrief[];

  /** 权限点集合。用 Set 而不是数组 —— 每个按钮都会查一次，O(1) 与 O(n) 差别很明显。 */
  permissionSet: Set<string>;

  login: (payload: LoginRequest) => Promise<void>;
  /** 刷新页面后恢复上下文（用 access token 拉一次 /auth/me） */
  bootstrap: () => Promise<void>;
  logout: () => Promise<void>;
  /** 本地清理（令牌已失效时调用，不发请求） */
  reset: () => void;

  hasPermission: (permission: string | string[]) => boolean;
  /** ★ F4：成本与利润可见性。默认 false —— 拿不到上下文时按"不可见"处理。 */
  canViewCost: () => boolean;
}

const EMPTY_PERMISSIONS = new Set<string>();

export const useAuthStore = create<AuthState>()((set, get) => ({
  status: 'idle',
  user: null,
  tenant: null,
  availableTenants: [],
  permissionSet: EMPTY_PERMISSIONS,

  login: async (payload) => {
    const result = await authApi.login(payload);
    tokenStore.set({
      access_token: result.access_token,
      refresh_token: result.refresh_token,
    });
    // 登录响应里带了租户与角色，但权限点要再拉一次 /auth/me ——
    // 权限点只在租户上下文接口里返回，避免登录响应过大。
    set({
      status: 'authenticated',
      availableTenants: result.available_tenants,
    });
    await get().bootstrap();
  },

  bootstrap: async () => {
    if (!tokenStore.hasSession()) {
      set({ status: 'unauthenticated', user: null, tenant: null, permissionSet: EMPTY_PERMISSIONS });
      return;
    }
    set({ status: 'loading' });
    try {
      const me = await authApi.me();
      set({
        status: 'authenticated',
        user: me.user,
        tenant: me.tenant,
        permissionSet: new Set(me.tenant.permissions),
      });
    } catch (error) {
      // 401 由拦截器处理跳转；这里只把状态归零，避免界面卡在 loading
      if (error instanceof ApiError && error.isAuthError) {
        set({ status: 'unauthenticated', user: null, tenant: null, permissionSet: EMPTY_PERMISSIONS });
        return;
      }
      set({ status: 'unauthenticated' });
      throw error;
    }
  },

  logout: async () => {
    try {
      await authApi.logout();
    } catch {
      // 登出接口失败不该阻塞用户 —— 本地清理才是关键
    } finally {
      get().reset();
    }
  },

  reset: () => {
    tokenStore.clear();
    set({
      status: 'unauthenticated',
      user: null,
      tenant: null,
      availableTenants: [],
      permissionSet: EMPTY_PERMISSIONS,
    });
  },

  hasPermission: (permission) => {
    const required = Array.isArray(permission) ? permission : [permission];
    if (required.length === 0) return true;
    const granted = get().permissionSet;
    return required.some((item) => granted.has(item));
  },

  canViewCost: () => {
    // 双保险：既看后端给的 can_view_cost，也看权限点。
    // 前端这一层只是"别把按钮画出来"，真正的拦截在后端（F4 是硬规则）。
    return get().tenant?.can_view_cost === true && get().permissionSet.has('cost:read');
  },
}));

/** 供非组件环境（如 axios 回调）读取的静态入口。 */
export const authActions = {
  reset: () => useAuthStore.getState().reset(),
};
