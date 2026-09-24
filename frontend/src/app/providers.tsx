/** 全局 Provider 组合。 */

import { App as AntdApp, ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useEffect, type ReactNode } from 'react';

import { ApiError } from '@/api/client';
import { bindFeedback } from '@/app/feedback';
import { appTheme } from '@/app/theme';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // 后端数据变化不频繁，且列表页切换很频繁 —— 30s 内复用缓存体验更好
      staleTime: 30_000,
      gcTime: 5 * 60_000,
      refetchOnWindowFocus: false,
      retry: (failureCount, error) => {
        // 业务错误重试没有意义（权限不足、资源不存在，重试一万次也一样）
        if (error instanceof ApiError) {
          return failureCount < 2 && error.code >= 10099;
        }
        return failureCount < 2;
      },
    },
    mutations: {
      retry: false,
    },
  },
});

/** 把 antd 的 message 实例注入到非组件环境（axios 拦截器等）。 */
function FeedbackBridge({ children }: { children: ReactNode }) {
  const { message } = AntdApp.useApp();

  useEffect(() => {
    bindFeedback({
      message: {
        success: (content, duration) => {
          void message.success(content, duration);
        },
        error: (content, duration) => {
          void message.error(content, duration);
        },
        warning: (content, duration) => {
          void message.warning(content, duration);
        },
        info: (content, duration) => {
          void message.info(content, duration);
        },
      },
    });
  }, [message]);

  return <>{children}</>;
}

export function AppProviders({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={queryClient}>
      <ConfigProvider locale={zhCN} theme={appTheme}>
        <AntdApp>
          <FeedbackBridge>{children}</FeedbackBridge>
        </AntdApp>
      </ConfigProvider>
    </QueryClientProvider>
  );
}

export default AppProviders;
