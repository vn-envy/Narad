/**
 * "Narad's Mac is asleep" — shown instead of a blank page or a browser error
 * when the host Mac (or its tunnel) cannot be reached.
 *
 * Try again reloads the page: that also lets Cloudflare Access show its
 * sign-in page when the phone's Access session has simply expired.
 */
import { useEffect, useState } from 'react'
import { CloudOff, LoaderCircle, RefreshCw, WifiOff } from 'lucide-react'
import { hostReachable, onHostReachability, probeHost } from '@/lib/host-status'

const RETRY_EVERY_MS = 30_000

function offlineText() {
  return typeof navigator !== 'undefined' && !navigator.onLine
    ? { title: 'Your phone is offline', detail: 'Connect to the internet, then try again.' }
    : { title: "Narad's Mac is asleep or offline", detail: "It'll be back soon. Your chats and reminders are safe on the Mac." }
}

export function HostOfflineScreen() {
  const text = offlineText()
  const Icon = navigator.onLine ? CloudOff : WifiOff
  return (
    <main className="family-gate" aria-live="polite">
      <section className="family-gate-card" style={{ width: 'min(440px, 100%)', textAlign: 'center' }}>
        <div style={{ width: 54, height: 54, margin: '6vh auto 18px', display: 'grid', placeItems: 'center', borderRadius: '18px 18px 18px 6px', background: 'rgba(194,65,12,0.18)', color: '#e8773f' }}>
          <Icon size={24} />
        </div>
        <h1 style={{ margin: 0, fontFamily: 'var(--font-hero)', fontSize: 26, lineHeight: 1.15 }}>{text.title}</h1>
        <p style={{ margin: '10px auto 24px', maxWidth: 320, color: 'rgba(252,250,242,0.72)', fontSize: 16, lineHeight: 1.55 }}>{text.detail}</p>
        <button type="button" className="family-primary" style={{ width: '100%' }} onClick={() => window.location.reload()}>
          <RefreshCw size={15} /> Try again
        </button>
      </section>
    </main>
  )
}

/** A slim bar over the app while the Mac is unreachable; it checks again on its own. */
export function HostOfflineBanner() {
  const [reachable, setReachable] = useState(hostReachable())
  const [checking, setChecking] = useState(false)

  useEffect(() => onHostReachability(setReachable), [])

  useEffect(() => {
    if (reachable) return
    const timer = window.setInterval(() => { void probeHost() }, RETRY_EVERY_MS)
    const online = () => { void probeHost() }
    window.addEventListener('online', online)
    return () => {
      window.clearInterval(timer)
      window.removeEventListener('online', online)
    }
  }, [reachable])

  if (reachable) return null
  const text = offlineText()
  return (
    <div
      role="status"
      style={{
        position: 'relative',
        zIndex: 5,
        flexShrink: 0,
        padding: 'max(8px, env(safe-area-inset-top)) 12px 8px',
        display: 'flex',
        alignItems: 'center',
        gap: 10,
        background: 'rgba(33,31,28,0.97)',
        borderBottom: '1px solid rgba(232,119,63,0.35)',
        color: 'rgba(252,250,242,0.88)',
        fontSize: 14,
        lineHeight: 1.4,
        boxShadow: '0 6px 18px rgba(0,0,0,0.25)',
      }}
    >
      <CloudOff size={15} style={{ flex: '0 0 auto', color: '#e8773f' }} />
      <span style={{ flex: 1, minWidth: 0 }}>
        <strong style={{ fontWeight: 650 }}>{text.title}.</strong> {text.detail}
      </span>
      <button
        type="button"
        disabled={checking}
        onClick={async () => {
          setChecking(true)
          // Still no answer: reload, which also brings up Cloudflare Access
          // sign-in when an expired Access session is the real cause.
          if (!(await probeHost())) window.location.reload()
          setChecking(false)
        }}
        style={{
          flex: '0 0 auto',
          minHeight: 44,
          padding: '0 14px',
          borderRadius: 9,
          border: '1px solid rgba(252,250,242,0.2)',
          background: 'transparent',
          color: 'inherit',
          fontSize: 14,
          fontWeight: 650,
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          cursor: 'pointer',
        }}
      >
        {checking ? <LoaderCircle size={13} className="animate-spin" /> : <RefreshCw size={13} />} Try again
      </button>
    </div>
  )
}
