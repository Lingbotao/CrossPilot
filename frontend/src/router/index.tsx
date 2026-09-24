/**
 * 路由表。
 *
 * 子路由**由菜单配置生成** —— 菜单和路由是同一个数据源，
 * 避免出现"菜单里有、路由没配"（点进去 404）或反过来的情况。
 */

import { createBrowserRouter } from 'react-router-dom';

import { AppLayout } from '@/components/AppLayout';
import { AcceptInvitationPage } from '@/pages/AcceptInvitationPage';
import { LoginPage } from '@/pages/LoginPage';
import { NotFoundPage } from '@/pages/NotFoundPage';
import { PlaceholderPage } from '@/pages/PlaceholderPage';
import { RegisterPage } from '@/pages/RegisterPage';
import { VerifyEmailPage } from '@/pages/VerifyEmailPage';
import { DefaultAuthorizedRoute } from '@/router/DefaultAuthorizedRoute';
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
    path: '/register',
    element: (
      <RequireGuest>
        <RegisterPage />
      </RequireGuest>
    ),
  },
  { path: '/verify-email', element: <VerifyEmailPage /> },
  { path: '/invite/accept', element: <AcceptInvitationPage /> },
  {
    path: '/',
    element: (
      <RequireAuth>
        <AppLayout />
      </RequireAuth>
    ),
    children: [
      { index: true, element: <DefaultAuthorizedRoute /> },
      ...MENU_ITEMS.map((item) => {
        const Page = item.component ?? PlaceholderPage;
        return {
          path: item.path.replace(/^\//, ''),
          element: (
            <RequirePermission permission={item.permission}>
              <Page />
            </RequirePermission>
          ),
        };
      }),
      { path: '*', element: <NotFoundPage /> },
    ],
  },
]);
