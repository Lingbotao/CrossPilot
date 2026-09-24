/** 路由守卫。 */

import { Result, Spin } from 'antd';
import type { ReactNode } from 'react';
import { Navigate, useLocation } from 'react-router-dom';

import { usePermission } from '@/hooks/usePermission';
import { useAuthStore } from '@/store/auth';
import type { PermValue } from '@/api/types';

function FullPageLoading() {
  return (
    <div
      style={{
        height: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
      }}
    >
      <Spin size="large" tip="正在恢复登录状态…">
        <div style={{ width: 160, height: 60 }} />
      </Spin>
    </div>
  );
}

/** 需要登录。未登录时跳登录页，并记住原目标地址。 */
export function RequireAuth({ children }: { children: ReactNode }) {
  const status = useAuthStore((state) => state.status);
  const location = useLocation();

  // idle 与 loading 都还在恢复阶段 —— 不能在这时判定"未登录"，
  // 否则刷新页面会先闪一下登录页再跳回来（体验很差且会丢 deep link）
  if (status === 'idle' || status === 'loading') {
    return <FullPageLoading />;
  }

  if (status === 'unauthenticated') {
    return <Navigate to="/login" state={{ from: location.pathname + location.search }} replace />;
  }

  return <>{children}</>;
}

/** 仅未登录可访问（登录页）。已登录用户访问登录页直接送回看板。 */
export function RequireGuest({ children }: { children: ReactNode }) {
  const status = useAuthStore((state) => state.status);

  if (status === 'idle' || status === 'loading') {
    return <FullPageLoading />;
  }
  if (status === 'authenticated') {
    return <Navigate to="/dashboard" replace />;
  }
  return <>{children}</>;
}

/**
 * 需要特定权限点。
 *
 * ⚠️ 这里拦的是"URL 直接访问"的场景（用户手敲地址或从旧书签进入）。
 * 后端仍然会独立校验一次 —— 前端拦不住真正的越权。
 */
export function RequirePermission({
  permission,
  children,
}: {
  permission: PermValue;
  children: ReactNode;
}) {
  const { can } = usePermission();

  if (!can(permission)) {
    return (
      <Result
        status="403"
        title="403"
        subTitle="当前角色无权访问该功能。如需授权，请联系租户管理员调整角色。"
      />
    );
  }
  return <>{children}</>;
}
