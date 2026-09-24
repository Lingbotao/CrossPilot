/**
 * HTTP 客户端（Axios 拦截器）。
 *
 * 这一层负责四件事，业务代码不需要再关心：
 *
 * 1. **信封解包**：所有响应都是 `{code, message, data, trace_id}`；
 *    `code !== 0` 统一抛 `ApiError`，业务代码只需 try/catch。
 * 2. **令牌自动续期**：401 时用 Refresh Token 换新令牌并**重放原请求**；
 *    并发请求只触发一次续期（single-flight），否则会六个请求同时刷新、互相作废令牌。
 * 3. **trace_id 透传**：每个请求带 `X-Trace-Id`，报错时一并展示给用户，
 *    用户报障时可以直接把这个 ID 给后端去检索全链路日志。
 * 4. **统一错误提示**：默认弹一次提示；需要自己处理错误的场景用 `skipErrorToast`。
 */

import axios, {
  type AxiosError,
  type AxiosInstance,
  type AxiosRequestConfig,
  type InternalAxiosRequestConfig,
} from 'axios';

import { feedback } from '@/app/feedback';
import { newTraceId } from '@/utils/trace';

import { ErrorCode, type ApiResponse } from './types';
import { tokenStore } from './tokenStore';

declare module 'axios' {
  export interface AxiosRequestConfig {
    /** 不弹全局错误提示（用于表单内联校验、列表静默重试等场景） */
    skipErrorToast?: boolean;
    /** 不参与自动续期（登录/刷新接口自身必须跳过，否则会递归） */
    skipAuthRefresh?: boolean;
    /** 写操作自动带上 Idempotency-Key（PRD 10.1 约定） */
    idempotent?: boolean;
  }
}

export class ApiError extends Error {
  readonly code: number;
  readonly data: unknown;
  readonly traceId: string | null;
  readonly httpStatus: number | undefined;

  constructor(params: {
    code: number;
    message: string;
    data?: unknown;
    traceId?: string | null;
    httpStatus?: number;
  }) {
    super(params.message);
    this.name = 'ApiError';
    this.code = params.code;
    this.data = params.data ?? null;
    this.traceId = params.traceId ?? null;
    this.httpStatus = params.httpStatus;
  }

  /** 是否是"需要重新登录"这类错误（会触发跳登录页）。 */
  get isAuthError(): boolean {
    return (
      this.code === ErrorCode.UNAUTHENTICATED ||
      this.code === ErrorCode.TOKEN_EXPIRED ||
      this.httpStatus === 401
    );
  }

  /** 令牌过期但可续期：交给拦截器处理。 */
  get isRefreshable(): boolean {
    return this.code === ErrorCode.TOKEN_EXPIRED;
  }
}

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '/api/v1';
const REQUEST_TIMEOUT_MS = 30_000;

export const http: AxiosInstance = axios.create({
  baseURL: BASE_URL,
  timeout: REQUEST_TIMEOUT_MS,
  headers: { 'Content-Type': 'application/json; charset=utf-8' },
});

/** 续期失败时通知外部（跳登录页）—— 用回调而不是直接 import router，避免循环依赖。 */
type UnauthorizedHandler = () => void;
let onUnauthorized: UnauthorizedHandler = () => {
  window.location.href = '/login';
};
export function setUnauthorizedHandler(handler: UnauthorizedHandler): void {
  onUnauthorized = handler;
}

// ------------------------------------------------------------------ 请求拦截
http.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const token = tokenStore.getAccessToken();
  if (token && !config.headers.Authorization) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  // 客户端生成 trace_id 便于前后端串联排障；服务端会校验格式，非法则自行生成
  if (!config.headers['X-Trace-Id']) {
    config.headers['X-Trace-Id'] = newTraceId();
  }
  // 写操作用 Idempotency-Key 防重复提交（表单双击、网络重试都不会重复下单）
  if (config.idempotent && !config.headers['Idempotency-Key']) {
    config.headers['Idempotency-Key'] = newTraceId();
  }
  return config;
});

// ------------------------------------------------------------------ 令牌续期
let refreshPromise: Promise<string> | null = null;

async function refreshAccessToken(): Promise<string> {
  const refreshToken = tokenStore.getRefreshToken();
  if (!refreshToken) {
    throw new ApiError({ code: ErrorCode.UNAUTHENTICATED, message: '登录状态已失效' });
  }

  // 用裸 axios 发请求：绕开本实例的拦截器，避免 401 → 续期 → 401 的递归
  const response = await axios.post<ApiResponse<{ access_token: string; refresh_token: string }>>(
    `${BASE_URL}/auth/refresh`,
    { refresh_token: refreshToken },
    { timeout: REQUEST_TIMEOUT_MS },
  );

  const payload = response.data;
  if (payload.code !== 0 || !payload.data) {
    throw new ApiError({
      code: payload.code,
      message: payload.message,
      traceId: payload.trace_id,
    });
  }

  tokenStore.set({
    access_token: payload.data.access_token,
    refresh_token: payload.data.refresh_token,
  });
  return payload.data.access_token;
}

/** 并发请求共享同一次续期：否则多个 401 会各刷一次，把彼此的 refresh token 作废。 */
function ensureRefreshed(): Promise<string> {
  refreshPromise ??= refreshAccessToken().finally(() => {
    refreshPromise = null;
  });
  return refreshPromise;
}

// ------------------------------------------------------------------ 响应拦截
http.interceptors.response.use(
  (response) => response,
  async (error: AxiosError<ApiResponse<unknown>>) => {
    const config = error.config as (AxiosRequestConfig & { _retried?: boolean }) | undefined;
    const payload = error.response?.data;
    const status = error.response?.status;

    // ---- 网络层错误（无响应）：超时 / 断网 / 跨域被拦 ----
    if (!error.response) {
      const isTimeout = error.code === 'ECONNABORTED';
      const apiError = new ApiError({
        code: ErrorCode.INTERNAL_ERROR,
        message: isTimeout ? '请求超时，请稍后重试' : '网络异常，请检查网络连接',
      });
      if (!config?.skipErrorToast) {
        feedback().message.error(apiError.message);
      }
      throw apiError;
    }

    const apiError = new ApiError({
      code: payload?.code ?? ErrorCode.INTERNAL_ERROR,
      message: payload?.message ?? `请求失败（HTTP ${status}）`,
      data: payload?.data,
      traceId: payload?.trace_id ?? null,
      httpStatus: status,
    });

    // ---- 401：尝试续期一次并重放原请求 ----
    const canRetry =
      status === 401 &&
      !config?.skipAuthRefresh &&
      config !== undefined &&
      !config._retried &&
      Boolean(tokenStore.getRefreshToken());

    if (canRetry && config) {
      config._retried = true;
      try {
        const newToken = await ensureRefreshed();
        config.headers = { ...config.headers, Authorization: `Bearer ${newToken}` };
        return await http.request(config);
      } catch {
        // 续期失败：清空令牌并交给外部跳登录页
        tokenStore.clear();
        onUnauthorized();
        throw new ApiError({
          code: ErrorCode.UNAUTHENTICATED,
          message: '登录状态已失效，请重新登录',
          httpStatus: 401,
        });
      }
    }

    if (status === 401) {
      tokenStore.clear();
      onUnauthorized();
    }

    // 40201：店铺授权过期，需要引导用户重新授权（由具体页面处理，不在这里跳转）
    if (!config?.skipErrorToast) {
      const suffix = apiError.traceId ? `（追踪号 ${apiError.traceId.slice(-6)}）` : '';
      feedback().message.error(`${apiError.message}${suffix}`);
    }

    throw apiError;
  },
);

// ------------------------------------------------------------------ 便捷方法
/**
 * 发起请求并返回解包后的 `data`。
 *
 * 后端约定：成功时 `code === 0`，此时 `data` 一定存在（或为 null，如 204 语义）。
 */
export async function request<T>(config: AxiosRequestConfig): Promise<T> {
  const response = await http.request<ApiResponse<T>>(config);
  const payload = response.data;

  if (payload.code !== 0) {
    throw new ApiError({
      code: payload.code,
      message: payload.message,
      data: payload.data,
      traceId: payload.trace_id,
      httpStatus: response.status,
    });
  }
  return payload.data as T;
}

export const api = {
  get: <T>(url: string, config?: AxiosRequestConfig) => request<T>({ ...config, method: 'GET', url }),
  post: <T>(url: string, body?: unknown, config?: AxiosRequestConfig) =>
    request<T>({ ...config, method: 'POST', url, data: body }),
  put: <T>(url: string, body?: unknown, config?: AxiosRequestConfig) =>
    request<T>({ ...config, method: 'PUT', url, data: body }),
  patch: <T>(url: string, body?: unknown, config?: AxiosRequestConfig) =>
    request<T>({ ...config, method: 'PATCH', url, data: body }),
  delete: <T>(url: string, config?: AxiosRequestConfig) =>
    request<T>({ ...config, method: 'DELETE', url }),
};

export { BASE_URL };
