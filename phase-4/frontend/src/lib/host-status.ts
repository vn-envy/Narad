/**
 * Is Narad's Mac reachable? apiFetch reports every response and network
 * failure here, so the app can say "Narad's Mac is asleep" instead of failing
 * one request at a time.
 *
 * Through Cloudflare, an unreachable Mac shows up as a network error or as
 * Cloudflare's own 502/504/52x/530. Narad itself uses 503 for "this feature
 * is not ready", so 503 does not count.
 */
import { apiPath } from './api'

type Listener = (reachable: boolean) => void

let reachable = true
const listeners = new Set<Listener>()

export class HostUnreachableError extends Error {
  constructor() {
    super("Narad's Mac is asleep or offline")
    this.name = 'HostUnreachableError'
  }
}

export function isUnreachableStatus(status: number): boolean {
  return status === 502 || status === 504 || (status >= 520 && status <= 530)
}

export function hostReachable(): boolean {
  return reachable
}

function update(next: boolean): void {
  if (next === reachable) return
  reachable = next
  for (const listener of listeners) listener(next)
}

export function reportHostResponse(status: number): void {
  update(!isUnreachableStatus(status))
}

export function reportHostFailure(error: unknown): void {
  // A request the app cancelled (Stop, a closed screen) says nothing about the Mac.
  if (error instanceof DOMException && error.name === 'AbortError') return
  update(false)
}

export function onHostReachability(listener: Listener): () => void {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}

/** Ask /health directly; updates everyone listening. */
export async function probeHost(): Promise<boolean> {
  try {
    const response = await fetch(apiPath('/health'), { cache: 'no-store' })
    update(!isUnreachableStatus(response.status))
  } catch (error) {
    reportHostFailure(error)
  }
  return reachable
}
