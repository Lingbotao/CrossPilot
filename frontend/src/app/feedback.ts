/**
 * 全局反馈（message / notification / modal）出口。
 *
 * 为什么要绕一层：antd v5 的静态方法（`message.error`）**拿不到 ConfigProvider 的
 * 主题与语言上下文**，开发环境会打印告警，暗色/自定义主题下样式也会不一致。
 * 正确做法是用 `App.useApp()` 拿到实例后注入到这里，
 * 这样 axios 拦截器（非组件环境）也能用上带主题的提示。
 */

type FeedbackFn = (content: string, duration?: number) => void;

export interface FeedbackApi {
  message: {
    success: FeedbackFn;
    error: FeedbackFn;
    warning: FeedbackFn;
    info: FeedbackFn;
  };
}

const noop: FeedbackFn = () => undefined;

/** 兜底实现：在 Provider 挂载之前（例如模块初始化阶段）不会崩。 */
let current: FeedbackApi = {
  message: { success: noop, error: noop, warning: noop, info: noop },
};

export function bindFeedback(api: FeedbackApi): void {
  current = api;
}

export function feedback(): FeedbackApi {
  return current;
}
