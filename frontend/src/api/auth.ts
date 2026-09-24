/** 认证相关接口（与后端 ``/api/v1/auth/*`` 对应）。 */

import { api } from './client';
import type { LoginRequest, LoginResponse, MeResponse, RefreshResponse } from './types';

export const authApi = {
  login: (payload: LoginRequest) =>
    api.post<LoginResponse>('/auth/login', payload, { skipAuthRefresh: true }),

  refresh: (refreshToken: string) =>
    api.post<RefreshResponse>('/auth/refresh', { refresh_token: refreshToken }, { skipAuthRefresh: true }),

  logout: () => api.post<{ status: string }>('/auth/logout'),

  me: () => api.get<MeResponse>('/auth/me'),
};
