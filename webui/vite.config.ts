import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      '/api/monitor': 'http://localhost:21530'
    }
  },
  test: {
    environment: 'jsdom'
  }
})
