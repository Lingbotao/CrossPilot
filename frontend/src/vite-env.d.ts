/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** 后端 API 前缀。生产走 nginx 反代，保持默认 /api/v1 即可 */
  readonly VITE_API_BASE_URL?: string;
  readonly VITE_APP_TITLE?: string;
  /** 仅开发用：vite dev server 把 /api 代理到哪个后端 */
  readonly VITE_DEV_API_TARGET?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
