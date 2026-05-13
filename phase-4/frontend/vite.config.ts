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
  server: {
    port: 5173,
    proxy: {
      '/chat':     'http://localhost:8000',
      '/health':   'http://localhost:8000',
      '/trace':    'http://localhost:8000',
      '/sutras':   'http://localhost:8000',
      '/karma':    'http://localhost:8000',
      '/sankalpa': 'http://localhost:8000',
      '/media':    'http://localhost:8000',
      '/tts':      'http://localhost:8000',
      '/wiki':     'http://localhost:8000',
      '/projects': 'http://localhost:8000',
      '/sessions': 'http://localhost:8000',
    },
  },
})
