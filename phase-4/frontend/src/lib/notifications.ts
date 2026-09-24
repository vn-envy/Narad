/**
 * Notifications: the Activity inbox, Web Push on this phone, notification
 * preferences and care circles. Every call acts on the signed-in profile;
 * the server derives it from the session.
 */
import { apiFetch, apiUrl } from './api'
import { serviceWorkerReady } from './pwa'

export interface InboxItem {
  id: string
  ts: string
  kind: string
  title: string
  body: string
  priority?: string
  read: boolean
  source?: string
  data?: Record<string, unknown>
  shared_from?: string
  shared_from_name?: string
}

export interface InboxPayload {
  items: InboxItem[]
  unread: number
}

export interface NotificationPreferences {
  lock_screen_details: boolean
  quiet_hours: { enabled: boolean; start: string; end: string }
  medicine_in_quiet_hours: boolean
  timezone: string | null
  updated_at?: string
}

export interface CareGrant {
  carer: string
  carer_name?: string
  kinds: string[]
  granted_at?: string | null
}

export interface CareCircle {
  subject: string
  grants: CareGrant[]
  kinds: Array<{ id: string; label: string }>
}

export interface SharedWithMe {
  subject: string
  subject_name: string
  kinds: string[]
  granted_at?: string | null
}

export interface PushDevice {
  id: string
  label: string
  endpoint: string
  service: string
  created_at?: string | null
  last_success_at?: string | null
  last_error?: string | null
}

async function readJson<T>(response: Response): Promise<T> {
  const payload = await response.json().catch(() => ({})) as T & { detail?: string }
  if (!response.ok) throw new Error(payload.detail || `Request failed (${response.status})`)
  return payload
}

function jsonInit(method: string, body: unknown): RequestInit {
  return { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
}

export async function fetchInbox(limit = 100): Promise<InboxPayload> {
  return readJson<InboxPayload>(await apiFetch(apiUrl('/inbox', { limit })))
}

export async function markInboxRead(ids: string[]): Promise<void> {
  if (ids.length === 0) return
  await readJson(await apiFetch('/inbox/mark-read', jsonInit('POST', { ids })))
}

export async function fetchPreferences(): Promise<NotificationPreferences> {
  return readJson<NotificationPreferences>(await apiFetch('/notifications/preferences'))
}

export async function savePreferences(changes: Partial<NotificationPreferences>): Promise<NotificationPreferences> {
  return readJson<NotificationPreferences>(await apiFetch('/notifications/preferences', jsonInit('PUT', changes)))
}

export async function fetchCareCircle(): Promise<CareCircle> {
  return readJson<CareCircle>(await apiFetch('/care-circle'))
}

export async function saveCareCircle(grants: CareGrant[]): Promise<CareCircle> {
  const body = { grants: grants.map(grant => ({ carer: grant.carer, kinds: grant.kinds })) }
  return readJson<CareCircle>(await apiFetch('/care-circle', jsonInit('PUT', body)))
}

export async function fetchSharedWithMe(): Promise<SharedWithMe[]> {
  return (await readJson<{ shared: SharedWithMe[] }>(await apiFetch('/care-circle/shared-with-me'))).shared
}

export async function leaveCareCircle(subject: string): Promise<void> {
  await readJson(await apiFetch(`/care-circle/shared-with-me/${encodeURIComponent(subject)}`, { method: 'DELETE' }))
}

export async function fetchPushDevices(): Promise<PushDevice[]> {
  return (await readJson<{ devices: PushDevice[] }>(await apiFetch('/push/devices'))).devices
}

// ── Web Push on this phone ──────────────────────────────────────────────────

export type PushSupport = 'supported' | 'unsupported' | 'insecure' | 'ios-needs-install' | 'dev'

const ENABLED_KEY = 'narad_push_enabled'

function enabledKey(userId: string): string {
  return `${ENABLED_KEY}:${userId}`
}

function rememberEnabled(userId: string, enabled: boolean): void {
  try {
    if (enabled) localStorage.setItem(enabledKey(userId), '1')
    else localStorage.removeItem(enabledKey(userId))
  } catch { /* storage is optional */ }
}

function wasEnabled(userId: string): boolean {
  try { return localStorage.getItem(enabledKey(userId)) === '1' } catch { return false }
}

export function pushSupport(): PushSupport {
  if (typeof window === 'undefined') return 'unsupported'
  if (!window.isSecureContext) return 'insecure'
  const capable = 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window
  if (!capable) {
    const ios = /iPad|iPhone|iPod/.test(navigator.userAgent)
    const standalone = window.matchMedia?.('(display-mode: standalone)').matches
    return ios && !standalone ? 'ios-needs-install' : 'unsupported'
  }
  return import.meta.env.PROD ? 'supported' : 'dev'
}

export function notificationPermission(): NotificationPermission | 'unsupported' {
  return typeof Notification === 'undefined' ? 'unsupported' : Notification.permission
}

function base64UrlToBytes(value: string): Uint8Array<ArrayBuffer> {
  const padded = (value + '='.repeat((4 - (value.length % 4)) % 4)).replace(/-/g, '+').replace(/_/g, '/')
  const raw = atob(padded)
  const bytes = new Uint8Array(new ArrayBuffer(raw.length))
  for (let index = 0; index < raw.length; index += 1) bytes[index] = raw.charCodeAt(index)
  return bytes
}

function bytesToBase64Url(buffer: ArrayBuffer | null): string {
  if (!buffer) return ''
  let raw = ''
  for (const byte of new Uint8Array(buffer)) raw += String.fromCharCode(byte)
  return btoa(raw).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

/** A short, human label for this device, shown in the profile's device list. */
export function thisDeviceLabel(): string {
  const agent = navigator.userAgent
  const platform = /Android/i.test(agent) ? 'Android phone'
    : /iPhone/i.test(agent) ? 'iPhone'
      : /iPad/i.test(agent) ? 'iPad'
        : /Macintosh/i.test(agent) ? 'Mac'
          : /Windows/i.test(agent) ? 'Windows PC'
            : 'This device'
  const browser = /Edg\//.test(agent) ? 'Edge' : /Firefox\//.test(agent) ? 'Firefox'
    : /Chrome\//.test(agent) ? 'Chrome' : /Safari\//.test(agent) ? 'Safari' : ''
  return browser ? `${platform} · ${browser}` : platform
}

export async function currentPushSubscription(): Promise<PushSubscription | null> {
  if (pushSupport() !== 'supported') return null
  const registration = await serviceWorkerReady()
  return registration ? registration.pushManager.getSubscription() : null
}

async function vapidKey(): Promise<string> {
  const payload = await readJson<{ public_key: string; available: boolean }>(await apiFetch('/push/vapid-public-key'))
  if (!payload.available) throw new Error('Web Push is not installed on Narad\'s Mac yet.')
  return payload.public_key
}

async function registerWithNarad(subscription: PushSubscription): Promise<void> {
  await readJson(await apiFetch('/push/subscribe', jsonInit('POST', {
    subscription: subscription.toJSON(),
    device_label: thisDeviceLabel(),
  })))
}

/** Subscribe this browser, reusing its subscription when it matches Narad's current key. */
async function subscribe(publicKey: string): Promise<PushSubscription> {
  const registration = await serviceWorkerReady()
  if (!registration) throw new Error('Narad\'s offline helper is not running yet. Reload the app and try again.')
  let subscription = await registration.pushManager.getSubscription()
  if (subscription && bytesToBase64Url(subscription.options.applicationServerKey) !== publicKey) {
    await subscription.unsubscribe()
    subscription = null
  }
  return subscription ?? registration.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: base64UrlToBytes(publicKey),
  })
}

/** Turn notifications on for this phone. Call only from a tap: it asks for permission. */
export async function enablePush(userId: string): Promise<PushSubscription> {
  const permission = await Notification.requestPermission()
  if (permission !== 'granted') {
    throw new Error(permission === 'denied'
      ? 'Notifications are blocked for Narad. Allow them in Chrome: tap the lock icon by the address, then Permissions.'
      : 'Notifications were not allowed.')
  }
  const subscription = await subscribe(await vapidKey())
  await registerWithNarad(subscription)
  rememberEnabled(userId, true)
  return subscription
}

/** Turn notifications off for this phone: Narad forgets it and the browser unsubscribes. */
export async function disablePush(userId: string): Promise<void> {
  rememberEnabled(userId, false)
  const subscription = await currentPushSubscription()
  if (!subscription) return
  await apiFetch('/push/subscribe', jsonInit('DELETE', { endpoint: subscription.endpoint })).catch(() => undefined)
  await subscription.unsubscribe().catch(() => false)
}

/** Keep this phone registered after a browser key rotation, a new Narad key or a re-sign-in. */
export async function syncPushSubscription(userId: string): Promise<void> {
  if (!wasEnabled(userId) || pushSupport() !== 'supported' || notificationPermission() !== 'granted') return
  try {
    const subscription = await subscribe(await vapidKey())
    await registerWithNarad(subscription)
  } catch {
    // Offline or not ready: the next app start tries again.
  }
}

export async function sendTestPush(endpoint?: string): Promise<{ sent: number; results: Array<PushDevice & { status: string }> }> {
  return readJson(await apiFetch('/push/test', jsonInit('POST', { endpoint: endpoint ?? '' })))
}

export function setAppBadge(count: number): void {
  const nav = navigator as Navigator & { setAppBadge?: (count?: number) => Promise<void>; clearAppBadge?: () => Promise<void> }
  if (count > 0) nav.setAppBadge?.(count).catch(() => undefined)
  else nav.clearAppBadge?.().catch(() => undefined)
}
