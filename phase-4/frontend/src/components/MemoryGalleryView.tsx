import { useEffect, useState } from 'react'
import { apiFetch } from '@/lib/api'
import { avatarColour, AVATAR_COLOURS, AVATAR_NAMES } from '@/lib/avatara-constants'
import { relativeTime as ago } from '@/lib/format-time'

interface MemoryEntry {
  id: string
  avatar: string
  text: string
  created_at: string
  type: string
}
const TYPE_COLOURS: Record<string, string> = {
  decision: '#2d6cdf', feature: '#065f46', goal: '#fcd34d',
  insight: '#57534e', context: '#78716c',
}
const DAYS_OPTIONS = [
  { label: 'All time', value: 0 },
  { label: 'Today', value: 1 },
  { label: 'This week', value: 7 },
  { label: 'This month', value: 30 },
]
const TYPE_OPTIONS = ['decision','feature','goal','insight']

interface Props { userId: string; notionEnabled?: boolean; notionUrl?: string | null }

export function MemoryGalleryView({ userId, notionEnabled, notionUrl }: Props) {
  const [entries, setEntries] = useState<MemoryEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [avatarFilter, setAvatarFilter] = useState<string | null>(null)
  const [daysFilter, setDaysFilter] = useState(0)
  const [typeFilter, setTypeFilter] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<string | null>(null)

  useEffect(() => {
    setLoading(true)
    const params = new URLSearchParams({ user_id: userId, limit: '60' })
    if (avatarFilter) params.set('avatar', avatarFilter)
    if (daysFilter > 0) params.set('days', String(daysFilter))
    if (typeFilter) params.set('memory_type', typeFilter)
    apiFetch(`/memory?${params}`)
      .then(r => r.json())
      .then(d => { setEntries(Array.isArray(d) ? d : []); setLoading(false) })
      .catch(() => setLoading(false))
  }, [userId, avatarFilter, daysFilter, typeFilter])

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Filter bar */}
      <div className="flex items-center gap-2 px-4 py-2.5 flex-shrink-0 flex-wrap" style={{ borderBottom: '1px solid rgba(252,250,242,0.08)' }}>
        {/* Avatar filters */}
        <div className="flex items-center gap-1 flex-wrap">
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
        </div>

        <div className="w-px h-4 mx-1" style={{ background: 'rgba(252,250,242,0.12)' }} />

        {/* Days filter */}
        <select
          value={daysFilter}
          onChange={e => setDaysFilter(Number(e.target.value))}
          className="text-[10px] px-2 py-1 rounded outline-none"
          style={{ background: 'rgba(252,250,242,0.06)', color: 'rgba(252,250,242,0.6)', border: '1px solid rgba(252,250,242,0.1)' }}
        >
          {DAYS_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>

        {/* Type filters */}
        <div className="flex items-center gap-1">
          {TYPE_OPTIONS.map(t => (
            <button
              key={t}
              onClick={() => setTypeFilter(typeFilter === t ? null : t)}
              className="text-[9px] px-2 py-0.5 rounded-full transition-all"
              style={{
                background: typeFilter === t ? `${TYPE_COLOURS[t]}44` : 'rgba(252,250,242,0.06)',
                color: typeFilter === t ? TYPE_COLOURS[t] : 'rgba(252,250,242,0.35)',
                border: `1px solid ${typeFilter === t ? TYPE_COLOURS[t] : 'transparent'}`,
              }}
            >
              {t}
            </button>
          ))}
        </div>

        {/* Notion sync badge */}
        {notionEnabled && notionUrl && (
          <a
            href={notionUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="ml-auto flex items-center gap-1 text-[9px] px-2 py-0.5 rounded-full hover:opacity-80 transition-opacity"
            style={{ background: 'rgba(252,250,242,0.06)', color: 'rgba(252,250,242,0.4)', border: '1px solid rgba(252,250,242,0.1)' }}
          >
            <span>◈</span> Synced to Notion ↗
          </a>
        )}
      </div>

      {/* Gallery grid */}
      <div className="flex-1 overflow-y-auto p-4">
        {loading ? (
          <div className="flex items-center justify-center h-32 opacity-40 text-sm" style={{ color: 'rgba(252,250,242,0.4)' }}>Loading memories…</div>
        ) : entries.length === 0 ? (
          <div className="flex items-center justify-center h-32 opacity-40 text-sm" style={{ color: 'rgba(252,250,242,0.4)' }}>No memories match these filters</div>
        ) : (
          <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))' }}>
            {entries.map(entry => {
              const colour = avatarColour(entry.avatar)
              const typeColour = TYPE_COLOURS[entry.type] || '#57534e'
              const isExpanded = expanded === entry.id
              return (
                <div
                  key={entry.id}
                  className="rounded-lg p-3 cursor-pointer transition-all flex flex-col gap-2"
                  style={{
                    background: 'rgba(252,250,242,0.04)',
                    border: `1px solid rgba(252,250,242,0.08)`,
                    borderTop: `2px solid ${colour}`,
                  }}
                  onClick={() => setExpanded(isExpanded ? null : entry.id)}
                >
                  {/* Avatar + type row */}
                  <div className="flex items-center gap-1.5">
                    <div
                      className="w-5 h-5 rounded-full flex items-center justify-center text-[8px] font-bold flex-shrink-0"
                      style={{ background: `${colour}33`, color: colour }}
                    >
                      {entry.avatar.slice(0, 2).toUpperCase()}
                    </div>
                    <span className="text-[10px] font-semibold" style={{ color: 'rgba(252,250,242,0.5)' }}>
                      {entry.avatar}
                    </span>
                    <span
                      className="ml-auto text-[8px] px-1.5 py-0.5 rounded-full font-semibold"
                      style={{ background: `${typeColour}33`, color: typeColour }}
                    >
                      {entry.type}
                    </span>
                  </div>

                  {/* Memory text */}
                  <p
                    className="text-[11px] leading-snug"
                    style={{
                      color: 'rgba(252,250,242,0.75)',
                      display: '-webkit-box',
                      WebkitLineClamp: isExpanded ? undefined : 4,
                      WebkitBoxOrient: 'vertical' as any,
                      overflow: isExpanded ? 'visible' : 'hidden',
                    }}
                  >
                    {entry.text}
                  </p>

                  {/* Timestamp */}
                  <div className="text-[9px] font-mono mt-auto" style={{ color: 'rgba(252,250,242,0.25)' }}>
                    {ago(entry.created_at)}
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
