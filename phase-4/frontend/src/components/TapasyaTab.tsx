import { useCallback, useEffect, useMemo, useState } from 'react'
import { apiFetch, type EvolutionHistory, type SwapnaInboxItem } from '@/lib/api'
import { AVATAR_COLOURS, DEVA } from '@/lib/avatara-constants'
import { relativeTime } from '@/lib/format-time'

interface SutraEntry {
  id: string
  ts: string
  avatar: string
  query: string
  result: string
  kind?: string
  rule?: string
  score: number
  score_reason: string
  status: 'pending' | 'active' | 'demoted' | 'reverted'
  cooldown_remaining: string | null
  strike_count?: number
}

interface SutraPayload {
  summary?: {
    pending?: number
    active?: number
    reverted?: number
  }
  settings?: {
    promote_threshold?: number
    cooldown_hours?: number
    auto_promote_after_hours?: number
  }
  sutras?: SutraEntry[]
}

interface Props {
  userId?: string
}

function numberCard(label: string, value: string, hint: string, accent: string) {
  return (
    <div
      style={{
        padding: '14px 16px',
        borderRadius: 16,
        border: '1px solid rgba(26,24,21,0.08)',
        background: `linear-gradient(145deg, color-mix(in srgb, ${accent} 12%, var(--paper)) 0%, rgba(252,250,242,0.94) 100%)`,
      }}
    >
      <div style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.18em', color: 'rgba(26,24,21,0.42)' }}>
        {label}
      </div>
      <div style={{ marginTop: 6, fontSize: 28, fontWeight: 700, color: accent, fontFamily: 'var(--font-hero)' }}>
        {value}
      </div>
      <div style={{ marginTop: 4, fontSize: 12, lineHeight: 1.5, color: 'rgba(26,24,21,0.56)' }}>
        {hint}
      </div>
    </div>
  )
}

export function TapasyaTab({ userId = 'default' }: Props) {
  const [sutraPayload, setSutraPayload] = useState<SutraPayload | null>(null)
  const [swapnaInbox, setSwapnaInbox] = useState<SwapnaInboxItem[]>([])
  const [history, setHistory] = useState<EvolutionHistory | null>(null)
  const [loading, setLoading] = useState(false)
  const [actioning, setActioning] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [sutraResponse, swapnaResponse, evolutionResponse] = await Promise.all([
        apiFetch('/sutras'),
        apiFetch('/swapna/inbox'),
        apiFetch('/evolution/history?days=30'),
      ])

      if (sutraResponse.ok) {
        setSutraPayload(await sutraResponse.json())
      } else {
        setSutraPayload(null)
      }

      if (swapnaResponse.ok) {
        const swapnaData = await swapnaResponse.json()
        setSwapnaInbox(Array.isArray(swapnaData?.items) ? swapnaData.items : [])
      } else {
        setSwapnaInbox([])
      }

      if (evolutionResponse.ok) {
        setHistory(await evolutionResponse.json())
      } else {
        setHistory(null)
      }
    } catch {
      setSutraPayload(null)
      setSwapnaInbox([])
      setHistory(null)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load, userId])

  const runSutraAction = useCallback(async (sutraId: string, action: 'accept' | 'revert') => {
    setActioning(`${action}:${sutraId}`)
    try {
      const response = await apiFetch(`/sutras/${sutraId}/${action}`, { method: 'POST' })
      if (!response.ok) throw new Error(action)
      await load()
    } finally {
      setActioning(null)
    }
  }, [load])

  const pendingSutras = useMemo(
    () => (sutraPayload?.sutras ?? []).filter(sutra => sutra.status === 'pending'),
    [sutraPayload],
  )
  const activeSutras = useMemo(
    () => (sutraPayload?.sutras ?? []).filter(sutra => sutra.status === 'active'),
    [sutraPayload],
  )
  const demotedSutras = useMemo(
    () => (sutraPayload?.sutras ?? []).filter(sutra => sutra.status === 'demoted'),
    [sutraPayload],
  )
  const totalEvolutionPoints = useMemo(
    () => (history?.agents ?? []).reduce((sum, agent) => sum + Object.values(agent.totals).reduce((inner, value) => inner + value, 0), 0),
    [history],
  )

  return (
    <div style={{ height: '100%', minHeight: 0, overflowX: 'hidden', overflowY: 'auto', WebkitOverflowScrolling: 'touch', touchAction: 'pan-y', padding: 18 }}>
      <div
        style={{
          padding: '16px 18px',
          borderRadius: 20,
          border: '1px solid rgba(26,24,21,0.08)',
          background: 'linear-gradient(135deg, rgba(252,250,242,0.96) 0%, rgba(245,237,223,0.92) 100%)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 14, flexWrap: 'wrap' }}>
          <div>
            <div style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.22em', color: 'rgba(26,24,21,0.42)' }}>
              Tapasya
            </div>
            <div style={{ marginTop: 6, fontSize: 28, fontWeight: 700, color: 'var(--kajal)', fontFamily: 'var(--font-hero)' }}>
              Tapas, Swapna, and self-evolution in one refinement loop
            </div>
            <div style={{ marginTop: 8, fontSize: 13, lineHeight: 1.55, color: 'rgba(26,24,21,0.56)', maxWidth: 860 }}>
              This is where Narad learns deliberately: Tapas scores candidates, Swapna consolidates experience, and agent behavior evolves through reviewed changes rather than hidden drift.
            </div>
          </div>
          <button
            type="button"
            onClick={() => load()}
            disabled={loading}
            style={{
              marginLeft: 'auto',
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
            {loading ? 'Refreshing…' : 'Refresh'}
          </button>
        </div>
      </div>

      <div style={{ display: 'grid', gap: 14, marginTop: 16 }} className="md:grid-cols-2 xl:grid-cols-4">
        {numberCard(
          'Tapas Model',
          history?.config.tapas_judge_model?.replace(/^.*\//, '') ?? '—',
          'Current judge model for learning promotion',
          'var(--kajal)',
        )}
        {numberCard(
          'Promote Threshold',
          sutraPayload?.settings?.promote_threshold?.toFixed(2) ?? '—',
          'Minimum Tapas score before a Sutra enters cooldown',
          'var(--marigold)',
        )}
        {numberCard(
          'Swapna Cooldown',
          `${sutraPayload?.settings?.cooldown_hours ?? 24}h`,
          `${pendingSutras.length} pending learnings waiting for action or auto-promotion`,
          'var(--kesari)',
        )}
        {numberCard(
          'Evolution Points',
          String(totalEvolutionPoints),
          `${activeSutras.length} active Sutras across the current learning window`,
          'var(--mor)',
        )}
      </div>

      <div style={{ display: 'grid', gap: 16, marginTop: 16 }} className="xl:grid-cols-[1.25fr_0.75fr]">
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
              Pending Sutras
            </div>
            <div style={{ fontSize: 12, color: 'rgba(26,24,21,0.5)' }}>
              Approve now or let cooldown complete if Tapas keeps them eligible
            </div>
          </div>

          <div style={{ display: 'grid', gap: 12, marginTop: 16 }}>
            {pendingSutras.length === 0 && (
              <div style={{ padding: 16, borderRadius: 14, border: '1px dashed rgba(26,24,21,0.16)', color: 'rgba(26,24,21,0.45)' }}>
                No pending learning candidates right now.
              </div>
            )}
            {pendingSutras.map(sutra => {
              const busyAccept = actioning === `accept:${sutra.id}`
              const busyReject = actioning === `revert:${sutra.id}`
              const accent = AVATAR_COLOURS[sutra.avatar as keyof typeof AVATAR_COLOURS] ?? 'var(--kajal)'
              return (
                <div
                  key={sutra.id}
                  style={{
                    padding: '14px 16px',
                    borderRadius: 16,
                    border: '1px solid rgba(26,24,21,0.08)',
                    background: 'rgba(26,24,21,0.03)',
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                    <span
                      style={{
                        padding: '3px 8px',
                        borderRadius: 999,
                        background: `${accent}20`,
                        color: accent,
                        fontSize: 10.5,
                        fontWeight: 700,
                      }}
                    >
                      {DEVA[sutra.avatar as keyof typeof DEVA] ?? '•'} {sutra.avatar}
                    </span>
                    <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--kajal)' }}>
                      score {sutra.score.toFixed(2)}
                    </span>
                    <span style={{ marginLeft: 'auto', fontSize: 10.5, color: 'rgba(26,24,21,0.42)', fontFamily: 'var(--font-mono)' }}>
                      {sutra.cooldown_remaining ? `⏳ ${sutra.cooldown_remaining}` : 'Pending review'}
                    </span>
                  </div>

                  <div style={{ marginTop: 10, fontSize: 13, lineHeight: 1.6, color: 'var(--kajal)' }}>
                    {sutra.rule ?? sutra.query}
                  </div>
                  <div style={{ marginTop: 6, fontSize: 12, lineHeight: 1.55, color: 'rgba(26,24,21,0.55)' }}>
                    {sutra.rule ? `From: ${sutra.query.slice(0, 160)}` : sutra.score_reason}
                  </div>

                  <div style={{ display: 'flex', gap: 8, marginTop: 12, flexWrap: 'wrap' }}>
                    <button
                      type="button"
                      disabled={Boolean(actioning)}
                      onClick={() => runSutraAction(sutra.id, 'accept')}
                      style={{
                        padding: '8px 12px',
                        borderRadius: 10,
                        border: 'none',
                        background: 'var(--kajal)',
                        color: 'var(--paper)',
                        fontSize: 11.5,
                        fontWeight: 700,
                        cursor: 'pointer',
                      }}
                    >
                      {busyAccept ? 'Approving…' : 'Approve'}
                    </button>
                    <button
                      type="button"
                      disabled={Boolean(actioning)}
                      onClick={() => runSutraAction(sutra.id, 'revert')}
                      style={{
                        padding: '8px 12px',
                        borderRadius: 10,
                        border: '1px solid rgba(194,65,12,0.25)',
                        background: 'rgba(194,65,12,0.06)',
                        color: 'var(--sindoor)',
                        fontSize: 11.5,
                        fontWeight: 700,
                        cursor: 'pointer',
                      }}
                    >
                      {busyReject ? 'Rejecting…' : 'Reject'}
                    </button>
                  </div>
                </div>
              )
            })}
          </div>

          {demotedSutras.length > 0 && (
            <>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap', marginTop: 22 }}>
                <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--sindoor)', fontFamily: 'var(--font-hero)' }}>
                  Demoted Sutras
                </div>
                <div style={{ fontSize: 12, color: 'rgba(26,24,21,0.5)' }}>
                  Pulled from injection after steering sessions into failures
                </div>
              </div>
              <div style={{ display: 'grid', gap: 12, marginTop: 12 }}>
                {demotedSutras.map(sutra => {
                  const busyAccept = actioning === `accept:${sutra.id}`
                  const accent = AVATAR_COLOURS[sutra.avatar as keyof typeof AVATAR_COLOURS] ?? 'var(--kajal)'
                  return (
                    <div
                      key={sutra.id}
                      style={{
                        padding: '14px 16px',
                        borderRadius: 16,
                        border: '1px solid rgba(194,65,12,0.18)',
                        background: 'rgba(194,65,12,0.04)',
                      }}
                    >
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                        <span style={{ padding: '3px 8px', borderRadius: 999, background: `${accent}20`, color: accent, fontSize: 10.5, fontWeight: 700 }}>
                          {DEVA[sutra.avatar as keyof typeof DEVA] ?? '•'} {sutra.avatar}
                        </span>
                        <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--sindoor)' }}>
                          {sutra.strike_count ?? 0} strikes
                        </span>
                      </div>
                      <div style={{ marginTop: 10, fontSize: 13, lineHeight: 1.6, color: 'var(--kajal)' }}>
                        {sutra.rule ?? sutra.query}
                      </div>
                      <div style={{ display: 'flex', gap: 8, marginTop: 12, flexWrap: 'wrap' }}>
                        <button
                          type="button"
                          disabled={Boolean(actioning)}
                          onClick={() => runSutraAction(sutra.id, 'accept')}
                          style={{
                            padding: '8px 12px',
                            borderRadius: 10,
                            border: 'none',
                            background: 'var(--kajal)',
                            color: 'var(--paper)',
                            fontSize: 11.5,
                            fontWeight: 700,
                            cursor: 'pointer',
                          }}
                        >
                          {busyAccept ? 'Reactivating…' : 'Reactivate'}
                        </button>
                        <button
                          type="button"
                          disabled={Boolean(actioning)}
                          onClick={() => runSutraAction(sutra.id, 'revert')}
                          style={{
                            padding: '8px 12px',
                            borderRadius: 10,
                            border: '1px solid rgba(194,65,12,0.25)',
                            background: 'rgba(194,65,12,0.06)',
                            color: 'var(--sindoor)',
                            fontSize: 11.5,
                            fontWeight: 700,
                            cursor: 'pointer',
                          }}
                        >
                          {actioning === `revert:${sutra.id}` ? 'Removing…' : 'Remove forever'}
                        </button>
                      </div>
                    </div>
                  )
                })}
              </div>
            </>
          )}
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
            Swapna Inbox
          </div>
          <div style={{ marginTop: 6, fontSize: 12, lineHeight: 1.5, color: 'rgba(26,24,21,0.55)' }}>
            Dream-cycle proposals stay visible here before they become durable knowledge.
          </div>

          <div style={{ display: 'grid', gap: 10, marginTop: 16 }}>
            {swapnaInbox.length === 0 && (
              <div style={{ padding: 16, borderRadius: 14, border: '1px dashed rgba(26,24,21,0.16)', color: 'rgba(26,24,21,0.45)' }}>
                No pending Swapna consolidations right now.
              </div>
            )}
            {swapnaInbox.map(item => (
              <div
                key={item.id}
                style={{
                  padding: '12px 14px',
                  borderRadius: 14,
                  background: 'rgba(26,24,21,0.03)',
                  border: '1px solid rgba(26,24,21,0.06)',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                  <span style={{ fontSize: 11.5, fontWeight: 700, color: 'var(--kajal)' }}>
                    {item.project_id}
                  </span>
                  <span style={{ marginLeft: 'auto', fontSize: 10.5, color: 'rgba(26,24,21,0.42)', fontFamily: 'var(--font-mono)' }}>
                    {relativeTime(item.ts)}
                  </span>
                </div>
                <div style={{ marginTop: 8, display: 'flex', flexWrap: 'wrap', gap: 8, fontSize: 11.5, color: 'rgba(26,24,21,0.56)' }}>
                  <span>{item.source_episode_ids.length} source episodes</span>
                  <span>{item.suggestions.facts.length} facts</span>
                  <span>{item.suggestions.scenarios.length} scenarios</span>
                </div>
                {item.suggestions.candidate_keywords.length > 0 && (
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 10 }}>
                    {item.suggestions.candidate_keywords.slice(0, 6).map(keyword => (
                      <span
                        key={keyword}
                        style={{
                          padding: '4px 8px',
                          borderRadius: 999,
                          background: 'rgba(26,24,21,0.05)',
                          border: '1px solid rgba(26,24,21,0.08)',
                          fontSize: 10.5,
                          color: 'rgba(26,24,21,0.58)',
                        }}
                      >
                        {keyword}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        </section>
      </div>

      <div style={{ display: 'grid', gap: 16, marginTop: 16 }} className="xl:grid-cols-[0.95fr_1.05fr]">
        <section
          style={{
            padding: 18,
            borderRadius: 18,
            border: '1px solid rgba(26,24,21,0.08)',
            background: 'rgba(252,250,242,0.9)',
          }}
        >
          <div style={{ fontSize: 18, fontWeight: 700, color: 'var(--kajal)', fontFamily: 'var(--font-hero)' }}>
            Agent Evolution
          </div>
          <div style={{ marginTop: 6, fontSize: 12, lineHeight: 1.5, color: 'rgba(26,24,21,0.55)' }}>
            Rolling change totals across tools, skills, behavior, memory, and runtime.
          </div>

          <div style={{ display: 'grid', gap: 10, marginTop: 16 }}>
            {(history?.agents ?? []).length === 0 && (
              <div style={{ padding: 16, borderRadius: 14, border: '1px dashed rgba(26,24,21,0.16)', color: 'rgba(26,24,21,0.45)' }}>
                Evolution history will appear here once Narad records enough runs.
              </div>
            )}
            {(history?.agents ?? []).map(agent => {
              const accent = AVATAR_COLOURS[agent.name as keyof typeof AVATAR_COLOURS] ?? 'var(--kajal)'
              const total = Object.values(agent.totals).reduce((sum, value) => sum + value, 0)
              return (
                <div
                  key={agent.name}
                  style={{
                    padding: '12px 14px',
                    borderRadius: 14,
                    border: '1px solid rgba(26,24,21,0.06)',
                    background: 'rgba(26,24,21,0.03)',
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span style={{ fontSize: 12.5, fontWeight: 700, color: accent }}>
                      {DEVA[agent.name as keyof typeof DEVA]} {agent.name}
                    </span>
                    <span style={{ fontSize: 11.5, color: 'rgba(26,24,21,0.46)' }}>
                      {agent.discipline}
                    </span>
                    <span style={{ marginLeft: 'auto', fontSize: 11, color: 'rgba(26,24,21,0.46)', fontFamily: 'var(--font-mono)' }}>
                      {total} change points
                    </span>
                  </div>
                  <div style={{ display: 'grid', gap: 8, marginTop: 10 }}>
                    {Object.entries(agent.totals).map(([key, value]) => (
                      <div key={key} style={{ display: 'grid', gridTemplateColumns: '84px 1fr auto', gap: 10, alignItems: 'center' }}>
                        <div style={{ fontSize: 11, color: 'rgba(26,24,21,0.5)', textTransform: 'capitalize' }}>{key}</div>
                        <div style={{ height: 8, borderRadius: 999, background: 'rgba(26,24,21,0.06)', overflow: 'hidden' }}>
                          <div
                            style={{
                              height: '100%',
                              width: `${total > 0 ? Math.max((value / total) * 100, value > 0 ? 8 : 0) : 0}%`,
                              borderRadius: 999,
                              background: accent,
                            }}
                          />
                        </div>
                        <div style={{ fontSize: 11, color: 'rgba(26,24,21,0.48)', fontFamily: 'var(--font-mono)' }}>
                          {value}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )
            })}
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
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' }}>
            <div style={{ fontSize: 18, fontWeight: 700, color: 'var(--kajal)', fontFamily: 'var(--font-hero)' }}>
              Recent Tapasya Changes
            </div>
            {history && (
              <div style={{ fontSize: 12, color: 'rgba(26,24,21,0.5)' }}>
                Updated {relativeTime(history.generated_at)}
              </div>
            )}
          </div>

          <div style={{ display: 'grid', gap: 10, marginTop: 16 }}>
            {(history?.recent_changes ?? []).length === 0 && (
              <div style={{ padding: 16, borderRadius: 14, border: '1px dashed rgba(26,24,21,0.16)', color: 'rgba(26,24,21,0.45)' }}>
                No recent refinement changes recorded yet.
              </div>
            )}
            {(history?.recent_changes ?? []).slice(0, 10).map(change => {
              const accent = AVATAR_COLOURS[change.agent as keyof typeof AVATAR_COLOURS] ?? 'var(--kajal)'
              return (
                <div
                  key={`${change.ts}-${change.agent}-${change.title}`}
                  style={{
                    padding: '12px 14px',
                    borderRadius: 14,
                    background: 'rgba(26,24,21,0.03)',
                    border: '1px solid rgba(26,24,21,0.06)',
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                    <span
                      style={{
                        padding: '3px 8px',
                        borderRadius: 999,
                        background: `${accent}18`,
                        color: accent,
                        fontSize: 10.5,
                        fontWeight: 700,
                      }}
                    >
                      {change.agent}
                    </span>
                    <span style={{ fontSize: 11, color: 'rgba(26,24,21,0.46)', textTransform: 'capitalize' }}>
                      {change.category}
                    </span>
                    <span style={{ marginLeft: 'auto', fontSize: 10.5, color: 'rgba(26,24,21,0.42)', fontFamily: 'var(--font-mono)' }}>
                      {relativeTime(change.ts)}
                    </span>
                  </div>
                  <div style={{ marginTop: 8, fontSize: 13, fontWeight: 700, color: 'var(--kajal)' }}>
                    {change.title}
                  </div>
                  <div style={{ marginTop: 4, fontSize: 12, lineHeight: 1.55, color: 'rgba(26,24,21,0.56)' }}>
                    {change.detail}
                  </div>
                </div>
              )
            })}
          </div>
        </section>
      </div>
    </div>
  )
}
