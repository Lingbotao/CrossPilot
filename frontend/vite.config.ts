import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import { fileURLToPath, URL } from 'node:url';

export default defineConfig({
  plugins: [react()],

  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },

  server: {
    port: 5173,
    // 本机开发时把 /api 代理到本地后端，避免前端代码里写死后端地址
    // （也顺带绕开 CORS —— 虽然后端已配 CORS，但同源更接近生产形态）
    proxy: {
      '/api': {
        target: process.env.VITE_DEV_API_TARGET ?? 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },

  build: {
    outDir: 'dist',
    sourcemap: false,
    chunkSizeWarningLimit: 1200,
    rollupOptions: {
      output: {
        // 按依赖体积拆包：antd 与 react 是当前最大的两块，
        // 拆开后业务代码改动不会让用户重新下载这两个大包。
        // echarts 到 M5 数据看板才引入，届时再补一个 charts 分组
        //（现在写进来会产出一个 0 字节的空 chunk）。
        manualChunks: {
          react: ['react', 'react-dom', 'react-router-dom'],
          antd: ['antd', '@ant-design/icons'],
          query: ['@tanstack/react-query', '@tanstack/react-table'],
        },
      },
    },
  },

  test: {
    environment: 'jsdom',
    globals: true,
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
  },
});
