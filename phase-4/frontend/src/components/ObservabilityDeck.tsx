import { useEffect, useMemo, useState } from 'react'
import type { AvatarName, AvatarStatus, SessionInfo, StepEvent } from '../hooks/useAvatara'
import {
  apiFetch,
  type ArchitectureScorecard,
  type KarmaMutation,
  type RuntimeCapabilities,
  type SwapnaInboxItem,
} from '@/lib/api'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { AVATAR_COLOURS, AVATAR_DISCIPLINES, AVATAR_NAMES, DEVA } from '@/lib/avatara-constants'
import { relativeTime } from '@/lib/format-time'

type GraphMode = 'avatars' | 'disciplines' | 'mutations'

interface Props {
  open: boolean
  avatars: Record<AvatarName, AvatarStatus>
  currentSession: SessionInfo | null
  stepEvents: StepEvent[]
  sessionTotals: { promptTokens: number; completionTokens: number; totalTokens: number }
  capabilities: RuntimeCapabilities | null
  compact?: boolean
  metricsOnly?: boolean
}

interface BarDatum {
  label: string
  value: number
  color: string
  meta?: string
}

interface SutraMetric {
  id: string
  avatar: string
  score: number
  status: 'pending' | 'active' | 'reverted'
}

function fmtCompact(value: number): string {
  if (value >= 1000) return `${(value / 1000).toFixed(1)}k`
  return String(value)
}

function StatCard({
  title,
  value,
  subtitle,
  accent,
}: {
  title: string
  value: string
  subtitle: string
  accent: string
}) {
  return (
    <Card
      className="border-none shadow-none"
      style={{
        background:
          `linear-gradient(145deg, color-mix(in srgb, ${accent} 10%, var(--paper)) 0%, var(--paper) 72%)`,
        boxShadow: 'inset 0 0 0 1px color-mix(in srgb, var(--kajal) 8%, transparent)',
      }}
      size="sm"
    >
      <CardHeader className="pb-1">
        <CardDescription className="font-mono text-[10px] uppercase tracking-[0.18em]">
          {title}
        </CardDescription>
        <CardTitle className="text-[24px]" style={{ color: accent }}>
          {value}
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-0 text-[12px] leading-relaxed text-muted-foreground">
        {subtitle}
      </CardContent>
    </Card>
  )
}

function MiniBarChart({ data, emptyLabel }: { data: BarDatum[]; emptyLabel: string }) {
  const max = Math.max(...data.map(item => item.value), 0)

  if (max === 0) {
    return (
      <div
        className="flex min-h-[180px] items-center justify-center rounded-xl border border-dashed border-border/70 bg-secondary/35 text-sm text-muted-foreground"
      >
        {emptyLabel}
      </div>
    )
  }

  return (
    <div className="flex min-h-[180px] flex-col gap-3">
      {data.map(item => (
        <div key={item.label} className="grid grid-cols-[110px_1fr_auto] items-center gap-3">
          <div className="truncate text-[12px] font-medium text-foreground">
            {item.label}
          </div>
          <div className="h-2.5 rounded-full bg-secondary/70">
            <div
              className="h-full rounded-full transition-all"
              style={{
                width: `${Math.max((item.value / max) * 100, 6)}%`,
                background: item.color,
              }}
            />
          </div>
          <div className="text-right font-mono text-[11px] text-muted-foreground">
            {fmtCompact(item.value)}
            {item.meta ? ` · ${item.meta}` : ''}
          </div>
        </div>
      ))}
    </div>
  )
}

function MutationFeed({ mutations }: { mutations: KarmaMutation[] }) {
  if (mutations.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-border/70 bg-secondary/35 p-4 text-sm text-muted-foreground">
        No architecture mutations recorded yet.
      </div>
    )
  }

  return (
    <div className="flex max-h-[280px] flex-col gap-3 overflow-auto pr-1">
      {mutations.slice(0, 8).map(mutation => (
        <div
          key={mutation.id}
          className="rounded-xl border border-border/70 bg-card/70 px-3 py-3"
          style={{ boxShadow: 'inset 0 0 0 1px rgba(45,42,38,0.03)' }}
        >
          <div className="mb-1 flex items-center gap-2">
            <Badge variant="outline" className="border-marigold/20 bg-marigold/5 text-[10px] text-marigold">
              {mutation.action.replace(/_/g, ' ')}
            </Badge>
            <span className="text-[11px] font-medium text-foreground">{mutation.actor}</span>
            <span className="ml-auto text-[10px] font-mono text-muted-foreground">
              {relativeTime(mutation.ts)}
            </span>
          </div>
          <p className="text-[12px] leading-relaxed text-muted-foreground">
            {mutation.detail || mutation.entity_type}
          </p>
          <div className="mt-2 flex flex-wrap gap-2 text-[10px] font-mono text-muted-foreground">
            <span>{mutation.entity_type}</span>
            {mutation.policy && <span>{mutation.policy}</span>}
            {mutation.provenance_ids && mutation.provenance_ids.length > 0 && (
              <span>{mutation.provenance_ids.length} sources</span>
            )}
          </div>
        </div>
      ))}
    </div>
  )
}

export function ObservabilityDeck({
  open,
  avatars,
  currentSession,
  stepEvents,
  sessionTotals,
  capabilities,
  compact = false,
  metricsOnly = false,
}: Props) {
  const [graphMode, setGraphMode] = useState<GraphMode>('avatars')
  const [scorecard, setScorecard] = useState<ArchitectureScorecard | null>(null)
  const [mutations, setMutations] = useState<KarmaMutation[]>([])
  const [swapnaInbox, setSwapnaInbox] = useState<SwapnaInboxItem[]>([])
  const [sutras, setSutras] = useState<SutraMetric[]>([])

  useEffect(() => {
    if (!open) return

    let cancelled = false

    apiFetch('/architecture/scorecard')
      .then(res => (res.ok ? res.json() : null))
      .then((data: ArchitectureScorecard | null) => {
        if (!cancelled) setScorecard(data)
      })
      .catch(() => {
        if (!cancelled) setScorecard(null)
      })

    apiFetch('/karma/mutations?limit=24')
      .then(res => (res.ok ? res.json() : null))
      .then((data: { mutations?: KarmaMutation[] } | null) => {
        if (!cancelled) setMutations(Array.isArray(data?.mutations) ? data!.mutations : [])
      })
      .catch(() => {
        if (!cancelled) setMutations([])
      })

    apiFetch('/swapna/inbox')
      .then(res => (res.ok ? res.json() : null))
      .then((data: { items?: SwapnaInboxItem[] } | null) => {
        if (!cancelled) setSwapnaInbox(Array.isArray(data?.items) ? data!.items : [])
      })
      .catch(() => {
        if (!cancelled) setSwapnaInbox([])
      })

    apiFetch('/sutras')
      .then(res => (res.ok ? res.json() : null))
      .then((data: { sutras?: SutraMetric[] } | null) => {
        if (!cancelled) setSutras(Array.isArray(data?.sutras) ? data!.sutras : [])
      })
      .catch(() => {
        if (!cancelled) setSutras([])
      })

    return () => {
      cancelled = true
    }
  }, [open, currentSession?.sessionId])

  const toolCalls = stepEvents.filter(event => event.kind === 'tool_call').length
  const activeAgents = AVATAR_NAMES.filter(name => avatars[name]?.state === 'active').length
  const degradedTools = capabilities
    ? Object.values(capabilities.tool_families).filter(item => !item.available).length
    : 0
  const sessionTokPerSec = currentSession?.tokPerSec ?? 0
  const promptPct = sessionTotals.totalTokens > 0
    ? Math.round((sessionTotals.promptTokens / sessionTotals.totalTokens) * 100)
    : 0
  const completionPct = sessionTotals.totalTokens > 0
    ? Math.round((sessionTotals.completionTokens / sessionTotals.totalTokens) * 100)
    : 0

  const avatarGraph = useMemo<BarDatum[]>(() => {
    return AVATAR_NAMES.map(name => {
      const status = avatars[name]
      const latency = status?.latencyMs ?? 0
      const stepCount = stepEvents.filter(event => event.avatar === name).length
      return {
        label: name,
        value: latency || stepCount,
        color: AVATAR_COLOURS[name],
        meta: latency ? `${(latency / 1000).toFixed(1)}s` : `${stepCount} steps`,
      }
    })
  }, [avatars, stepEvents])

  const disciplineGraph = useMemo<BarDatum[]>(() => {
    const counts = new Map<string, number>()
    for (const event of stepEvents) {
      const key = event.discipline ?? 'general'
      counts.set(key, (counts.get(key) ?? 0) + 1)
    }
    return Array.from(counts.entries())
      .sort((a, b) => b[1] - a[1])
      .slice(0, 6)
      .map(([label, value], index) => ({
        label,
        value,
        color: [ 'var(--marigold)', 'var(--mor)', 'var(--kajal)', 'var(--haldi)', 'var(--loha)', 'var(--gagan)' ][index % 6],
      }))
  }, [stepEvents])

  const mutationGraph = useMemo<BarDatum[]>(() => {
    const counts = new Map<string, number>()
    for (const mutation of mutations) {
      const key = mutation.action.replace(/_/g, ' ')
      counts.set(key, (counts.get(key) ?? 0) + 1)
    }
    return Array.from(counts.entries())
      .sort((a, b) => b[1] - a[1])
      .slice(0, 6)
      .map(([label, value], index) => ({
        label,
        value,
        color: [ 'var(--marigold)', 'var(--tulsi)', 'var(--kajal)', 'var(--kesari)', 'var(--loha)', 'var(--gagan)' ][index % 6] ?? 'var(--marigold)',
      }))
  }, [mutations])

  const chartData = graphMode === 'avatars'
    ? avatarGraph
    : graphMode === 'disciplines'
    ? disciplineGraph
    : mutationGraph

  const avatarScoreBars = useMemo<BarDatum[]>(() => {
    const byAvatar = new Map<string, number[]>()
    for (const sutra of sutras) {
      if (!byAvatar.has(sutra.avatar)) byAvatar.set(sutra.avatar, [])
      byAvatar.get(sutra.avatar)?.push(sutra.score)
    }
    return Array.from(byAvatar.entries())
      .map(([label, scores]) => ({
        label,
        value: Math.round((scores.reduce((sum, score) => sum + score, 0) / scores.length) * 100),
        color: AVATAR_COLOURS[label as keyof typeof AVATAR_COLOURS] ?? 'var(--marigold)',
        meta: `${scores.length} scored`,
      }))
      .sort((a, b) => b.value - a.value)
  }, [sutras])

  const topTapasAgent = avatarScoreBars[0]?.label ?? '—'
  const avgTapasScore = avatarScoreBars.length > 0
    ? (avatarScoreBars.reduce((sum, item) => sum + item.value, 0) / avatarScoreBars.length / 100).toFixed(2)
    : null

  const capabilityGroups = useMemo(() => {
    if (!capabilities) return []
    const providerEntries = Object.entries(capabilities.providers).slice(0, 6)
    return providerEntries.map(([name, flag]) => ({
      name,
      available: flag.available,
      reason: flag.reason ?? '',
    }))
  }, [capabilities])

  if (compact) {
    return (
      <div
        style={{
          height: '100%',
          display: 'flex',
          flexDirection: 'column',
          minHeight: 0,
          overflowX: 'hidden',
          background: 'linear-gradient(180deg, rgba(252,250,242,0.94) 0%, rgba(243,239,225,0.88) 100%)',
        }}
      >
        <div style={{ padding: '16px 16px 12px', borderBottom: '1px solid rgba(26,24,21,0.08)' }}>
          <div style={{ fontSize: 10, letterSpacing: '0.16em', textTransform: 'uppercase', color: 'rgba(26,24,21,0.42)' }}>
            Drishti
          </div>
          <div style={{ marginTop: 4, fontSize: 20, fontWeight: 700, color: 'var(--kajal)', fontFamily: 'var(--font-hero)' }}>
            Runtime Visibility
          </div>
          <p style={{ marginTop: 6, fontSize: 12, lineHeight: 1.5, color: 'rgba(26,24,21,0.56)' }}>
            Cultural-core health, traces, and mutation flow for the current workspace.
          </p>
        </div>

        <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: 16 }}>
          <div className="grid gap-3">
            <StatCard
              title="Runtime"
              value={capabilities?.status === 'healthy' ? 'Healthy' : 'Degraded'}
              subtitle={
                capabilities
                  ? `${capabilities.build.runtime_mode} · ${capabilities.issue_count} issue${capabilities.issue_count === 1 ? '' : 's'}`
                  : 'Waiting for runtime contract'
              }
              accent={capabilities?.status === 'healthy' ? 'var(--tulsi)' : 'var(--marigold)'}
            />
            <StatCard
              title="Session Trace"
              value={currentSession?.sessionId ? `${toolCalls}` : '0'}
              subtitle={
                currentSession
                  ? `${sessionTotals.totalTokens > 0 ? fmtCompact(sessionTotals.totalTokens) : '0'} tok · ${currentSession.totalMs ? (currentSession.totalMs / 1000).toFixed(1) : '0.0'}s`
                  : 'No active session yet'
              }
              accent="var(--kajal)"
            />
            <StatCard
              title="Smriti Core"
              value={scorecard ? String(scorecard.smriti_core_imports) : '—'}
              subtitle={
                scorecard
                  ? `${scorecard.legacy_direct_memory_imports} legacy imports remain`
                  : 'Architecture scorecard pending'
              }
              accent="var(--mor)"
            />
            <StatCard
              title="Learning Flow"
              value={String(swapnaInbox.length)}
              subtitle={`${mutations.length} mutations · ${degradedTools} degraded tools`}
              accent="var(--kesari)"
            />
          </div>

          <div
            style={{
              marginTop: 14,
              padding: 14,
              borderRadius: 18,
              background: 'rgba(252,250,242,0.82)',
              border: '1px solid rgba(26,24,21,0.08)',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
              <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--kajal)' }}>Trace Graph</div>
              <div className="ml-auto flex gap-1 rounded-full bg-secondary/70 p-1">
                {([
                  ['avatars', 'Agents'],
                  ['disciplines', 'Disciplines'],
                ] as const).map(([mode, label]) => (
                  <button
                    key={mode}
                    type="button"
                    onClick={() => setGraphMode(mode)}
                    className="rounded-full px-2.5 py-1 text-[10px] font-medium transition-colors"
                    style={{
                      background: graphMode === mode ? 'var(--paper)' : 'transparent',
                      color: graphMode === mode ? 'var(--kajal)' : 'rgba(45,42,38,0.55)',
                    }}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>
            <MiniBarChart data={chartData.slice(0, 5)} emptyLabel={`No ${graphMode} signals yet.`} />
          </div>

          <div
            style={{
              marginTop: 14,
              padding: 14,
              borderRadius: 18,
              background: 'rgba(252,250,242,0.82)',
              border: '1px solid rgba(26,24,21,0.08)',
            }}
          >
            <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--kajal)', marginBottom: 10 }}>
              Runtime Surface
            </div>
            <div className="flex flex-wrap gap-2">
              {capabilityGroups.length === 0 && (
                <span className="text-[12px] text-muted-foreground">Capability data unavailable.</span>
              )}
              {capabilityGroups.map(group => (
                <Badge
                  key={group.name}
                  variant="outline"
                  className="rounded-full bg-paper/85 text-[11px]"
                  style={{
                    borderColor: group.available ? 'rgba(6,95,70,0.25)' : 'rgba(194,65,12,0.2)',
                    color: group.available ? 'var(--tulsi)' : 'var(--marigold)',
                  }}
                  title={group.reason || `${group.name} available`}
                >
                  {group.name}
                </Badge>
              ))}
            </div>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div
        style={{
          height: '100%',
          minHeight: 0,
          overflowX: 'hidden',
          overflowY: 'auto',
          WebkitOverflowScrolling: 'touch',
          touchAction: 'pan-y',
          padding: '16px',
          background:
            'linear-gradient(180deg, rgba(252,250,242,0.94) 0%, rgba(243,239,225,0.88) 100%)',
          borderBottom: '1px solid rgba(26,24,21,0.08)',
        }}
      >
      <div className="mb-4 flex flex-wrap items-end gap-3">
        <div>
          <div className="font-mono text-[10px] uppercase tracking-[0.24em] text-muted-foreground">
            {metricsOnly ? 'दिव्यदृष्टि  Metrics Surface' : 'दृष्टि  Runtime Visibility'}
          </div>
          <h2
            className="mt-1 text-[20px] font-semibold leading-none"
            style={{ fontFamily: 'var(--font-hero)', color: 'var(--kajal)' }}
          >
            {metricsOnly ? 'Metrics, health, and runtime visibility' : 'Cultural Core, traced live'}
          </h2>
        </div>
        <div className="ml-auto flex flex-wrap gap-2">
          {AVATAR_NAMES.map(name => (
            <Badge
              key={name}
              variant="outline"
              className="rounded-full border-border/70 bg-card/80 px-2.5 py-1 text-[11px]"
              style={{
                color: AVATAR_COLOURS[name],
                borderColor: `${AVATAR_COLOURS[name]}33`,
              }}
              title={AVATAR_DISCIPLINES[name].join(', ')}
            >
              {DEVA[name]} · {name}
            </Badge>
          ))}
        </div>
      </div>

      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        {metricsOnly ? (
          <>
            <StatCard
              title="Session Tokens"
              value={sessionTotals.totalTokens > 0 ? fmtCompact(sessionTotals.totalTokens) : '0'}
              subtitle={sessionTotals.totalTokens > 0 ? `${promptPct}% prompt · ${completionPct}% completion` : 'No token usage yet'}
              accent="var(--kajal)"
            />
            <StatCard
              title="Throughput"
              value={sessionTokPerSec > 0 ? `${sessionTokPerSec}` : '—'}
              subtitle={sessionTokPerSec > 0 ? 'tokens per second' : 'Waiting for a completed synthesis'}
              accent="var(--marigold)"
            />
            <StatCard
              title="Avg Tapas Score"
              value={avgTapasScore ?? '—'}
              subtitle={avgTapasScore ? `${sutras.length} scored Sutras in memory` : 'No Sutra score data yet'}
              accent="var(--mor)"
            />
            <StatCard
              title="Top Agent"
              value={topTapasAgent}
              subtitle={topTapasAgent !== '—' ? 'highest average Sutra score' : 'Awaiting enough learning traces'}
              accent="var(--tulsi)"
            />
          </>
        ) : (
          <>
            <StatCard
              title="Runtime"
              value={capabilities?.status === 'healthy' ? 'Healthy' : 'Degraded'}
              subtitle={
                capabilities
                  ? `${capabilities.build.runtime_mode} mode · ${capabilities.issue_count} issue${capabilities.issue_count === 1 ? '' : 's'}`
                  : 'Waiting for runtime contract'
              }
              accent={capabilities?.status === 'healthy' ? 'var(--tulsi)' : 'var(--marigold)'}
            />
            <StatCard
              title="Session Trace"
              value={currentSession?.sessionId ? `${toolCalls}` : '0'}
              subtitle={
                currentSession
                  ? `${sessionTotals.totalTokens > 0 ? fmtCompact(sessionTotals.totalTokens) : '0'} tokens · ${currentSession.totalMs ? (currentSession.totalMs / 1000).toFixed(1) : '0.0'}s`
                  : 'No active session yet'
              }
              accent="var(--kajal)"
            />
            <StatCard
              title="Smriti Core"
              value={scorecard ? String(scorecard.smriti_core_imports) : '—'}
              subtitle={
                scorecard
                  ? `${scorecard.legacy_direct_memory_imports} legacy runtime imports remain`
                  : 'Architecture scorecard pending'
              }
              accent="var(--mor)"
            />
            <StatCard
              title="Learning Flow"
              value={String(swapnaInbox.length)}
              subtitle={`${mutations.length} recent mutations · ${degradedTools} degraded tool families`}
              accent="var(--kesari)"
            />
          </>
        )}
      </div>

      <div className={`mt-4 grid gap-4 ${metricsOnly ? 'xl:grid-cols-[1.45fr_1fr]' : 'xl:grid-cols-[1.55fr_1fr]'}`}>
        <Card
          className="border-none shadow-none"
          style={{
            background: 'rgba(252,250,242,0.82)',
            boxShadow: 'inset 0 0 0 1px rgba(45,42,38,0.08)',
          }}
        >
          <CardHeader className="border-b border-border/70 pb-3">
            <div className="flex flex-wrap items-center gap-2">
              <div>
                <CardTitle>Trace Graph</CardTitle>
                <CardDescription>
                  Switch between agent throughput, discipline coverage, and cultural-core mutations.
                </CardDescription>
              </div>
              <div className="ml-auto flex gap-1 rounded-full bg-secondary/70 p-1">
                {([
                  ['avatars', 'Agents'],
                  ['disciplines', 'Disciplines'],
                  ['mutations', 'Mutations'],
                ] as const).map(([mode, label]) => (
                  <button
                    key={mode}
                    type="button"
                    onClick={() => setGraphMode(mode)}
                    className="rounded-full px-3 py-1 text-[11px] font-medium transition-colors"
                    style={{
                      background: graphMode === mode ? 'var(--paper)' : 'transparent',
                      color: graphMode === mode ? 'var(--kajal)' : 'rgba(45,42,38,0.55)',
                      boxShadow: graphMode === mode ? '0 1px 2px rgba(45,42,38,0.08)' : 'none',
                    }}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>
          </CardHeader>
          <CardContent className="grid gap-4 pt-4 lg:grid-cols-[1.2fr_0.8fr]">
            <MiniBarChart
              data={chartData}
              emptyLabel={`No ${graphMode} signals yet.`}
            />
            <div className="space-y-3">
              <div className="rounded-xl border border-border/70 bg-secondary/30 p-3">
                <div className="mb-2 font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
                  Live Signals
                </div>
                <div className="grid gap-2 text-[12px] text-muted-foreground">
                  <div className="flex items-center justify-between">
                    <span>Active agents</span>
                    <strong style={{ color: 'var(--kajal)' }}>{activeAgents}</strong>
                  </div>
                  <div className="flex items-center justify-between">
                    <span>Tool calls</span>
                    <strong style={{ color: 'var(--kajal)' }}>{toolCalls}</strong>
                  </div>
                  <div className="flex items-center justify-between">
                    <span>Disciplines touched</span>
                    <strong style={{ color: 'var(--kajal)' }}>{disciplineGraph.length}</strong>
                  </div>
                  <div className="flex items-center justify-between">
                    <span>Swapna proposals</span>
                    <strong style={{ color: 'var(--kajal)' }}>{swapnaInbox.length}</strong>
                  </div>
                </div>
              </div>

              <div className="rounded-xl border border-border/70 bg-card/80 p-3">
                <div className="mb-2 font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
                  Runtime surface
                </div>
                <div className="flex flex-wrap gap-2">
                  {capabilityGroups.length === 0 && (
                    <span className="text-[12px] text-muted-foreground">Capability data unavailable.</span>
                  )}
                  {capabilityGroups.map(group => (
                    <Badge
                      key={group.name}
                      variant="outline"
                      className="rounded-full bg-paper/85 text-[11px]"
                      style={{
                        borderColor: group.available ? 'rgba(6,95,70,0.25)' : 'rgba(194,65,12,0.2)',
                        color: group.available ? 'var(--tulsi)' : 'var(--marigold)',
                      }}
                      title={group.reason || `${group.name} available`}
                    >
                      {group.name}
                    </Badge>
                  ))}
                </div>
              </div>
            </div>
          </CardContent>
        </Card>

        {metricsOnly ? (
          <Card
            className="border-none shadow-none"
            style={{
              background: 'rgba(252,250,242,0.82)',
              boxShadow: 'inset 0 0 0 1px rgba(45,42,38,0.08)',
            }}
          >
            <CardHeader className="border-b border-border/70 pb-3">
              <CardTitle>Runtime Scorecard</CardTitle>
              <CardDescription>
                Capability coverage, architecture integrity, and the current operating surface.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4 pt-4">
              <div className="grid gap-2 sm:grid-cols-2">
                <div className="rounded-xl border border-border/70 bg-secondary/30 p-3">
                  <div className="font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
                    Startup checks
                  </div>
                  <div className="mt-1 text-[20px] font-semibold text-foreground">
                    {capabilities?.startup_checks.filter(check => check.ok).length ?? 0}/{capabilities?.startup_checks.length ?? 0}
                  </div>
                  <div className="text-[12px] text-muted-foreground">
                    Passing capability and environment checks.
                  </div>
                </div>
                <div className="rounded-xl border border-border/70 bg-secondary/30 p-3">
                  <div className="font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
                    Local readiness
                  </div>
                  <div className="mt-1 text-[20px] font-semibold text-foreground">
                    {capabilities?.local_ready.frontend_transport_agnostic ? 'Ready' : 'Partial'}
                  </div>
                  <div className="text-[12px] text-muted-foreground">
                    Frontend transport and local-mode seams.
                  </div>
                </div>
              </div>

              {scorecard && (
                <div className="rounded-xl border border-border/70 bg-card/80 p-3">
                  <div className="mb-2 font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
                    Architecture scorecard
                  </div>
                  <div className="grid gap-2 text-[12px] text-muted-foreground">
                    <div className="flex items-center justify-between">
                      <span>Smriti core imports</span>
                      <strong style={{ color: 'var(--kajal)' }}>{scorecard.smriti_core_imports}</strong>
                    </div>
                    <div className="flex items-center justify-between">
                      <span>Legacy direct memory imports</span>
                      <strong style={{ color: 'var(--kajal)' }}>{scorecard.legacy_direct_memory_imports}</strong>
                    </div>
                    <div className="flex items-center justify-between">
                      <span>Swapna enabled</span>
                      <strong style={{ color: 'var(--kajal)' }}>{scorecard.swapna_enabled ? 'Yes' : 'No'}</strong>
                    </div>
                    <div className="flex items-center justify-between">
                      <span>Karma mutation log</span>
                      <strong style={{ color: 'var(--kajal)' }}>{scorecard.karma_mutation_log_enabled ? 'Yes' : 'No'}</strong>
                    </div>
                  </div>
                </div>
              )}

              <div className="rounded-xl border border-border/70 bg-card/80 p-3">
                <div className="mb-2 font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
                  Token flow
                </div>
                <div className="grid gap-2 text-[12px] text-muted-foreground">
                  <div className="flex items-center justify-between">
                    <span>Prompt tokens</span>
                    <strong style={{ color: 'var(--kajal)' }}>{sessionTotals.promptTokens.toLocaleString()}</strong>
                  </div>
                  <div className="flex items-center justify-between">
                    <span>Completion tokens</span>
                    <strong style={{ color: 'var(--kajal)' }}>{sessionTotals.completionTokens.toLocaleString()}</strong>
                  </div>
                  <div className="flex items-center justify-between">
                    <span>Total tokens</span>
                    <strong style={{ color: 'var(--kajal)' }}>{sessionTotals.totalTokens.toLocaleString()}</strong>
                  </div>
                  <div className="flex items-center justify-between">
                    <span>Session throughput</span>
                    <strong style={{ color: 'var(--kajal)' }}>{sessionTokPerSec > 0 ? `${sessionTokPerSec} tok/s` : '—'}</strong>
                  </div>
                </div>
                {sessionTotals.totalTokens > 0 && (
                  <div className="mt-3 grid gap-2">
                    <div>
                      <div className="mb-1 flex items-center justify-between text-[11px] text-muted-foreground">
                        <span>Prompt</span>
                        <span>{promptPct}%</span>
                      </div>
                      <div className="h-2 rounded-full bg-secondary/70">
                        <div className="h-full rounded-full" style={{ width: `${promptPct}%`, background: 'var(--kajal)' }} />
                      </div>
                    </div>
                    <div>
                      <div className="mb-1 flex items-center justify-between text-[11px] text-muted-foreground">
                        <span>Completion</span>
                        <span>{completionPct}%</span>
                      </div>
                      <div className="h-2 rounded-full bg-secondary/70">
                        <div className="h-full rounded-full" style={{ width: `${completionPct}%`, background: 'var(--marigold)' }} />
                      </div>
                    </div>
                  </div>
                )}
              </div>

              <div className="rounded-xl border border-border/70 bg-card/80 p-3">
                <div className="mb-2 font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
                  Tapas score by agent
                </div>
                <MiniBarChart data={avatarScoreBars} emptyLabel="No Tapas score data yet." />
              </div>

              <div className="rounded-xl border border-border/70 bg-card/80 p-3">
                <div className="mb-2 font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
                  Capability surface
                </div>
                <div className="flex flex-wrap gap-2">
                  {capabilityGroups.length === 0 && (
                    <span className="text-[12px] text-muted-foreground">Capability data unavailable.</span>
                  )}
                  {capabilityGroups.map(group => (
                    <Badge
                      key={group.name}
                      variant="outline"
                      className="rounded-full bg-paper/85 text-[11px]"
                      style={{
                        borderColor: group.available ? 'rgba(6,95,70,0.25)' : 'rgba(194,65,12,0.2)',
                        color: group.available ? 'var(--tulsi)' : 'var(--marigold)',
                      }}
                      title={group.reason || `${group.name} available`}
                    >
                      {group.name}
                    </Badge>
                  ))}
                </div>
              </div>
            </CardContent>
          </Card>
        ) : (
          <Card
            className="border-none shadow-none"
            style={{
              background: 'rgba(252,250,242,0.82)',
              boxShadow: 'inset 0 0 0 1px rgba(45,42,38,0.08)',
            }}
          >
            <CardHeader className="border-b border-border/70 pb-3">
              <CardTitle>Karma and Swapna Feed</CardTitle>
              <CardDescription>
                Recent architectural mutations, provenance-backed learning moves, and dream-cycle outputs.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4 pt-4">
              <div className="grid gap-2 sm:grid-cols-2">
                <div className="rounded-xl border border-border/70 bg-secondary/30 p-3">
                  <div className="font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
                    Mutation ledger
                  </div>
                  <div className="mt-1 text-[20px] font-semibold text-foreground">{mutations.length}</div>
                  <div className="text-[12px] text-muted-foreground">
                    Changes recorded through Karma.
                  </div>
                </div>
                <div className="rounded-xl border border-border/70 bg-secondary/30 p-3">
                  <div className="font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
                    Dream inbox
                  </div>
                  <div className="mt-1 text-[20px] font-semibold text-foreground">{swapnaInbox.length}</div>
                  <div className="text-[12px] text-muted-foreground">
                    Pending Swapna consolidations.
                  </div>
                </div>
              </div>

              {swapnaInbox.length > 0 && (
                <div className="rounded-xl border border-border/70 bg-card/80 p-3">
                  <div className="mb-2 font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
                    Latest Swapna
                  </div>
                  <div className="text-[12px] text-muted-foreground">
                    {swapnaInbox[0].source_episode_ids.length} source episodes ·{' '}
                    {swapnaInbox[0].suggestions.facts.length} facts ·{' '}
                    {swapnaInbox[0].suggestions.scenarios.length} scenarios
                  </div>
                  {swapnaInbox[0].suggestions.candidate_keywords.length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-2">
                      {swapnaInbox[0].suggestions.candidate_keywords.slice(0, 6).map(keyword => (
                        <Badge key={keyword} variant="outline" className="rounded-full bg-paper/85 text-[11px]">
                          {keyword}
                        </Badge>
                      ))}
                    </div>
                  )}
                </div>
              )}

              <MutationFeed mutations={mutations} />
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  )
}
