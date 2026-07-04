import path from 'path'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          markdown: ['react-markdown', 'remark-gfm'],
        },
      },
    },
  },
  server: {
    port: 5173,
    // No proxy: src/lib/api.ts targets http://localhost:8000 directly in dev
    // (or VITE_API_BASE_URL), and the backend's CORS allows the Vite origins.
    // In production the backend serves dist/ itself — same origin, no base.
  },
})
