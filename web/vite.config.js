import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  preview: {
    port: 5173,
    // Allow any host so production deploys work behind any domain
    // (Railway-generated, custom Cloudflare-fronted, etc.)
    // For an internal tool this is acceptable; for a public app you'd
    // restrict to a specific list.
    allowedHosts: true,
  },
})