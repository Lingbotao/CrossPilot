/**
 * 路由表。
 *
 * 子路由**由菜单配置生成** —— 菜单和路由是同一个数据源，
 * 避免出现"菜单里有、路由没配"（点进去 404）或反过来的情况。
 */

import { createBrowserRouter, Navigate } from 'react-router-dom';

import { AppLayout } from '@/components/AppLayout';
import { DashboardPage } from '@/pages/DashboardPage';
import { LoginPage } from '@/pages/LoginPage';
import { NotFoundPage } from '@/pages/NotFoundPage';
import { PlaceholderPage } from '@/pages/PlaceholderPage';
import { RequireAuth, RequireGuest, RequirePermission } from '@/router/guards';
import { MENU_ITEMS } from '@/router/menu';

export const router = createBrowserRouter([
  {
    path: '/login',
    element: (
      <RequireGuest>
        <LoginPage />
      </RequireGuest>
    ),
  },
  {
    path: '/',
    element: (
      <RequireAuth>
        <AppLayout />
      </RequireAuth>
    ),
    children: [
      { index: true, element: <Navigate to="/dashboard" replace /> },
      {
        path: 'dashboard',
        element: (
          <RequirePermission permission={MENU_ITEMS[0]!.permission}>
            <DashboardPage />
          </RequirePermission>
        ),
      },
      // 已铺菜单但功能未交付的模块：走统一占位页，明确标注交付里程碑。
      // 里程碑交付后，只需把这里换成真实页面组件，菜单与权限守卫无需改动。
      ...MENU_ITEMS.slice(1).map((item) => ({
        path: item.path.replace(/^\//, ''),
        element: (
          <RequirePermission permission={item.permission}>
            <PlaceholderPage />
          </RequirePermission>
        ),
      })),
      { path: '*', element: <NotFoundPage /> },
    ],
  },
]);
