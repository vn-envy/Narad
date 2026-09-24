/**
 * The service worker (public/sw.js): registration, updates, and messages.
 *
 * Registered only in a production build over a secure context (HTTPS, or
 * localhost on the Mac). A new build installs in the background; it takes over
 * on the next navigation, or right away when the person taps "Reload".
 */
import { toast } from 'sonner'

/** window event: open this same-origin url inside the app (notification taps). */
export const OPEN_URL_EVENT = 'narad:open-url'
/** window event: a push arrived while the app is open (detail: the payload). */
export const PUSH_EVENT = 'narad:push'

export interface PushPayload {
  title: string
  body: string
  url: string
  tag: string
  kind: string
  id: string
  unread: number | null
}

const UPDATE_CHECK_MS = 30 * 60_000

let registration: Promise<ServiceWorkerRegistration | null> | null = null

export function serviceWorkerEnabled(): boolean {
  return import.meta.env.PROD
    && typeof navigator !== 'undefined'
    && 'serviceWorker' in navigator
    && window.isSecureContext
}

/** The active registration once the worker is running (null when there is none). */
export async function serviceWorkerReady(timeoutMs = 8000): Promise<ServiceWorkerRegistration | null> {
  if (!serviceWorkerEnabled()) return null
  const ready = navigator.serviceWorker.ready
  const timeout = new Promise<null>(resolve => window.setTimeout(() => resolve(null), timeoutMs))
  return Promise.race([ready, timeout])
}

function offerUpdate(worker: ServiceWorker): void {
  toast('A new version of Narad is ready', {
    id: 'narad-update',
    duration: Infinity,
    action: { label: 'Reload', onClick: () => worker.postMessage({ type: 'SKIP_WAITING' }) },
  })
}

function watchForUpdates(current: ServiceWorkerRegistration): void {
  if (current.waiting && navigator.serviceWorker.controller) offerUpdate(current.waiting)
  current.addEventListener('updatefound', () => {
    const worker = current.installing
    worker?.addEventListener('statechange', () => {
      // "installed" with a controller means an older build is still running.
      if (worker.state === 'installed' && navigator.serviceWorker.controller) offerUpdate(worker)
    })
  })
  let lastCheck = Date.now()
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState !== 'visible' || Date.now() - lastCheck < UPDATE_CHECK_MS) return
    lastCheck = Date.now()
    current.update().catch(() => undefined)
  })
}

export function registerServiceWorker(): void {
  if (!serviceWorkerEnabled() || registration) return
  let controlled = Boolean(navigator.serviceWorker.controller)
  let reloading = false
  navigator.serviceWorker.addEventListener('controllerchange', () => {
    // The first install claims the page; only a replaced build needs a reload.
    if (!controlled) {
      controlled = true
      return
    }
    if (reloading) return
    reloading = true
    window.location.reload()
  })
  navigator.serviceWorker.addEventListener('message', event => {
    const data = event.data as { type?: string; url?: unknown; payload?: PushPayload } | null
    if (data?.type === 'narad:open' && typeof data.url === 'string') {
      window.dispatchEvent(new CustomEvent(OPEN_URL_EVENT, { detail: { url: data.url } }))
    } else if (data?.type === 'narad:push' && data.payload) {
      window.dispatchEvent(new CustomEvent(PUSH_EVENT, { detail: data.payload }))
    }
  })
  registration = navigator.serviceWorker
    .register('/sw.js', { scope: '/', updateViaCache: 'none' })
    .then(current => {
      watchForUpdates(current)
      return current
    })
    .catch(error => {
      console.warn('Narad service worker was not registered:', error)
      return null
    })
}
