import { createHash } from 'crypto'
import fs from 'fs'
import path from 'path'
import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

/**
 * Stamps dist/sw.js (copied from public/) with this build's version and its
 * app-shell file list, so every build gets a fresh shell cache and an old one
 * is never kept. The version is a hash of the built output and the worker.
 */
function naradServiceWorker(): Plugin {
  let outDir = 'dist'
  let publicDir = 'public'
  return {
    name: 'narad-service-worker',
    apply: 'build',
    configResolved(config) {
      outDir = path.resolve(config.root, config.build.outDir)
      publicDir = config.publicDir
    },
    writeBundle(_options, bundle) {
      const swPath = path.join(outDir, 'sw.js')
      if (!fs.existsSync(swPath)) return
      const source = fs.readFileSync(swPath, 'utf8')
      const hash = createHash('sha256').update(source)
      for (const name of Object.keys(bundle).sort()) {
        const item = bundle[name]
        hash.update(name).update(item.type === 'chunk' ? item.code : item.source)
      }
      const version = hash.digest('hex').slice(0, 12)
      const built = Object.keys(bundle).filter(name => name.startsWith('assets/') && !name.endsWith('.map'))
      const icons = fs.existsSync(path.join(publicDir, 'icons'))
        ? fs.readdirSync(path.join(publicDir, 'icons')).map(name => `icons/${name}`)
        : []
      const shell = ['/', ...[...built, ...icons, 'manifest.webmanifest'].sort().map(name => `/${name}`)]
      const stamped = source
        .replace("'__NARAD_BUILD_VERSION__'", JSON.stringify(version))
        .replace("/* __NARAD_SHELL_ASSETS__ */ ['/']", JSON.stringify(shell))
      if (stamped.includes('__NARAD_BUILD_VERSION__') || stamped.includes('__NARAD_SHELL_ASSETS__')) {
        this.error('narad-service-worker: public/sw.js placeholders were not found')
      }
      fs.writeFileSync(swPath, stamped)
    },
  }
}

export default defineConfig({
  plugins: [react(), tailwindcss(), naradServiceWorker()],
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
    host: '127.0.0.1',
    port: 5174,
    // No proxy: src/lib/api.ts targets the same host on port 8000 in dev
    // (or VITE_API_BASE_URL), and the backend's CORS allows the Vite origins.
    // In production the backend serves dist/ itself — same origin, no base.
  },
})
