/*
 * Narad service worker: push notifications and an offline app shell.
 *
 * Caches only the app shell (index.html, hashed JS/CSS, icons, the manifest)
 * and Google Fonts, under a cache name that changes with every build. API
 * responses and /media never touch a cache: they are personal data and pass
 * straight through. Navigations are network-first; when the Mac is asleep or
 * the tunnel is down the cached shell starts, and the app shows its "Narad's
 * Mac is asleep" screen. A Cloudflare Access sign-in redirect is let through
 * and never cached as the shell.
 *
 * vite.config.ts (narad-service-worker) fills in BUILD_VERSION and
 * SHELL_ASSETS at build time. The request policy functions are plain and
 * side-effect free, so tests/sw.test.mjs runs them in Node.
 */

const BUILD_VERSION = '__NARAD_BUILD_VERSION__'
const SHELL_ASSETS = /* __NARAD_SHELL_ASSETS__ */ ['/']
const SHELL_PREFIX = 'narad-shell-'
const SHELL_CACHE = SHELL_PREFIX + BUILD_VERSION
const FONT_CACHE = 'narad-fonts-v1'
const SHELL_URL = '/'
// index.html carries <meta name="narad-shell">; an Access login page does not.
const SHELL_MARKER = 'name="narad-shell"'
const FONT_HOSTS = ['fonts.googleapis.com', 'fonts.gstatic.com']

// ── Request policy (pure) ───────────────────────────────────────────────────

/** How to answer a request: navigate | asset | refresh | font | passthrough. */
function requestStrategy(request, origin) {
  if (request.method !== 'GET') return 'passthrough'
  const url = new URL(request.url)
  if (url.origin !== origin) {
    return FONT_HOSTS.includes(url.hostname) ? 'font' : 'passthrough'
  }
  const path = url.pathname
  if (request.mode === 'navigate') {
    // Only the app itself; /media pages, OAuth callbacks and the rest are
    // documents of their own and always come from the network.
    return path === '/' || path === '/index.html' ? 'navigate' : 'passthrough'
  }
  if (path.startsWith('/assets/')) return 'asset'
  if (path.startsWith('/icons/') || path.startsWith('/favicon') || path === '/manifest.webmanifest') {
    return 'refresh'
  }
  return 'passthrough'
}

/** Cloudflare's answers when the Mac or the tunnel is unreachable. */
function isUnreachableStatus(status) {
  return status === 502 || status === 503 || status === 504 || (status >= 520 && status <= 530)
}

/** What to do with a navigation's network response: use it, or fall back to the shell. */
function navigationOutcome(response) {
  // An Access sign-in redirect (or any redirect) goes to the browser as-is.
  if (response.type === 'opaqueredirect') return 'use'
  return isUnreachableStatus(response.status) ? 'fallback' : 'use'
}

/** A same-origin 200 that was not redirected: never an Access page, never an error. */
function isCacheableShellResponse(response, origin) {
  if (!response || response.status !== 200 || response.type !== 'basic' || response.redirected) return false
  try {
    return !response.url || new URL(response.url).origin === origin
  } catch {
    return false
  }
}

/** Only same-origin paths may be opened from a notification. */
function safeAppUrl(value, origin) {
  try {
    const url = new URL(String(value || '/'), origin)
    return url.origin === origin ? url.pathname + url.search + url.hash : '/'
  } catch {
    return '/'
  }
}

/** Vahana's small payload: title, body, url, tag, kind, id (and the unread count). */
function parsePushPayload(text, origin) {
  let data = {}
  try {
    data = JSON.parse(text || '{}') || {}
  } catch {
    data = {}
  }
  const unread = Number(data.unread)
  return {
    title: String(data.title || 'Narad').slice(0, 80),
    body: String(data.body || 'Open Narad to see it.').slice(0, 180),
    url: safeAppUrl(data.url, origin),
    tag: data.tag ? String(data.tag).slice(0, 64) : '',
    kind: String(data.kind || 'system'),
    id: String(data.id || ''),
    unread: Number.isFinite(unread) && unread >= 0 ? unread : null,
  }
}

// ── Offline page (no cached shell yet) ──────────────────────────────────────

function offlinePage() {
  const html = `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#1e2447"><title>Narad is offline</title>
<style>
  body{margin:0;min-height:100vh;display:grid;place-items:center;padding:24px;box-sizing:border-box;
    background:#211f1c;color:#fcfaf2;font:15px/1.5 Inter,system-ui,sans-serif;text-align:center}
  main{max-width:360px}
  h1{font:700 24px/1.2 'Playfair Display',Georgia,serif;margin:18px 0 8px}
  p{margin:0 0 22px;color:rgba(252,250,242,.62)}
  .dot{width:12px;height:12px;border-radius:50%;background:#c2410c;margin:0 auto;box-shadow:0 0 0 6px rgba(194,65,12,.18)}
  button{min-height:48px;padding:0 26px;border:0;border-radius:11px;background:#c2410c;color:#fff;font:700 14px Inter,system-ui,sans-serif}
</style></head><body><main>
<div class="dot"></div>
<h1 id="t">Narad's Mac is asleep or offline</h1>
<p id="m">It'll be back soon. Your chats and reminders are safe on the Mac.</p>
<button onclick="location.reload()">Try again</button>
</main><script>
if (!navigator.onLine) {
  document.getElementById('t').textContent = 'Your phone is offline'
  document.getElementById('m').textContent = 'Connect to the internet, then try again.'
}
</script></body></html>`
  return new Response(html, { status: 503, headers: { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' } })
}

// ── Handlers ────────────────────────────────────────────────────────────────

async function cachedShell() {
  const cache = await caches.open(SHELL_CACHE)
  return cache.match(SHELL_URL)
}

async function handleNavigation(event) {
  const registration = self.registration
  if (registration.waiting) {
    // A new build is installed: take it on this navigation when no other tab
    // still runs the old one, by reloading into the new worker.
    const windows = await self.clients.matchAll({ type: 'window' })
    if (windows.length <= 1) {
      registration.waiting.postMessage({ type: 'SKIP_WAITING' })
      return new Response('', { headers: { Refresh: '0' } })
    }
  }
  try {
    const response = await fetch(event.request)
    if (navigationOutcome(response) === 'use') return response
  } catch {
    // Offline or the tunnel refused the connection: fall back below.
  }
  return (await cachedShell()) || offlinePage()
}

/** Hashed assets are immutable (cache first); icons and the manifest refresh in the background. */
async function handleAsset(event, refresh) {
  const cached = await caches.match(event.request)
  if (cached && !refresh) return cached
  const network = fetch(event.request).then(async response => {
    if (isCacheableShellResponse(response, self.location.origin)) {
      const cache = await caches.open(SHELL_CACHE)
      await cache.put(event.request, response.clone())
    }
    return response
  })
  if (!cached) return network
  event.waitUntil(network.catch(() => undefined))
  return cached
}

/** Font files never change; the Google Fonts stylesheet refreshes in the background. */
async function handleFont(event) {
  const cache = await caches.open(FONT_CACHE)
  const cached = await cache.match(event.request)
  const stylesheet = new URL(event.request.url).hostname === 'fonts.googleapis.com'
  if (cached && !stylesheet) return cached
  const network = fetch(event.request).then(async response => {
    if (response.ok || response.type === 'opaque') await cache.put(event.request, response.clone())
    return response
  })
  if (!cached) return network
  event.waitUntil(network.catch(() => undefined))
  return cached
}

async function precacheShell() {
  const cache = await caches.open(SHELL_CACHE)
  await Promise.all(SHELL_ASSETS.map(async path => {
    const response = await fetch(new Request(path, { cache: 'reload', credentials: 'same-origin' }))
    if (!isCacheableShellResponse(response, self.location.origin)) {
      throw new Error(`Narad shell asset unavailable: ${path}`)
    }
    if (path === SHELL_URL && !(await response.clone().text()).includes(SHELL_MARKER)) {
      throw new Error('The page at / is not the Narad app (a sign-in page?); not caching it')
    }
    await cache.put(path, response)
  }))
}

async function showPush(payload) {
  if (payload.unread !== null && self.navigator && 'setAppBadge' in self.navigator) {
    const badge = payload.unread > 0 ? self.navigator.setAppBadge(payload.unread) : self.navigator.clearAppBadge()
    await badge.catch(() => undefined)
  }
  const windows = await self.clients.matchAll({ type: 'window', includeUncontrolled: true })
  for (const client of windows) client.postMessage({ type: 'narad:push', payload })
  // The person is looking at Narad: the app shows it in place, no system banner.
  if (windows.some(client => client.focused)) return
  await self.registration.showNotification(payload.title, {
    body: payload.body,
    tag: payload.tag || undefined,
    renotify: Boolean(payload.tag),
    requireInteraction: payload.kind === 'approval_request',
    icon: '/icons/icon-192.png',
    badge: '/icons/badge-96.png',
    data: { url: payload.url, id: payload.id, kind: payload.kind },
  })
}

async function openApp(url) {
  const windows = await self.clients.matchAll({ type: 'window', includeUncontrolled: true })
  const app = windows.find(client => {
    const path = new URL(client.url).pathname
    return path === '/' || path === '/index.html'
  })
  if (app) {
    await app.focus()
    app.postMessage({ type: 'narad:open', url })
    return
  }
  await self.clients.openWindow(url)
}

// ── Wiring ──────────────────────────────────────────────────────────────────

self.addEventListener('install', event => {
  event.waitUntil(precacheShell())
})

self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    const names = await caches.keys()
    await Promise.all(
      names.filter(name => name.startsWith(SHELL_PREFIX) && name !== SHELL_CACHE).map(name => caches.delete(name))
    )
    await self.clients.claim()
  })())
})

self.addEventListener('message', event => {
  if (event.data && event.data.type === 'SKIP_WAITING') self.skipWaiting()
})

self.addEventListener('fetch', event => {
  const strategy = requestStrategy(event.request, self.location.origin)
  if (strategy === 'navigate') event.respondWith(handleNavigation(event))
  else if (strategy === 'asset') event.respondWith(handleAsset(event, false))
  else if (strategy === 'refresh') event.respondWith(handleAsset(event, true))
  else if (strategy === 'font') event.respondWith(handleFont(event))
  // passthrough: no respondWith, so the browser fetches it normally, uncached.
})

self.addEventListener('push', event => {
  const payload = parsePushPayload(event.data ? event.data.text() : '', self.location.origin)
  event.waitUntil(showPush(payload))
})

self.addEventListener('notificationclick', event => {
  event.notification.close()
  const url = safeAppUrl(event.notification.data && event.notification.data.url, self.location.origin)
  event.waitUntil(openApp(url))
})

self.addEventListener('pushsubscriptionchange', event => {
  // Keep a subscription alive when the browser rotates it; the app registers
  // the new endpoint with Narad the next time it opens.
  const options = event.oldSubscription && event.oldSubscription.options
  if (options && options.applicationServerKey) {
    event.waitUntil(self.registration.pushManager.subscribe(options).catch(() => undefined))
  }
})
