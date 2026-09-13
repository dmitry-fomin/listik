import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// Доска отдаётся сервером Listik из web/dist как SPA-фолбэк,
// поэтому base — абсолютный корень, а outDir — 'dist'.
export default defineConfig({
  base: '/',
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    proxy: {
      // В dev все /api/* уходят на локальный сервер Listik.
      // Благодаря этому VITE_API_BASE можно оставить пустым (same-origin),
      // и SSE тоже работает без CORS.
      '/api': {
        target: 'http://127.0.0.1:8787',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    sourcemap: false,
  },
})
