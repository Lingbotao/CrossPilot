import { Navigate } from 'react-router-dom';

import { menuLeaves } from '@/router/menuLeaves';
import { useAuthStore } from '@/store/auth';

/** 将用户送到其实际拥有权限的第一个菜单，避免默认落到 403 页面。 */
export function DefaultAuthorizedRoute() {
  const permissionSet = useAuthStore((state) => state.permissionSet);
  const destination = menuLeaves().find((item) => permissionSet.has(item.permission))?.path;
  return <Navigate to={destination ?? '/login'} replace />;
}
