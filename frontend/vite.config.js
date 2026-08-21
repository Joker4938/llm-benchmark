import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  base: './',
  build: {
    target: ['chrome80', 'firefox78'],
    cssTarget: ['chrome80', 'firefox78'],
    sourcemap: false,
    assetsInlineLimit: 4096
  },
  server: {
    port: 8000,
    proxy: {
      '/api': 'http://127.0.0.1:8080',
      '/health': 'http://127.0.0.1:8080'
    }
  }
})
