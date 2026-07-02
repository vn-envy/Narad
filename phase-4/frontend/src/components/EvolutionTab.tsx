import { useEffect, useMemo, useState } from 'react'
import { apiFetch, type EvolutionHistory } from '@/lib/api'
import { AVATAR_COLOURS, AVATAR_NAMES, DEVA } from '@/lib/avatara-constants'
import { relativeTime } from '@/lib/format-time'

type EvolutionCategory = 'tools' | 'skills' | 'behavior' | 'memory' | 'runtime'

const CATEGORY_LABELS: Record<EvolutionCategory, string> = {
  tools: 'Tools',
  skills: 'Skills',
  behavior: 'Behavior',
  memory: 'Memory',
  runtime: 'Runtime',
}

function StatCard({ label, value, hint, accent }: { label: string; value: string; hint: string; accent: string }) {
  return (
    <div
      style={{
        padding: '14px 16px',
        borderRadius: 16,
        border: '1px solid rgba(26,24,21,0.08)',
        background: `linear-gradient(135deg, color-mix(in srgb, ${accent} 12%, var(--paper)) 0%, rgba(252,250,242,0.92) 100%)`,
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

export function EvolutionTab() {
  const [history, setHistory] = useState<EvolutionHistory | null>(null)
  const [selectedAgent, setSelectedAgent] = useState<string>('Matsya')
  const [selectedCategory, setSelectedCategory] = useState<EvolutionCategory>('skills')

  useEffect(() => {
    let cancelled = false
    apiFetch('/evolution/history?days=30')
      .then(res => (res.ok ? res.json() : null))
      .then((data: EvolutionHistory | null) => {
        if (!cancelled) setHistory(data)
      })
      .catch(() => {
        if (!cancelled) setHistory(null)
      })
    return () => {
      cancelled = true
    }
  }, [])

  const selected = useMemo(() => {
    const agent = history?.agents.find(item => item.name === selectedAgent)
    return agent ?? history?.agents[0] ?? null
  }, [history, selectedAgent])

  const series = useMemo(() => {
    if (!history || !selected) return []
    return history.timeline.map(entry => {
      const agentPoint = entry.agents.find(item => item.agent === selected.name)
      return {
        date: entry.date,
        daily: agentPoint?.daily?.[selectedCategory] ?? 0,
        cumulative: agentPoint?.cumulative?.[selectedCategory] ?? 0,
      }
    })
  }, [history, selected, selectedCategory])

  const maxDaily = Math.max(...series.map(item => item.daily), 0)
  const accent = selected ? AVATAR_COLOURS[selected.name as keyof typeof AVATAR_COLOURS] : 'var(--marigold)'

  return (
    <div style={{ height: '100%', minHeight: 0, overflow: 'auto', padding: 18 }}>
      <div
        style={{
          padding: '16px 18px',
          borderRadius: 20,
          border: '1px solid rgba(26,24,21,0.08)',
          background: 'linear-gradient(135deg, rgba(252,250,242,0.95) 0%, rgba(243,239,225,0.9) 100%)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 14, flexWrap: 'wrap' }}>
          <div>
            <div style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.22em', color: 'rgba(26,24,21,0.42)' }}>
              Agent Evolution
            </div>
            <div style={{ marginTop: 6, fontSize: 28, fontWeight: 700, color: 'var(--kajal)', fontFamily: 'var(--font-hero)' }}>
              Progress across Smriti, Tapas, Karma, and runtime behavior
            </div>
          </div>
          {history && (
            <div style={{ marginLeft: 'auto', fontSize: 12, color: 'rgba(26,24,21,0.5)' }}>
              Updated {relativeTime(history.generated_at)}
            </div>
          )}
        </div>

        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginTop: 16 }}>
          {AVATAR_NAMES.map(name => (
            <button
              key={name}
              type="button"
              onClick={() => setSelectedAgent(name)}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                padding: '8px 12px',
                borderRadius: 999,
                border: `1px solid ${selectedAgent === name ? `${AVATAR_COLOURS[name]}66` : 'rgba(26,24,21,0.12)'}`,
                background: selectedAgent === name ? `${AVATAR_COLOURS[name]}18` : 'rgba(252,250,242,0.7)',
                color: selectedAgent === name ? AVATAR_COLOURS[name] : 'rgba(26,24,21,0.58)',
                cursor: 'pointer',
              }}
            >
              <span style={{ fontFamily: 'var(--font-deva)', fontSize: 13 }}>{DEVA[name]}</span>
              <span style={{ fontSize: 12.5, fontWeight: 600 }}>{name}</span>
            </button>
          ))}
        </div>

        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 14 }}>
          {(history?.categories ?? []).map(category => (
            <button
              key={category}
              type="button"
              onClick={() => setSelectedCategory(category as EvolutionCategory)}
              style={{
                padding: '6px 10px',
                borderRadius: 999,
                border: `1px solid ${selectedCategory === category ? `${accent}66` : 'rgba(26,24,21,0.12)'}`,
                background: selectedCategory === category ? `${accent}16` : 'rgba(26,24,21,0.04)',
                color: selectedCategory === category ? accent : 'rgba(26,24,21,0.52)',
                fontSize: 11.5,
                fontWeight: 600,
                cursor: 'pointer',
              }}
            >
              {CATEGORY_LABELS[category as EvolutionCategory] ?? category}
            </button>
          ))}
        </div>
      </div>

      {!selected && (
        <div style={{ padding: 24, color: 'rgba(26,24,21,0.45)' }}>
          Evolution history will appear here once Narad records agent runs and learning events.
        </div>
      )}

      {selected && history && (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: 14, marginTop: 16 }}>
            <StatCard
              label="Selected Category"
              value={String(selected.totals[selectedCategory] ?? 0)}
              hint={`${CATEGORY_LABELS[selectedCategory]} change points in the last ${history.window_days} days`}
              accent={accent}
            />
            <StatCard
              label="Tool Surface"
              value={String(selected.totals.tools)}
              hint={`${selected.tool_usage.length} distinct tools observed`}
              accent="var(--tulsi)"
            />
            <StatCard
              label="Memory Growth"
              value={String(selected.memory.episodes)}
              hint={`${selected.memory.commitments} commitments, ${selected.memory.reflections} reflections`}
              accent="var(--mor)"
            />
            <StatCard
              label="Runtime Footprint"
              value={String(selected.behavior.sessions)}
              hint={`${selected.behavior.avg_latency_ms ? `${(selected.behavior.avg_latency_ms / 1000).toFixed(1)}s avg latency` : 'No recent latency yet'}`}
              accent="var(--kesari)"
            />
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1.3fr 0.9fr', gap: 16, marginTop: 16 }}>
            <div
              style={{
                padding: 18,
                borderRadius: 18,
                border: '1px solid rgba(26,24,21,0.08)',
                background: 'rgba(252,250,242,0.9)',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
                <div style={{ fontSize: 18, fontWeight: 700, color: 'var(--kajal)', fontFamily: 'var(--font-hero)' }}>
                  {CATEGORY_LABELS[selectedCategory]} progression
                </div>
                <div style={{ fontSize: 12, color: 'rgba(26,24,21,0.48)' }}>
                  daily activity with cumulative growth
                </div>
              </div>

              <div style={{ display: 'grid', gap: 10, marginTop: 16 }}>
                {series.length === 0 && (
                  <div style={{ padding: 18, borderRadius: 14, border: '1px dashed rgba(26,24,21,0.16)', color: 'rgba(26,24,21,0.45)' }}>
                    No persisted evolution data yet for this window.
                  </div>
                )}
                {series.map(point => (
                  <div key={point.date} style={{ display: 'grid', gridTemplateColumns: '84px 1fr 74px', gap: 12, alignItems: 'center' }}>
                    <div style={{ fontSize: 11, color: 'rgba(26,24,21,0.5)', fontFamily: 'var(--font-mono)' }}>
                      {point.date.slice(5)}
                    </div>
                    <div style={{ height: 12, borderRadius: 999, background: 'rgba(26,24,21,0.06)', overflow: 'hidden' }}>
                      <div
                        style={{
                          height: '100%',
                          width: `${maxDaily > 0 ? Math.max((point.daily / maxDaily) * 100, point.daily > 0 ? 8 : 0) : 0}%`,
                          borderRadius: 999,
                          background: accent,
                        }}
                      />
                    </div>
                    <div style={{ textAlign: 'right', fontSize: 11, color: 'rgba(26,24,21,0.52)', fontFamily: 'var(--font-mono)' }}>
                      +{point.daily} · {point.cumulative}
                    </div>
                  </div>
                ))}
              </div>
            </div>

            <div style={{ display: 'grid', gap: 16 }}>
              <div
                style={{
                  padding: 18,
                  borderRadius: 18,
                  border: '1px solid rgba(26,24,21,0.08)',
                  background: 'rgba(252,250,242,0.9)',
                }}
              >
                <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--kajal)', fontFamily: 'var(--font-hero)' }}>
                  Active runtime
                </div>
                <div style={{ marginTop: 12, display: 'grid', gap: 8 }}>
                  <div style={{ fontSize: 12, color: 'rgba(26,24,21,0.56)' }}>
                    Discipline: <strong style={{ color: 'var(--kajal)' }}>{selected.discipline}</strong>
                  </div>
                  <div style={{ fontSize: 12, color: 'rgba(26,24,21,0.56)' }}>
                    Tokens observed: <strong style={{ color: 'var(--kajal)' }}>{selected.behavior.total_tokens.toLocaleString()}</strong>
                  </div>
                  <div style={{ fontSize: 12, color: 'rgba(26,24,21,0.56)' }}>
                    Degraded events: <strong style={{ color: 'var(--kajal)' }}>{selected.behavior.degraded_events}</strong>
                  </div>
                  <div style={{ marginTop: 8, display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                    {selected.runtime.models_seen.length === 0 && (
                      <span style={{ fontSize: 11, color: 'rgba(26,24,21,0.45)' }}>No model traces yet.</span>
                    )}
                    {selected.runtime.models_seen.map(model => (
                      <span
                        key={model}
                        style={{
                          padding: '4px 8px',
                          borderRadius: 999,
                          background: 'rgba(26,24,21,0.05)',
                          border: '1px solid rgba(26,24,21,0.08)',
                          fontSize: 10.5,
                          color: 'rgba(26,24,21,0.64)',
                        }}
                      >
                        {model}
                      </span>
                    ))}
                  </div>
                </div>
              </div>

              <div
                style={{
                  padding: 18,
                  borderRadius: 18,
                  border: '1px solid rgba(26,24,21,0.08)',
                  background: 'rgba(252,250,242,0.9)',
                }}
              >
                <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--kajal)', fontFamily: 'var(--font-hero)' }}>
                  Tool and learning surface
                </div>
                <div style={{ display: 'grid', gap: 10, marginTop: 12 }}>
                  {selected.tool_usage.slice(0, 4).map(tool => (
                    <div key={tool.tool} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                      <div style={{ minWidth: 92, fontSize: 11.5, color: 'rgba(26,24,21,0.58)' }}>{tool.tool}</div>
                      <div style={{ flex: 1, height: 8, borderRadius: 999, background: 'rgba(26,24,21,0.06)' }}>
                        <div
                          style={{
                            width: `${selected.tool_usage[0]?.count ? Math.max((tool.count / selected.tool_usage[0].count) * 100, 10) : 0}%`,
                            height: '100%',
                            borderRadius: 999,
                            background: accent,
                          }}
                        />
                      </div>
                      <div style={{ fontSize: 11, color: 'rgba(26,24,21,0.48)', fontFamily: 'var(--font-mono)' }}>{tool.count}</div>
                    </div>
                  ))}
                  <div style={{ fontSize: 12, color: 'rgba(26,24,21,0.56)', marginTop: 4 }}>
                    Sutras promoted: <strong style={{ color: 'var(--kajal)' }}>{selected.learning.sutras_promoted}</strong>
                  </div>
                  <div style={{ fontSize: 12, color: 'rgba(26,24,21,0.56)' }}>
                    Active learnings: <strong style={{ color: 'var(--kajal)' }}>{selected.learning.sutras_active}</strong>
                  </div>
                  <div style={{ fontSize: 12, color: 'rgba(26,24,21,0.56)' }}>
                    Sankalpa updates: <strong style={{ color: 'var(--kajal)' }}>{selected.learning.sankalpa_updates}</strong>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <div
            style={{
              marginTop: 16,
              padding: 18,
              borderRadius: 18,
              border: '1px solid rgba(26,24,21,0.08)',
              background: 'rgba(252,250,242,0.9)',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' }}>
              <div style={{ fontSize: 18, fontWeight: 700, color: 'var(--kajal)', fontFamily: 'var(--font-hero)' }}>
                Recent architectural changes
              </div>
              <div style={{ fontSize: 12, color: 'rgba(26,24,21,0.48)' }}>
                pulled from Karma mutations, Smriti events, and Yantra traces
              </div>
            </div>

            <div style={{ display: 'grid', gap: 10, marginTop: 14 }}>
              {history.recent_changes
                .filter(change => change.agent === selected.name)
                .slice(0, 12)
                .map(change => (
                  <div
                    key={`${change.ts}-${change.title}-${change.detail}`}
                    style={{
                      display: 'grid',
                      gridTemplateColumns: '92px 110px 1fr',
                      gap: 12,
                      alignItems: 'start',
                      padding: '10px 12px',
                      borderRadius: 14,
                      background: 'rgba(26,24,21,0.03)',
                    }}
                  >
                    <div style={{ fontSize: 11, color: 'rgba(26,24,21,0.44)', fontFamily: 'var(--font-mono)' }}>
                      {relativeTime(change.ts)}
                    </div>
                    <div style={{ fontSize: 11.5, color: accent, fontWeight: 700, textTransform: 'capitalize' }}>
                      {change.category}
                    </div>
                    <div>
                      <div style={{ fontSize: 12.5, color: 'var(--kajal)', fontWeight: 600 }}>{change.title}</div>
                      <div style={{ marginTop: 3, fontSize: 12, color: 'rgba(26,24,21,0.58)', lineHeight: 1.5 }}>
                        {change.detail}
                      </div>
                    </div>
                  </div>
                ))}
            </div>
          </div>
        </>
      )}
    </div>
  )
}
