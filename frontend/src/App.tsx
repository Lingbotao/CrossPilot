/** 应用根组件：Provider + 路由 + 启动引导。 */

import { useEffect } from 'react';
import { RouterProvider } from 'react-router-dom';

import { setUnauthorizedHandler } from '@/api/client';
import { AppProviders } from '@/app/providers';
import { router } from '@/router';
import { useAuthStore } from '@/store/auth';

export function App() {
  useEffect(() => {
    // 令牌彻底失效时：清空状态并整页跳登录。
    // 这里刻意用整页跳转而不是 router.navigate —— 整页刷新能确保
    // 内存里的旧租户数据、查询缓存全部被清掉，不会出现"切换账号后
    // 还看到上一个租户的残留数据"这种极易被误判为串租户的现象。
    setUnauthorizedHandler(() => {
      useAuthStore.getState().reset();
      if (window.location.pathname !== '/login') {
        const from = encodeURIComponent(window.location.pathname + window.location.search);
        window.location.assign(`/login?from=${from}`);
      }
    });

    // 刷新页面后用 access token 恢复上下文（拉 /auth/me）
    void useAuthStore.getState().bootstrap().catch(() => {
      // 401 由拦截器统一处理；其它错误也不能阻塞首屏渲染
    });
  }, []);

  return (
    <AppProviders>
      <RouterProvider router={router} />
    </AppProviders>
  );
}

export default App;
