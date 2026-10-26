import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// TrajNexa_Map 的只读空间快照经开发代理转发，避免浏览器跨域；生产部署时由网关提供同路径。
const MAP_PROXY_TARGET = process.env.MAP_PROXY_TARGET || 'http://localhost:8088'

export default defineConfig({
  plugins: [vue()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      '/api': { target: process.env.API_PROXY_TARGET || 'http://localhost:8000', changeOrigin: true },
      '/map-api': {
        target: MAP_PROXY_TARGET,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/map-api/, ''),
      },
    },
  },
})
