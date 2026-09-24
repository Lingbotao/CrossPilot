/** 认证相关接口（与后端 ``/api/v1/auth/*`` 对应）。 */

import { api } from './client';
import { tokenStore } from './tokenStore';
import type {
  ConfirmPasswordRequest,
  ConfirmPasswordResponse,
  LoginRequest,
  LoginResponse,
  MeResponse,
  RefreshResponse,
  RegisterRequest,
  RegisterResponse,
  ResendVerificationRequest,
  VerifyEmailRequest,
} from './types';

export const authApi = {
  login: (payload: LoginRequest) =>
    api.post<LoginResponse>('/auth/login', payload, { skipAuthRefresh: true }),

  register: (payload: RegisterRequest) =>
    api.post<RegisterResponse>('/auth/register', payload, {
      idempotent: true,
      skipAuthRefresh: true,
    }),

  verifyEmail: (payload: VerifyEmailRequest) =>
    api.post<{ status: string }>('/auth/verify-email', payload, {
      idempotent: true,
      skipAuthRefresh: true,
    }),

  resendVerification: (payload: ResendVerificationRequest) =>
    api.post<{ status: string }>('/auth/resend-verification', payload, {
      idempotent: true,
      skipAuthRefresh: true,
    }),

  confirmPassword: (payload: ConfirmPasswordRequest) =>
    api.post<ConfirmPasswordResponse>('/auth/confirm-password', payload, {
      idempotent: true,
      skipErrorToast: true,
    }),

  refresh: (refreshToken: string) =>
    api.post<RefreshResponse>('/auth/refresh', { refresh_token: refreshToken }, { skipAuthRefresh: true }),

  logout: () =>
    api.post<{ status: string }>(
      '/auth/logout',
      { refresh_token: tokenStore.getRefreshToken() },
      { skipAuthRefresh: true },
    ),

  me: () => api.get<MeResponse>('/auth/me'),
};
