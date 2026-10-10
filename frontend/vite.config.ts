import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// 后端地址：默认走 Pi 热点（演示）；宿舍网验证时用
//   VITE_BACKEND_TARGET=http://192.168.1.78:5000 npm run dev
const backendTarget = process.env.VITE_BACKEND_TARGET || 'http://192.168.4.1:5000';

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 3000,
    proxy: {
      '/api': {
        target: backendTarget,
        changeOrigin: true,
      },
    },
  },
});
