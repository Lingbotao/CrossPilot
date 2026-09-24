/**
 * 权限渲染守卫（M0-09）。
 *
 * ⚠️ **这不是安全边界**。它的唯一作用是"别把用户点了会 403 的按钮画出来"。
 * 所有权限校验必须在后端接口层强制执行（PRD 约束 C8）。
 */

import type { ReactNode } from 'react';

import { usePermission } from '@/hooks/usePermission';
import type { PermValue } from '@/api/types';

export interface PermissionGuardProps {
  /** 需要的权限点 */
  permission: PermValue | PermValue[];
  /** `any`（默认）= 任一命中；`all` = 全部命中 */
  mode?: 'any' | 'all';
  /** 无权限时的兜底内容。默认渲染 null（即不占位） */
  fallback?: ReactNode;
  children: ReactNode;
}

export function PermissionGuard({
  permission,
  mode = 'any',
  fallback = null,
  children,
}: PermissionGuardProps) {
  const { can, canAll } = usePermission();
  const required = Array.isArray(permission) ? permission : [permission];
  const allowed = mode === 'all' ? canAll(required) : can(required);

  if (!allowed) {
    return <>{fallback}</>;
  }
  return <>{children}</>;
}

export interface CostGuardProps {
  fallback?: ReactNode;
  children: ReactNode;
}

/**
 * ★ F4 专用守卫：采购成本价 / 利润数据的渲染门禁。
 *
 * 单独抽一个组件（而不是让调用方写 `permission={Perm.COST_READ}`）的原因：
 * F4 是产品红线，需要在代码里**可被搜索、可被 review** ——
 * 全文搜 `CostGuard` 就能列出所有展示成本的入口，
 * 而搜 `permission="cost:read"` 很容易漏（拼写、大小写、散落各处）。
 */
export function CostGuard({ fallback = null, children }: CostGuardProps) {
  const { canViewCost, can } = usePermission();
  // 双条件：后端标记 + 权限点。任一不满足都不展示。
  const allowed = canViewCost && can('cost:read');

  if (!allowed) {
    return <>{fallback}</>;
  }
  return <>{children}</>;
}

export default PermissionGuard;
