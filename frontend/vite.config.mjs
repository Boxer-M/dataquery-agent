import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { localApi } from './server/local-api.mjs'

export default defineConfig({
  plugins: [react(), localApi(new URL('../.env.api', import.meta.url))],
  envPrefix: 'DATAQUERY_PUBLIC_',
  optimizeDeps: { include: ['echarts/core', 'echarts/charts', 'echarts/components', 'echarts/renderers'] },
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    cors: false,
    fs: {
      allow: [fileURLToPath(new URL('.', import.meta.url))],
      deny: ['.env', '.env.*', '*.{crt,pem}', '**/.git/**', '**/server/**', '**/vite.config.*'],
    },
  },
})
