import { useCallback, useEffect, useMemo, useState } from 'react'
import { apiFetch, apiUrl } from '@/lib/api'

interface MemoryEntry {
  id: string
  source?: string
  entity?: string
  content?: string
  text?: string
  avatar?: string
  project_id?: string
  session_id?: string
  created_at?: string
  updated_at?: string
  score?: number
  tag?: string
  type?: string
}

interface CommitmentEntry {
  id: string
  kind: string
  content: string
  avatar?: string
  ts?: string
}

interface SutraEntry {
  id: string
  avatar: string
  query: string
  score: number
  status: 'pending' | 'active' | 'reverted'
  cooldown_remaining?: string | null
}

const FILTER_TAGS = [
  { key: 'all', label: 'All' },
  { key: 'finance', label: 'Finance' },
  { key: 'code', label: 'Code' },
  { key: 'planning', label: 'Planning' },
  { key: 'learner_profile', label: 'Learner' },
  { key: 'insight', label: 'Insights' },
]

interface Props {
  userId: string
}

export function MemoryTab({ userId }: Props) {
  const [filterTag, setFilterTag] = useState('all')
  const [localSearch, setLocalSearch] = useState('')
  const [memories, setMemories] = useState<MemoryEntry[]>([])
  const [commitments, setCommitments] = useState<CommitmentEntry[]>([])
  const [sutras, setSutras] = useState<SutraEntry[]>([])
  const [loading, setLoading] = useState(false)
  const [selected, setSelected] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams({ user_id: userId, limit: '80' })
      if (filterTag !== 'all') params.set('tag', filterTag)
      const [memoryResponse, sankalpaResponse, sutraResponse] = await Promise.all([
        apiFetch(`/memory?${params}`),
        apiFetch(apiUrl('/sankalpa', { user_id: userId })),
        apiFetch('/sutras'),
      ])

      if (memoryResponse.ok) {
        const data = await memoryResponse.json()
        const nextMemories = (Array.isArray(data) ? data : data.memories ?? []).map((memory: MemoryEntry) => ({
          ...memory,
          content: memory.content ?? memory.text ?? '',
          entity: memory.entity ?? memory.type ?? memory.tag ?? memory.source ?? 'general',
          tag: memory.tag ?? memory.type ?? '',
        }))
        setMemories(nextMemories)
      }
      if (sankalpaResponse.ok) {
        const data = await sankalpaResponse.json()
        setCommitments(Array.isArray(data.commitments) ? data.commitments : [])
      }
      if (sutraResponse.ok) {
        const data = await sutraResponse.json()
        setSutras(Array.isArray(data.sutras) ? data.sutras : [])
      }
    } catch {
      setMemories([])
      setCommitments([])
      setSutras([])
    } finally {
      setLoading(false)
    }
  }, [filterTag, userId])

  useEffect(() => {
    load()
  }, [load])

  const filteredMemories = useMemo(() => {
    const q = localSearch.trim().toLowerCase()
    return memories.filter(memory => {
      const matchesTag =
        filterTag === 'all' ||
        (memory.entity ?? '').toLowerCase().includes(filterTag) ||
        (memory.tag ?? '').toLowerCase().includes(filterTag)
      const haystack = `${memory.content ?? ''} ${memory.entity ?? ''} ${memory.source ?? ''}`.toLowerCase()
      const matchesSearch = !q || haystack.includes(q)
      return matchesTag && matchesSearch
    })
  }, [filterTag, localSearch, memories])

  const activeSutras = useMemo(
    () => sutras.filter(sutra => sutra.status === 'active').slice(0, 8),
    [sutras],
  )

  return (
    <div style={{ height: '100%', minHeight: 0, overflowX: 'hidden', overflowY: 'auto', WebkitOverflowScrolling: 'touch', touchAction: 'pan-y', padding: 18 }}>
      <div
        style={{
          padding: '16px 18px',
          borderRadius: 20,
          border: '1px solid rgba(26,24,21,0.08)',
          background: 'linear-gradient(135deg, rgba(252,250,242,0.95) 0%, rgba(243,239,225,0.9) 100%)',
        }}
      >
        <div style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.22em', color: 'rgba(26,24,21,0.42)' }}>
          Smriti
        </div>
        <div style={{ marginTop: 6, fontSize: 28, fontWeight: 700, color: 'var(--kajal)', fontFamily: 'var(--font-hero)' }}>
          Memory, provenance, commitments, and learned context
        </div>
        <div style={{ marginTop: 8, fontSize: 13, lineHeight: 1.55, color: 'rgba(26,24,21,0.56)' }}>
          Smriti keeps together retained recall, approved learnings, and the commitments Narad should carry forward from past work.
        </div>

        <div style={{ display: 'grid', gap: 10, marginTop: 14 }} className="md:grid-cols-3">
          {[
            { label: 'Retained memories', value: filteredMemories.length, hint: 'Searchable recall entries' },
            { label: 'Commitments', value: commitments.length, hint: 'Active Sankalpa constraints' },
            { label: 'Active Sutras', value: activeSutras.length, hint: 'Approved learnings in context' },
          ].map(item => (
            <div
              key={item.label}
              style={{
                padding: '12px 14px',
                borderRadius: 14,
                background: 'rgba(252,250,242,0.8)',
                border: '1px solid rgba(26,24,21,0.08)',
              }}
            >
              <div style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.16em', color: 'rgba(26,24,21,0.42)' }}>
                {item.label}
              </div>
              <div style={{ marginTop: 6, fontSize: 22, fontWeight: 700, color: 'var(--kajal)' }}>
                {item.value}
              </div>
              <div style={{ marginTop: 4, fontSize: 12, color: 'rgba(26,24,21,0.52)' }}>
                {item.hint}
              </div>
            </div>
          ))}
        </div>

        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginTop: 14 }}>
          <input
            value={localSearch}
            onChange={event => setLocalSearch(event.target.value)}
            placeholder="Search memory and provenance…"
            style={{
              flex: '1 1 280px',
              minWidth: 240,
              background: 'rgba(26,24,21,0.05)',
              border: '1px solid rgba(26,24,21,0.12)',
              borderRadius: 10,
              padding: '10px 12px',
              fontSize: 12.5,
              outline: 'none',
              color: 'var(--kajal)',
            }}
          />
          <button
            type="button"
            onClick={() => load()}
            style={{
              padding: '10px 12px',
              borderRadius: 10,
              border: '1px solid rgba(26,24,21,0.12)',
              background: 'rgba(252,250,242,0.85)',
              color: 'var(--kajal)',
              cursor: 'pointer',
              fontSize: 12,
              fontWeight: 600,
            }}
          >
            Refresh
          </button>
        </div>

        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 12 }}>
          {FILTER_TAGS.map(tag => (
            <button
              key={tag.key}
              type="button"
              onClick={() => setFilterTag(tag.key)}
              style={{
                padding: '6px 10px',
                borderRadius: 999,
                border: `1px solid ${filterTag === tag.key ? 'rgba(242,142,28,0.4)' : 'rgba(26,24,21,0.12)'}`,
                background: filterTag === tag.key ? 'rgba(242,142,28,0.12)' : 'rgba(252,250,242,0.7)',
                color: filterTag === tag.key ? 'var(--marigold)' : 'rgba(26,24,21,0.52)',
                fontSize: 11.5,
                fontWeight: 600,
                cursor: 'pointer',
              }}
            >
              {tag.label}
            </button>
          ))}
        </div>
      </div>

      <div style={{ display: 'grid', gap: 16, marginTop: 16 }} className="xl:grid-cols-[0.9fr_1.35fr]">
        <div style={{ display: 'grid', gap: 16 }}>
          <section
            style={{
              padding: 18,
              borderRadius: 18,
              border: '1px solid rgba(26,24,21,0.08)',
              background: 'rgba(252,250,242,0.9)',
            }}
          >
            <div style={{ fontSize: 18, fontWeight: 700, color: 'var(--kajal)', fontFamily: 'var(--font-hero)' }}>
              Sankalpa commitments
            </div>
            <div style={{ marginTop: 6, fontSize: 12, lineHeight: 1.5, color: 'rgba(26,24,21,0.55)' }}>
              Durable goals, preferences, and constraints extracted from recent work.
            </div>

            <div style={{ display: 'grid', gap: 10, marginTop: 14 }}>
              {loading && <div style={{ color: 'rgba(26,24,21,0.45)', fontSize: 12 }}>Loading commitments…</div>}
              {!loading && commitments.length === 0 && (
                <div style={{ color: 'rgba(26,24,21,0.45)', fontSize: 12 }}>
                  No commitments recorded yet.
                </div>
              )}
              {commitments.slice(-8).reverse().map(commitment => (
                <div
                  key={commitment.id}
                  style={{
                    padding: '10px 12px',
                    borderRadius: 14,
                    background: 'rgba(26,24,21,0.03)',
                    border: '1px solid rgba(26,24,21,0.06)',
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                    <span
                      style={{
                        padding: '2px 7px',
                        borderRadius: 999,
                        background: 'rgba(242,142,28,0.12)',
                        color: 'var(--marigold)',
                        fontSize: 10,
                        fontWeight: 700,
                        textTransform: 'uppercase',
                      }}
                    >
                      {commitment.kind}
                    </span>
                    {commitment.avatar && (
                      <span style={{ fontSize: 10.5, color: 'rgba(26,24,21,0.46)' }}>{commitment.avatar}</span>
                    )}
                    {commitment.ts && (
                      <span style={{ marginLeft: 'auto', fontSize: 10, color: 'rgba(26,24,21,0.38)', fontFamily: 'var(--font-mono)' }}>
                        {commitment.ts.slice(0, 10)}
                      </span>
                    )}
                  </div>
                  <div style={{ fontSize: 12.5, lineHeight: 1.55, color: 'var(--kajal)' }}>
                    {commitment.content}
                  </div>
                </div>
              ))}
            </div>
          </section>

          <section
            style={{
              padding: 18,
              borderRadius: 18,
              border: '1px solid rgba(26,24,21,0.08)',
              background: 'rgba(252,250,242,0.9)',
            }}
          >
            <div style={{ fontSize: 18, fontWeight: 700, color: 'var(--kajal)', fontFamily: 'var(--font-hero)' }}>
              Active Sutras
            </div>
            <div style={{ marginTop: 6, fontSize: 12, lineHeight: 1.5, color: 'rgba(26,24,21,0.55)' }}>
              Approved learnings that are now part of Narad&apos;s retained operating context.
            </div>

            <div style={{ display: 'grid', gap: 10, marginTop: 14 }}>
              {!loading && activeSutras.length === 0 && (
                <div style={{ color: 'rgba(26,24,21,0.45)', fontSize: 12 }}>
                  No active Sutras yet.
                </div>
              )}
              {activeSutras.map(sutra => (
                <div
                  key={sutra.id}
                  style={{
                    padding: '10px 12px',
                    borderRadius: 14,
                    background: 'rgba(26,24,21,0.03)',
                    border: '1px solid rgba(26,24,21,0.06)',
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                    <span
                      style={{
                        padding: '2px 7px',
                        borderRadius: 999,
                        background: 'rgba(6,95,70,0.1)',
                        color: 'var(--tulsi)',
                        fontSize: 10,
                        fontWeight: 700,
                        textTransform: 'uppercase',
                      }}
                    >
                      {sutra.avatar}
                    </span>
                    <span style={{ fontSize: 10.5, color: 'rgba(26,24,21,0.46)' }}>
                      score {sutra.score.toFixed(2)}
                    </span>
                  </div>
                  <div style={{ fontSize: 12.5, lineHeight: 1.55, color: 'var(--kajal)' }}>
                    {sutra.query}
                  </div>
                </div>
              ))}
            </div>
          </section>
        </div>

        <section
          style={{
            padding: 18,
            borderRadius: 18,
            border: '1px solid rgba(26,24,21,0.08)',
            background: 'rgba(252,250,242,0.9)',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' }}>
            <div style={{ fontSize: 18, fontWeight: 700, color: 'var(--kajal)', fontFamily: 'var(--font-hero)' }}>
              Retained memories
            </div>
            <div style={{ fontSize: 12, color: 'rgba(26,24,21,0.5)' }}>
              {filteredMemories.length} matching entries
            </div>
          </div>

          <div style={{ display: 'grid', gap: 10, marginTop: 14 }}>
            {loading && <div style={{ color: 'rgba(26,24,21,0.45)', fontSize: 12 }}>Loading memories…</div>}
            {!loading && filteredMemories.length === 0 && (
              <div style={{ color: 'rgba(26,24,21,0.45)', fontSize: 12 }}>
                No memories match this filter.
              </div>
            )}
            {filteredMemories.map(memory => {
              const isSelected = selected === memory.id
              const label = memory.entity ?? memory.source ?? memory.tag ?? memory.type ?? 'general'
              const timestamp = memory.updated_at ?? memory.created_at ?? ''
              return (
                <button
                  key={memory.id}
                  type="button"
                  onClick={() => setSelected(isSelected ? null : memory.id)}
                  style={{
                    textAlign: 'left',
                    padding: '12px 14px',
                    borderRadius: 14,
                    border: `1px solid ${isSelected ? 'rgba(242,142,28,0.35)' : 'rgba(26,24,21,0.08)'}`,
                    background: isSelected ? 'rgba(242,142,28,0.08)' : 'rgba(26,24,21,0.03)',
                    cursor: 'pointer',
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                    <span
                      style={{
                        padding: '2px 7px',
                        borderRadius: 999,
                        background: 'rgba(242,142,28,0.12)',
                        color: 'var(--marigold)',
                        fontSize: 10,
                        fontWeight: 700,
                        textTransform: 'uppercase',
                      }}
                    >
                      {label}
                    </span>
                    {memory.avatar && (
                      <span style={{ fontSize: 10.5, color: 'rgba(26,24,21,0.46)' }}>{memory.avatar}</span>
                    )}
                    {timestamp && (
                      <span style={{ marginLeft: 'auto', fontSize: 10, color: 'rgba(26,24,21,0.38)', fontFamily: 'var(--font-mono)' }}>
                        {timestamp.slice(0, 10)}
                      </span>
                    )}
                  </div>

                  <div style={{ fontSize: 12.5, lineHeight: 1.6, color: 'var(--kajal)' }}>
                    {isSelected
                      ? memory.content ?? ''
                      : `${(memory.content ?? '').slice(0, 180)}${(memory.content?.length ?? 0) > 180 ? '…' : ''}`}
                  </div>

                  {isSelected && (
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, marginTop: 8, fontSize: 10.5, color: 'rgba(26,24,21,0.46)' }}>
                      {memory.project_id && <span>project: {memory.project_id}</span>}
                      {memory.session_id && <span>session: {memory.session_id.slice(0, 16)}…</span>}
                      {typeof memory.score === 'number' && <span>score: {memory.score.toFixed(2)}</span>}
                    </div>
                  )}
                </button>
              )
            })}
          </div>
        </section>
      </div>
    </div>
  )
}
