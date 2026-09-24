/**
 * 令牌存取。
 *
 * ⚠️ **已知风险与取舍（要上线前处理）**
 * 当前把 token 放在 localStorage：任一 XSS 都能直接读走令牌。
 * 更稳的方案是 Refresh Token 走 `HttpOnly + Secure + SameSite=Strict` Cookie，
 * Access Token 只留在内存里（刷新页面就重新换一次）。
 *
 * 之所以现在没这么做：Cookie 方案要处理 CSRF、跨域、以及 SPA 刷新时的"静默续期"
 * 时序，属于**上线前的安全加固项**，已登记在开发规划 §13（上线前补做清单）。
 * 当前是开发交付版本，明确记录这个取舍，而不是假装它不存在。
 */

const ACCESS_TOKEN_KEY = 'crosspilot.access_token';
const REFRESH_TOKEN_KEY = 'crosspilot.refresh_token';

export interface TokenPair {
  access_token: string;
  refresh_token: string;
}

export const tokenStore = {
  getAccessToken(): string | null {
    try {
      return window.localStorage.getItem(ACCESS_TOKEN_KEY);
    } catch {
      return null; // 隐私模式/禁用存储时降级为未登录，而不是抛错白屏
    }
  },

  getRefreshToken(): string | null {
    try {
      return window.localStorage.getItem(REFRESH_TOKEN_KEY);
    } catch {
      return null;
    }
  },

  set(pair: TokenPair): void {
    try {
      window.localStorage.setItem(ACCESS_TOKEN_KEY, pair.access_token);
      window.localStorage.setItem(REFRESH_TOKEN_KEY, pair.refresh_token);
    } catch (error) {
      console.error('[tokenStore] 写入令牌失败（可能处于隐私模式）', error);
    }
  },

  clear(): void {
    try {
      window.localStorage.removeItem(ACCESS_TOKEN_KEY);
      window.localStorage.removeItem(REFRESH_TOKEN_KEY);
    } catch {
      /* 忽略 */
    }
  },

  hasSession(): boolean {
    return Boolean(tokenStore.getAccessToken());
  },
};
