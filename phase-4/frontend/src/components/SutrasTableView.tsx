import { useEffect, useState } from 'react'
import { apiFetch } from '@/lib/api'
import { avatarColour, AVATAR_COLOURS, AVATAR_NAMES } from '@/lib/avatara-constants'

interface Sutra {
  id: string
  avatar: string
  query: string
  result: string
  score: number
  status: 'pending' | 'active' | 'accepted' | 'reverted'
  ts: string
  ttl_days?: number
}

const STATUS_COLOURS = {
  active: '#065f46', pending: '#92610a', accepted: '#2d6cdf', reverted: '#57534e',
}

type SortKey = 'score' | 'ts' | 'avatar' | 'status'

export function SutrasTableView() {
  const [sutras, setSutras] = useState<Sutra[]>([])
  const [loading, setLoading] = useState(true)
  const [avatarFilter, setAvatarFilter] = useState<string | null>(null)
  const [statusFilter, setStatusFilter] = useState<string | null>(null)
  const [sortKey, setSortKey] = useState<SortKey>('score')
  const [sortAsc, setSortAsc] = useState(false)
  const [acting, setActing] = useState<string | null>(null)
  const [expandedId, setExpandedId] = useState<string | null>(null)

  const load = () => {
    setLoading(true)
    apiFetch('/sutras')
      .then(r => r.json())
      .then(d => { setSutras(d.sutras || []); setLoading(false) })
      .catch(() => setLoading(false))
  }

  useEffect(() => { load() }, [])

  const handleAction = async (id: string, action: 'accept' | 'revert') => {
    setActing(id)
    try {
      await apiFetch(`/sutras/${id}/${action}`, { method: 'POST' })
      load()
    } catch { /* noop */ } finally {
      setActing(null)
    }
  }

  const sorted = [...sutras]
    .filter(s => !avatarFilter || s.avatar === avatarFilter)
    .filter(s => !statusFilter || s.status === statusFilter)
    .sort((a, b) => {
      let cmp = 0
      if (sortKey === 'score') cmp = (b.score || 0) - (a.score || 0)
      else if (sortKey === 'ts') cmp = (b.ts || '').localeCompare(a.ts || '')
      else if (sortKey === 'avatar') cmp = a.avatar.localeCompare(b.avatar)
      else if (sortKey === 'status') cmp = a.status.localeCompare(b.status)
      return sortAsc ? -cmp : cmp
    })

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) setSortAsc(v => !v)
    else { setSortKey(key); setSortAsc(false) }
  }

  const sortIndicator = (key: SortKey) => sortKey === key ? (sortAsc ? ' ↑' : ' ↓') : ''

  function ago(ts: string) {
    if (!ts) return ''
    const diff = Date.now() - new Date(ts).getTime()
    const d = Math.floor(diff / 86400000)
    return d < 1 ? 'today' : `${d}d ago`
  }

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Filter bar */}
      <div className="flex items-center gap-2 px-4 py-2.5 flex-shrink-0 flex-wrap" style={{ borderBottom: '1px solid rgba(252,250,242,0.08)' }}>
        {AVATAR_NAMES.map(name => (
          <button
            key={name}
            onClick={() => setAvatarFilter(avatarFilter === name ? null : name)}
            className="text-[9px] font-bold px-2 py-0.5 rounded-full transition-all"
            style={{
              background: avatarFilter === name ? `${AVATAR_COLOURS[name]}44` : 'rgba(252,250,242,0.06)',
              color: avatarFilter === name ? AVATAR_COLOURS[name] : 'rgba(252,250,242,0.4)',
              border: `1px solid ${avatarFilter === name ? AVATAR_COLOURS[name] : 'transparent'}`,
            }}
          >
            {name.slice(0, 2).toUpperCase()}
          </button>
        ))}
        <div className="w-px h-4 mx-1" style={{ background: 'rgba(252,250,242,0.12)' }} />
        {(['active','pending','accepted','reverted'] as const).map(s => (
          <button
            key={s}
            onClick={() => setStatusFilter(statusFilter === s ? null : s)}
            className="text-[9px] px-2 py-0.5 rounded-full transition-all capitalize"
            style={{
              background: statusFilter === s ? `${STATUS_COLOURS[s]}33` : 'rgba(252,250,242,0.06)',
              color: statusFilter === s ? STATUS_COLOURS[s] : 'rgba(252,250,242,0.35)',
              border: `1px solid ${statusFilter === s ? STATUS_COLOURS[s] : 'transparent'}`,
            }}
          >
            {s}
          </button>
        ))}
        <span className="ml-auto text-[10px] font-mono opacity-30">{sorted.length} sutras</span>
      </div>

      {/* Table header */}
      <div className="grid px-4 py-2 text-[9px] font-semibold uppercase tracking-wider flex-shrink-0"
        style={{ gridTemplateColumns: '32px 1fr 80px 80px 60px', color: 'rgba(252,250,242,0.3)', borderBottom: '1px solid rgba(252,250,242,0.06)' }}>
        <span />
        <button className="text-left hover:opacity-70 transition-opacity" onClick={() => toggleSort('avatar')}>Avatar{sortIndicator('avatar')}</button>
        <button className="text-center hover:opacity-70 transition-opacity" onClick={() => toggleSort('score')}>Score{sortIndicator('score')}</button>
        <button className="text-center hover:opacity-70 transition-opacity" onClick={() => toggleSort('status')}>Status{sortIndicator('status')}</button>
        <span className="text-center">Actions</span>
      </div>

      {/* Table rows */}
      <div className="flex-1 overflow-y-auto">
        {loading ? (
          <div className="flex items-center justify-center h-32 opacity-40 text-sm" style={{ color: 'rgba(252,250,242,0.4)' }}>Loading…</div>
        ) : sorted.length === 0 ? (
          <div className="flex items-center justify-center h-32 opacity-40 text-sm" style={{ color: 'rgba(252,250,242,0.4)' }}>No sutras match</div>
        ) : sorted.map(sutra => {
          const colour = avatarColour(sutra.avatar)
          const statusColour = STATUS_COLOURS[sutra.status] || '#57534e'
          const isExpanded = expandedId === sutra.id
          return (
            <div
              key={sutra.id}
              className="transition-colors"
              style={{ borderBottom: '1px solid rgba(252,250,242,0.04)' }}
            >
              <div
                className="grid px-4 py-2.5 items-center cursor-pointer hover:bg-white/[0.02] transition-colors"
                style={{ gridTemplateColumns: '32px 1fr 80px 80px 60px' }}
                onClick={() => setExpandedId(isExpanded ? null : sutra.id)}
              >
                {/* Avatar badge */}
                <div
                  className="w-6 h-6 rounded-full flex items-center justify-center text-[8px] font-bold"
                  style={{ background: `${colour}33`, color: colour }}
                >
                  {sutra.avatar.slice(0, 2).toUpperCase()}
                </div>

                {/* Query preview */}
                <div className="min-w-0 pr-3">
                  <div className="text-[11px] truncate" style={{ color: 'rgba(252,250,242,0.8)' }}>
                    {sutra.query?.slice(0, 90) || '—'}
                  </div>
                  <div className="text-[9px] opacity-30 font-mono mt-0.5">{ago(sutra.ts)}</div>
                </div>

                {/* Score */}
                <div className="flex flex-col items-center gap-1">
                  <span className="text-[11px] font-mono font-semibold" style={{ color: sutra.score >= 0.8 ? '#065f46' : sutra.score >= 0.6 ? '#92610a' : '#57534e' }}>
                    {sutra.score?.toFixed(2)}
                  </span>
                  <div className="w-10 h-0.5 rounded-full" style={{ background: 'rgba(252,250,242,0.1)' }}>
                    <div className="h-full rounded-full" style={{ width: `${(sutra.score || 0) * 100}%`, background: sutra.score >= 0.8 ? '#065f46' : '#92610a' }} />
                  </div>
                </div>

                {/* Status */}
                <div className="flex justify-center">
                  <span
                    className="text-[9px] px-2 py-0.5 rounded-full font-semibold capitalize"
                    style={{ background: `${statusColour}33`, color: statusColour }}
                  >
                    {sutra.status}
                  </span>
                </div>

                {/* Actions */}
                <div className="flex items-center justify-center gap-1.5" onClick={e => e.stopPropagation()}>
                  {sutra.status !== 'accepted' && sutra.status !== 'reverted' && (
                    <>
                      <button
                        onClick={() => handleAction(sutra.id, 'accept')}
                        disabled={acting === sutra.id}
                        className="text-[10px] font-bold hover:opacity-80 disabled:opacity-30 transition-opacity"
                        style={{ color: '#065f46' }}
                        title="Accept"
                      >✓</button>
                      <button
                        onClick={() => handleAction(sutra.id, 'revert')}
                        disabled={acting === sutra.id}
                        className="text-[10px] font-bold hover:opacity-80 disabled:opacity-30 transition-opacity"
                        style={{ color: '#c2410c' }}
                        title="Revert"
                      >↩</button>
                    </>
                  )}
                </div>
              </div>

              {/* Expanded result */}
              {isExpanded && (
                <div className="px-10 pb-3 text-[10px] leading-relaxed" style={{ color: 'rgba(252,250,242,0.45)', borderTop: '1px solid rgba(252,250,242,0.04)' }}>
                  {sutra.result?.slice(0, 400) || '—'}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
