import { useEffect, useState, useCallback } from 'react'
import { apiFetch, apiUrl } from '@/lib/api'
import { avatarColour } from '@/lib/avatara-constants'
import { relativeTime } from '@/lib/format-time'

interface KarmaEvent {
  id: string
  ts: string
  action: 'promoted' | 'accepted' | 'reverted' | 'expired'
  sutra_id: string
  avatar: string
  detail: string
}

interface KarmaSummary {
  total_events: number
  by_action: Record<string, number>
  recent: KarmaEvent[]
}

const ACTION_META: Record<string, { bg: string; color: string; label: string; meaning: string }> = {
  promoted: {
    bg: '#065f46', color: '#fcfaf2',
    label: 'Promoted',
    meaning: 'Tapas scored ≥ 0.75 — entered 24h cooldown as a new Sutra.',
  },
  accepted: {
    bg: '#2d2a26', color: '#fcfaf2',
    label: 'Accepted',
    meaning: 'You approved. Pattern is now injected into matching avatar prompts.',
  },
  reverted: {
    bg: '#c2410c', color: '#fcfaf2',
    label: 'Reverted',
    meaning: 'You rejected this Sutra. It will never influence responses.',
  },
  expired: {
    bg: '#78716c', color: '#fcfaf2',
    label: 'Expired',
    meaning: 'TTL exceeded (90 days) — removed from active context.',
  },
}

interface Props {
  userId?: string
  compact?: boolean
}

export function KarmaPanel({ userId = 'default', compact = false }: Props) {
  const [summary, setSummary] = useState<KarmaSummary | null>(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(() => {
    setLoading(true)
    apiFetch(apiUrl('/karma', { user_id: userId }))
      .then(r => r.ok ? r.json() : null)
      .then(d => { setSummary(d); setLoading(false) })
      .catch(() => setLoading(false))
  }, [userId])

  useEffect(() => { load() }, [load])

  const events = summary?.recent ?? []
  const byAction = summary?.by_action ?? {}
  const totalEvents = summary?.total_events ?? 0

  return (
    <div style={{
      flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden',
      background: 'var(--paper)', fontFamily: 'var(--font-body)',
    }}>
      {/* Header */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 10,
        padding: '10px 16px', flexShrink: 0,
        background: 'var(--kajal)', borderBottom: '1px solid rgba(245,235,215,0.1)',
      }}>
        <span style={{ fontFamily: 'var(--font-deva)', fontSize: 18, color: 'var(--marigold)' }}>कर्म</span>
        <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--paper)', letterSpacing: 1 }}>KARMA LOG</span>
        {totalEvents > 0 && (
          <span style={{
            marginLeft: 4, fontFamily: 'var(--font-mono)', fontSize: 10,
            color: 'rgba(245,235,215,0.45)',
          }}>{totalEvents} events</span>
        )}
        <button
          onClick={load}
          style={{
            marginLeft: 'auto', fontSize: 10, padding: '2px 8px',
            background: 'rgba(245,235,215,0.08)', border: '1px solid rgba(245,235,215,0.15)',
            borderRadius: 4, color: 'rgba(245,235,215,0.55)', cursor: 'pointer',
          }}
        >↻ Refresh</button>
      </div>
      {/* Counts row */}
      {Object.keys(byAction).length > 0 && (
        <div style={{
          display: 'flex', gap: 8, padding: '10px 16px', flexShrink: 0,
          borderBottom: '1px solid rgba(26,24,21,0.08)',
        }}>
          {Object.entries(byAction).map(([action, count]) => {
            const meta = ACTION_META[action] ?? { bg: '#78716c', color: '#fcfaf2', label: action, meaning: '' }
            return (
              <div key={action} style={{
                display: 'flex', flexDirection: 'column', alignItems: 'center',
                padding: '6px 14px', borderRadius: 6, background: meta.bg, minWidth: 60,
              }}>
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 20, fontWeight: 700, lineHeight: 1, color: meta.color }}>
                  {count}
                </span>
                <span style={{ fontSize: 9, marginTop: 2, color: meta.color, opacity: 0.85 }}>
                  {meta.label}
                </span>
              </div>
            )
          })}
        </div>
      )}

      {/* Legend (collapsed in compact mode) */}
      {!compact && (
        <div style={{
          padding: '10px 16px', flexShrink: 0,
          background: 'rgba(26,24,21,0.03)', borderBottom: '1px solid rgba(26,24,21,0.07)',
        }}>
          <p style={{ fontSize: 11, lineHeight: 1.55, margin: '0 0 8px', color: 'rgba(26,24,21,0.65)' }}>
            <strong>Karma</strong> is the append-only audit trail of every change to your Sutra bank —
            promotions, acceptances, reversions, and expirations.
          </p>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
            {Object.entries(ACTION_META).map(([action, meta]) => (
              <div key={action} style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
                <span style={{
                  fontSize: 9, fontWeight: 700, padding: '2px 6px', borderRadius: 3,
                  background: meta.bg, color: meta.color, whiteSpace: 'nowrap', flexShrink: 0, marginTop: 1,
                }}>
                  {meta.label}
                </span>
                <span style={{ fontSize: 10, color: 'rgba(26,24,21,0.55)', lineHeight: 1.4 }}>
                  {meta.meaning}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Section label */}
      <div style={{ padding: '6px 16px 4px', flexShrink: 0 }}>
        <span style={{ fontSize: 10, fontWeight: 600, letterSpacing: 0.5, color: 'rgba(26,24,21,0.4)', textTransform: 'uppercase' }}>
          Recent Events · newest first
        </span>
      </div>

      {/* Timeline — scrollable */}
      <div style={{ flex: 1, overflow: 'auto', padding: '0 16px 16px' }}>
        {loading && (
          <p style={{ fontSize: 11, color: 'rgba(26,24,21,0.35)', textAlign: 'center', padding: '24px 0' }}>
            Loading karma log…
          </p>
        )}
        {!loading && events.length === 0 && (
          <p style={{ fontSize: 11, color: 'rgba(26,24,21,0.35)', textAlign: 'center', padding: '24px 0' }}>
            No karma events yet.{' '}
            <span style={{ opacity: 0.6 }}>Sutra changes will appear here.</span>
          </p>
        )}
        {events.map((e, i) => {
          const meta = ACTION_META[e.action] ?? { bg: '#78716c', color: '#fcfaf2', label: e.action, meaning: '' }
          const avatarColor = avatarColour(e.avatar, 'var(--kajal)')
          return (
            <div key={e.id} style={{ display: 'flex', gap: 10, paddingBottom: 10 }}>
              {/* Timeline spine */}
              <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', flexShrink: 0, width: 14 }}>
                <div style={{ width: 12, height: 12, borderRadius: '50%', background: meta.bg, flexShrink: 0, marginTop: 3 }} />
                {i < events.length - 1 && (
                  <div style={{ width: 1, flex: 1, minHeight: 18, marginTop: 3, background: 'rgba(26,24,21,0.1)' }} />
                )}
              </div>
              {/* Content */}
              <div style={{ flex: 1 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 3, flexWrap: 'wrap' }}>
                  <span style={{
                    fontSize: 9, fontWeight: 700, padding: '2px 6px', borderRadius: 3,
                    background: meta.bg, color: meta.color,
                  }}>{meta.label}</span>
                  <span style={{
                    fontSize: 9, fontWeight: 600, padding: '2px 6px', borderRadius: 3,
                    background: avatarColor, color: '#fcfaf2',
                  }}>{e.avatar}</span>
                  <span style={{
                    marginLeft: 'auto', fontFamily: 'var(--font-mono)', fontSize: 9,
                    color: 'rgba(26,24,21,0.35)',
                  }}>{relativeTime(e.ts)}</span>
                </div>
                {e.detail && (
                  <p style={{ fontSize: 11, margin: 0, lineHeight: 1.4, color: 'rgba(26,24,21,0.7)' }}>
                    {e.detail.slice(0, 120)}{e.detail.length > 120 ? '…' : ''}
                  </p>
                )}
                <p style={{ fontFamily: 'var(--font-mono)', fontSize: 8, color: 'rgba(26,24,21,0.28)', marginTop: 2, marginBottom: 0 }}>
                  {e.sutra_id.slice(0, 8)}…
                </p>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
