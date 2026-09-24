/**
 * 权限判断 Hook。
 *
 * ⚠️ 再次强调：**前端权限只决定"画不画"，不是安全边界**。
 * 用户可以改 localStorage、直接调接口 —— 所有校验必须在后端接口层强制执行
 * （PRD 约束 C8：前端隐藏不作为安全边界，这是本项目红线之一）。
 */

import { useCallback } from 'react';

import { useAuthStore } from '@/store/auth';
import type { PermValue } from '@/api/types';

export interface UsePermissionResult {
  /** 任一权限点命中即 true */
  can: (permission: PermValue | PermValue[]) => boolean;
  /** 全部权限点都命中才 true */
  canAll: (permissions: PermValue[]) => boolean;
  /** ★ F4：是否可见采购成本价与利润 */
  canViewCost: boolean;
  roleCode: string | null;
  roleName: string | null;
}

export function usePermission(): UsePermissionResult {
  const hasPermission = useAuthStore((state) => state.hasPermission);
  const canViewCost = useAuthStore((state) => state.canViewCost());
  const tenant = useAuthStore((state) => state.tenant);

  const can = useCallback(
    (permission: PermValue | PermValue[]) => hasPermission(permission as string | string[]),
    [hasPermission],
  );

  const canAll = useCallback(
    (permissions: PermValue[]) => permissions.every((item) => hasPermission(item as string)),
    [hasPermission],
  );

  return {
    can,
    canAll,
    canViewCost,
    roleCode: tenant?.role_code ?? null,
    roleName: tenant?.role_name ?? null,
  };
}
