import { useState, useCallback, useRef, useEffect } from 'react'
import { apiFetch, apiUrl } from '@/lib/api'

export interface SearchResult {
  id: string
  type: 'memory' | 'sutra' | 'plan' | 'andon' | 'audit' | 'session'
  avatar: string
  preview: string
  ts: string
  nav?: string
  event?: string
  matched_signals?: string[] | null
}

interface Props {
  userId: string
  onNavigate?: (nav: string) => void
}

const TYPE_LABELS: Record<string, string> = {
  memory:  'memory',
  sutra:   'sutra',
  plan:    'task',
  andon:   'alert',
  audit:   'audit',
  session: 'session',
}

const TYPE_COLORS: Record<string, { bg: string; color: string }> = {
  memory:  { bg: 'rgba(242,142,28,0.15)',  color: 'var(--marigold)' },
  sutra:   { bg: 'rgba(242,193,78,0.18)',  color: '#b8830a' },
  plan:    { bg: 'rgba(46,125,79,0.15)',   color: 'var(--tulsi)' },
  andon:   { bg: 'rgba(229,90,31,0.15)',   color: 'var(--kesari)' },
  audit:   { bg: 'rgba(245,158,11,0.15)',  color: '#b45309' },
  session: { bg: 'rgba(30,42,94,0.15)',    color: 'var(--nila)' },
}

function TypeBadge({ type }: { type: string }) {
  const style = TYPE_COLORS[type] ?? { bg: 'rgba(0,0,0,0.08)', color: 'var(--kajal)' }
  return (
    <span style={{
      flexShrink: 0,
      fontSize: 9.5, fontWeight: 700, padding: '2px 5px', borderRadius: 3,
      textTransform: 'uppercase', letterSpacing: '0.4px', marginTop: 2,
      background: style.bg, color: style.color,
    }}>
      {TYPE_LABELS[type] ?? type}
    </span>
  )
}

export function SearchBar({ userId, onNavigate }: Props) {
  const [query, setQuery]       = useState('')
  const [results, setResults]   = useState<SearchResult[]>([])
  const [loading, setLoading]   = useState(false)
  const [open, setOpen]         = useState(false)
  const [selected, setSelected] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const search = useCallback(async (q: string) => {
    if (!q || q.trim().length < 2) { setResults([]); return }
    setLoading(true)
    try {
      const res = await apiFetch(apiUrl('/search', { q, user_id: userId, limit: 24 }))
      if (res.ok) {
        const data: SearchResult[] = await res.json()
        setResults(data)
        setSelected(0)
      }
    } catch { /* server offline — leave empty */ }
    finally { setLoading(false) }
  }, [userId])

  const handleChange = (val: string) => {
    setQuery(val)
    setOpen(val.length >= 2)
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => search(val), 280)
  }

  const handleSelect = (r: SearchResult) => {
    setOpen(false)
    setQuery('')
    if (r.nav && onNavigate) onNavigate(r.nav)
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (!open) return
    if (e.key === 'ArrowDown')  { e.preventDefault(); setSelected(s => Math.min(s + 1, results.length - 1)) }
    if (e.key === 'ArrowUp')    { e.preventDefault(); setSelected(s => Math.max(s - 1, 0)) }
    if (e.key === 'Enter')      { if (results[selected]) handleSelect(results[selected]) }
    if (e.key === 'Escape')     { setOpen(false); setQuery('') }
  }

  // Close on outside click
  const wrapRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  // ⌘K focus shortcut
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault()
        inputRef.current?.focus()
        setOpen(!!query)
      }
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [query])

  // Group results by type
  const grouped = results.reduce<Record<string, SearchResult[]>>((acc, r) => {
    const key = r.type
    if (!acc[key]) acc[key] = []
    acc[key].push(r)
    return acc
  }, {})

  const groupOrder = ['memory', 'sutra', 'plan', 'audit', 'andon', 'session']
  const orderedGroups = groupOrder.filter(k => grouped[k]?.length)

  return (
    <div ref={wrapRef} style={{ flex: 1, maxWidth: 520, position: 'relative' }}>
      {/* Input */}
      <div style={{ position: 'relative' }}>
        <span style={{
          position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)',
          color: open ? 'var(--marigold)' : 'rgba(26,24,21,0.35)', fontSize: 14, pointerEvents: 'none',
        }}>⌕</span>
        <input
          ref={inputRef}
          value={query}
          onChange={e => handleChange(e.target.value)}
          onKeyDown={handleKeyDown}
          onFocus={() => { if (query.length >= 2) setOpen(true) }}
          placeholder="Search Smriti, sessions, Karya, Karma…"
          style={{
            width: '100%',
            background: 'rgba(26,24,21,0.06)',
            border: `1px solid ${open ? 'var(--marigold)' : 'rgba(26,24,21,0.15)'}`,
            borderRadius: 8,
            padding: '6px 48px 6px 32px',
            color: 'var(--kajal)',
            fontFamily: 'var(--font-body)',
            fontSize: 12.5, outline: 'none',
            boxShadow: open ? '0 0 0 3px rgba(242,142,28,0.18)' : 'none',
            transition: 'border-color 0.15s, box-shadow 0.15s',
          }}
        />
        {query ? (
          <button
            onClick={() => { setQuery(''); setOpen(false) }}
            style={{
              position: 'absolute', right: 8, top: '50%', transform: 'translateY(-50%)',
              fontSize: 10, color: 'rgba(26,24,21,0.45)', background: 'rgba(26,24,21,0.08)',
              border: '1px solid rgba(26,24,21,0.12)', borderRadius: 3, padding: '1px 5px',
              cursor: 'pointer',
            }}
          >✕</button>
        ) : (
          <span style={{
            position: 'absolute', right: 8, top: '50%', transform: 'translateY(-50%)',
            fontFamily: 'var(--font-mono)', fontSize: 10,
            color: 'rgba(26,24,21,0.35)', background: 'rgba(26,24,21,0.06)',
            border: '1px solid rgba(26,24,21,0.12)', borderRadius: 3, padding: '1px 5px',
          }}>⌘K</span>
        )}
      </div>

      {/* Results dropdown */}
      {open && (
        <div style={{
          position: 'absolute', top: 'calc(100% + 6px)', left: 0,
          width: 640, maxHeight: 480,
          background: 'var(--paper)',
          border: '1px solid rgba(26,24,21,0.18)',
          borderRadius: 12, overflow: 'hidden',
          boxShadow: '0 16px 48px rgba(26,24,21,0.22)',
          zIndex: 1000, display: 'flex', flexDirection: 'column',
        }}>
          {/* Header */}
          <div style={{
            padding: '8px 14px', borderBottom: '1px solid rgba(26,24,21,0.1)',
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            fontSize: 11, color: 'rgba(26,24,21,0.45)', flexShrink: 0,
          }}>
            <span>
              {loading ? 'Searching…' : results.length > 0
                ? `${results.length} results across ${orderedGroups.length} store${orderedGroups.length !== 1 ? 's' : ''}`
                : query.length >= 2 ? 'No results' : ''
              }
            </span>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10 }}>ESC to close</span>
          </div>

          {/* Results body */}
          <div style={{ overflowY: 'auto', flex: 1 }}>
            {orderedGroups.map(groupKey => (
              <div key={groupKey}>
                <div style={{
                  padding: '8px 14px 4px',
                  fontSize: 10.5, fontWeight: 600, textTransform: 'uppercase',
                  letterSpacing: '0.5px', color: 'rgba(26,24,21,0.4)',
                  borderTop: '1px solid rgba(26,24,21,0.08)',
                  display: 'flex', alignItems: 'center', gap: 8,
                }}>
                  {groupKey === 'memory' && '🧠'}
                  {groupKey === 'sutra'  && '✦'}
                  {groupKey === 'plan'   && '📋'}
                  {groupKey === 'audit'  && '🔍'}
                  {groupKey === 'andon'  && '⚡'}
                  {groupKey === 'session' && '📂'}
                  {groupKey.charAt(0).toUpperCase() + groupKey.slice(1)}s
                  <span style={{ marginLeft: 'auto', fontWeight: 400 }}>
                    {grouped[groupKey].length} result{grouped[groupKey].length !== 1 ? 's' : ''}
                  </span>
                </div>

                {grouped[groupKey].map((r, idx) => {
                  const globalIdx = results.indexOf(r)
                  const isSelected = globalIdx === selected
                  return (
                    <div
                      key={r.id}
                      onClick={() => handleSelect(r)}
                      onMouseEnter={() => setSelected(globalIdx)}
                      style={{
                        display: 'flex', alignItems: 'flex-start', gap: 10,
                        padding: '7px 14px',
                        background: isSelected ? 'rgba(242,142,28,0.08)' : 'transparent',
                        cursor: 'pointer', transition: 'background 0.1s',
                        borderLeft: isSelected ? '2px solid var(--marigold)' : '2px solid transparent',
                      }}
                    >
                      <TypeBadge type={r.type} />
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{
                          fontSize: 12.5, fontWeight: 600, color: 'var(--kajal)',
                          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                        }}>
                          {r.matched_signals?.length
                            ? `⚠ Scope warning — ${r.avatar}: ${r.matched_signals.join(', ')}`
                            : r.preview}
                        </div>
                        <div style={{
                          fontSize: 11.5, color: 'rgba(26,24,21,0.45)', marginTop: 2,
                          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                        }}>
                          {r.avatar && <span style={{ fontWeight: 500, marginRight: 6 }}>{r.avatar}</span>}
                          {r.ts && <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10 }}>{r.ts.slice(0, 16).replace('T', ' ')}</span>}
                        </div>
                      </div>
                    </div>
                  )
                })}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
