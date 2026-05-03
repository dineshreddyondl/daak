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
    // Allow any *.up.railway.app host so deploys don't break when the
    // domain changes. You can tighten this to a specific host later.
    allowedHosts: ['.up.railway.app'],
  },
})