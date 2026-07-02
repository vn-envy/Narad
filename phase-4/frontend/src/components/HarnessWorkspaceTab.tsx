import { useCallback, useEffect, useMemo, useState } from 'react'
import type { SessionInfo } from '@/hooks/useAvatara'
import { apiFetch, apiJson, apiUrl, type HarnessContextBundle, type HarnessOverview, type HarnessSessionRecord } from '@/lib/api'
import { relativeTime } from '@/lib/format-time'
import { AVATAR_COLOURS } from '@/lib/avatara-constants'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

interface Props {
  userId: string
  currentSession: SessionInfo | null
  onResumeSession: (sessionId: string) => Promise<boolean>
}

function planeAccent(key: string): string {
  switch (key) {
    case 'session':
      return 'var(--gagan)'
    case 'working':
      return 'var(--marigold)'
    case 'smriti':
      return 'var(--mor)'
    case 'governance':
      return 'var(--kajal)'
    default:
      return 'var(--marigold)'
  }
}

function toneForStatus(status: string): string {
  switch (status) {
    case 'ready':
      return 'var(--tulsi)'
    case 'warming':
      return 'var(--marigold)'
    case 'empty':
      return 'var(--loha)'
    default:
      return 'var(--kajal)'
  }
}

function compactNumber(value: number): string {
  if (value >= 1000) return `${(value / 1000).toFixed(1)}k`
  return String(value)
}

function lineClamp(text: string, limit = 110): string {
  const normalized = text.replace(/\s+/g, ' ').trim()
  if (normalized.length <= limit) return normalized
  return `${normalized.slice(0, limit)}…`
}

export function HarnessWorkspaceTab({ userId, currentSession, onResumeSession }: Props) {
  const [overview, setOverview] = useState<HarnessOverview | null>(null)
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(currentSession?.sessionId ?? null)
  const [detail, setDetail] = useState<HarnessContextBundle | null>(null)
  const [loading, setLoading] = useState(false)
  const [actionBusy, setActionBusy] = useState<string | null>(null)

  const loadOverview = useCallback(async (preferredSessionId?: string | null) => {
    setLoading(true)
    try {
      const params: Record<string, string> = { user_id: userId }
      if (preferredSessionId) params.session_id = preferredSessionId
      const data = await apiJson<HarnessOverview>(apiUrl('/harness/overview', params))
      setOverview(data)
      const resolvedSessionId = preferredSessionId || data.selected_session_id || data.sessions[0]?.session_id || null
      setSelectedSessionId(resolvedSessionId)
      setDetail(data.context ?? null)
    } catch {
      setOverview(null)
      setDetail(null)
    } finally {
      setLoading(false)
    }
  }, [userId])

  const loadSessionDetail = useCallback(async (sessionId: string) => {
    setSelectedSessionId(sessionId)
    try {
      const data = await apiJson<{ session: HarnessSessionRecord; context: HarnessContextBundle | null }>(
        apiUrl(`/harness/sessions/${sessionId}`, { user_id: userId }),
      )
      setDetail(data.context ?? null)
    } catch {
      setDetail(null)
    }
  }, [userId])

  useEffect(() => {
    loadOverview(currentSession?.sessionId ?? selectedSessionId)
  }, [currentSession?.sessionId, loadOverview])

  const runAction = useCallback(async (action: 'compact' | 'archive' | 'recover' | 'fork') => {
    if (!selectedSessionId) return
    setActionBusy(action)
    try {
      const path = action === 'fork'
        ? apiUrl(`/harness/sessions/${selectedSessionId}/fork`, { user_id: userId, title: `Fork · ${detail?.session.title ?? selectedSessionId.slice(0, 8)}` })
        : apiUrl(`/harness/sessions/${selectedSessionId}/${action}`, { user_id: userId })
      const response = await apiFetch(path, { method: 'POST' })
      if (!response.ok) throw new Error(action)
      const payload = await response.json()
      const nextSessionId = payload?.session?.session_id ?? selectedSessionId
      await loadOverview(nextSessionId)
      if (nextSessionId) {
        await loadSessionDetail(nextSessionId)
      }
    } finally {
      setActionBusy(null)
    }
  }, [detail?.session.title, loadOverview, loadSessionDetail, selectedSessionId, userId])

  const handleResume = useCallback(async () => {
    if (!selectedSessionId) return
    setActionBusy('resume')
    try {
      await onResumeSession(selectedSessionId)
    } finally {
      setActionBusy(null)
    }
  }, [onResumeSession, selectedSessionId])

  const sessions = overview?.sessions ?? []
  const selectedSession = detail?.session ?? sessions.find(session => session.session_id === selectedSessionId) ?? null
  const context = detail

  const selectedSessionBadges = useMemo(() => {
    if (!selectedSession) return []
    const badges: string[] = []
    if (selectedSession.archived) badges.push('Archived')
    if (selectedSession.source === 'fork') badges.push('Fork')
    if (selectedSession.restorable) badges.push('Restorable')
    if (selectedSession.karya?.total) badges.push(`${selectedSession.karya.total} Karya`)
    return badges
  }, [selectedSession])

  return (
    <div className="flex h-full min-h-0 flex-col overflow-auto p-4 md:p-5">
      <Card
        className="border-none shadow-none"
        style={{
          background: 'linear-gradient(135deg, rgba(252,250,242,0.98) 0%, rgba(246,240,226,0.92) 56%, rgba(245,235,215,0.96) 100%)',
          boxShadow: 'inset 0 0 0 1px rgba(45,42,38,0.08)',
        }}
      >
        <CardHeader className="gap-3">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="max-w-3xl space-y-2">
              <CardDescription className="font-mono text-[10px] uppercase tracking-[0.22em] text-muted-foreground">
                Workspace Architecture
              </CardDescription>
              <CardTitle className="text-3xl font-semibold tracking-tight text-[var(--kajal)]">
                Session continuity, working state, and Smriti in one workspace
              </CardTitle>
              <p className="max-w-2xl text-sm leading-relaxed text-muted-foreground">
                Narad now exposes the real harness contract directly: session lineage, restore state, context assembly,
                and governance are visible without digging through traces.
              </p>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <Badge variant="outline" className="border-marigold/35 bg-marigold/10 text-[11px] text-marigold">
                {overview?.runtime.status ?? 'unknown'}
              </Badge>
              <Badge variant="outline" className="border-border/80 bg-background/70 text-[11px]">
                {overview?.runtime.mode ?? 'cloud'}
              </Badge>
              <Button variant="outline" size="sm" disabled={loading} onClick={() => loadOverview(selectedSessionId)}>
                Refresh
              </Button>
            </div>
          </div>
        </CardHeader>
        <CardContent className="grid gap-3 md:grid-cols-4">
          {([
            { key: 'session', value: overview?.summary.session_count ?? 0, suffix: 'active sessions' },
            { key: 'working', value: overview?.summary.restorable_count ?? 0, suffix: 'restorable threads' },
            { key: 'smriti', value: overview?.summary.episode_count ?? 0, suffix: 'captured episodes' },
            { key: 'governance', value: overview?.summary.mutation_count ?? 0, suffix: 'governance mutations' },
          ] as const).map(item => (
            <div
              key={item.key}
              className="rounded-2xl px-4 py-4"
              style={{
                background: `linear-gradient(145deg, color-mix(in srgb, ${planeAccent(item.key)} 12%, white) 0%, rgba(255,255,255,0.7) 100%)`,
                boxShadow: 'inset 0 0 0 1px rgba(45,42,38,0.06)',
              }}
            >
              <div className="text-[10px] font-medium uppercase tracking-[0.2em] text-muted-foreground">
                {overview?.planes[item.key as keyof HarnessOverview['planes']]?.label ?? item.key}
              </div>
              <div className="mt-2 text-3xl font-semibold" style={{ color: planeAccent(item.key) }}>
                {compactNumber(item.value)}
              </div>
              <div className="mt-1 text-xs text-muted-foreground">
                {item.suffix}
              </div>
            </div>
          ))}
        </CardContent>
      </Card>

      <div className="mt-4 grid min-h-0 flex-1 gap-4 xl:grid-cols-[0.98fr_1.42fr]">
        <Card className="min-h-0 border-none shadow-none" style={{ boxShadow: 'inset 0 0 0 1px rgba(45,42,38,0.08)' }}>
          <CardHeader>
            <CardDescription className="font-mono text-[10px] uppercase tracking-[0.18em]">
              Session Plane
            </CardDescription>
            <CardTitle>Recent session lineage</CardTitle>
            <p className="text-sm leading-relaxed text-muted-foreground">
              Recent threads, forks, and archived branches live here. Pick a session to inspect how Narad will rehydrate it.
            </p>
          </CardHeader>
          <CardContent className="flex min-h-0 flex-col gap-3">
            {loading && sessions.length === 0 && (
              <div className="rounded-xl border border-dashed border-border/70 bg-secondary/35 p-4 text-sm text-muted-foreground">
                Loading session catalog…
              </div>
            )}
            <div className="flex max-h-[620px] flex-col gap-3 overflow-auto pr-1">
              {sessions.map(session => {
                const active = session.session_id === (selectedSessionId || currentSession?.sessionId)
                return (
                  <button
                    key={session.session_id}
                    type="button"
                    onClick={() => loadSessionDetail(session.session_id)}
                    className="rounded-2xl border px-4 py-4 text-left transition hover:border-marigold/35 hover:bg-secondary/35"
                    style={{
                      borderColor: active ? 'rgba(242, 142, 28, 0.35)' : 'rgba(45,42,38,0.08)',
                      background: active ? 'rgba(242,142,28,0.08)' : 'rgba(255,255,255,0.7)',
                    }}
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <div className="text-sm font-semibold text-foreground">
                        {lineClamp(session.title || session.session_id, 68)}
                      </div>
                      {currentSession?.sessionId === session.session_id && (
                        <Badge variant="outline" className="border-gagan/20 bg-gagan/10 text-[10px] text-gagan">
                          Live
                        </Badge>
                      )}
                      {session.archived && (
                        <Badge variant="outline" className="border-border/70 bg-background/80 text-[10px]">
                          Archived
                        </Badge>
                      )}
                      {session.source === 'fork' && (
                        <Badge variant="outline" className="border-mor/20 bg-mor/10 text-[10px] text-mor">
                          Fork
                        </Badge>
                      )}
                    </div>
                    <div className="mt-2 flex flex-wrap gap-2 text-[11px] text-muted-foreground">
                      <span>{session.turn_count} turns</span>
                      <span>{relativeTime(session.updated_at)}</span>
                      {session.parent_session_id && <span>from {session.parent_session_id.slice(0, 8)}</span>}
                      {session.restored_after_reset && <span>restored after reset</span>}
                    </div>
                    {(session.last_user_query || session.thread_summary) && (
                      <div className="mt-3 text-[12px] leading-relaxed text-muted-foreground">
                        {lineClamp(session.last_user_query || session.thread_summary || '', 140)}
                      </div>
                    )}
                  </button>
                )
              })}
              {!loading && sessions.length === 0 && (
                <div className="rounded-xl border border-dashed border-border/70 bg-secondary/35 p-4 text-sm text-muted-foreground">
                  No sessions have been captured yet. Start a conversation and Narad will build the session plane automatically.
                </div>
              )}
            </div>
          </CardContent>
        </Card>

        <div className="grid min-h-0 gap-4">
          <Card className="border-none shadow-none" style={{ boxShadow: 'inset 0 0 0 1px rgba(45,42,38,0.08)' }}>
            <CardHeader>
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="space-y-2">
                  <CardDescription className="font-mono text-[10px] uppercase tracking-[0.18em]">
                    Selected Session
                  </CardDescription>
                  <CardTitle>{selectedSession?.title ?? 'Choose a session'}</CardTitle>
                  <p className="max-w-3xl text-sm leading-relaxed text-muted-foreground">
                    This view shows exactly what Narad will bring into the next turn: thread memory, working state, durable recall, and governance context.
                  </p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  {selectedSessionBadges.map(badge => (
                    <Badge key={badge} variant="outline" className="border-border/70 bg-background/70 text-[10px]">
                      {badge}
                    </Badge>
                  ))}
                </div>
              </div>
            </CardHeader>
            <CardContent className="flex flex-wrap items-center gap-2">
              <Button size="sm" variant="outline" disabled={!selectedSessionId || actionBusy !== null} onClick={() => runAction('compact')}>
                {actionBusy === 'compact' ? 'Compacting…' : 'Compact'}
              </Button>
              <Button size="sm" variant="secondary" disabled={!selectedSessionId || actionBusy !== null} onClick={handleResume}>
                {actionBusy === 'resume' ? 'Resuming…' : 'Resume in chat'}
              </Button>
              <Button size="sm" variant="outline" disabled={!selectedSessionId || actionBusy !== null} onClick={() => runAction('fork')}>
                {actionBusy === 'fork' ? 'Forking…' : 'Fork'}
              </Button>
              {selectedSession?.archived ? (
                <Button size="sm" variant="secondary" disabled={!selectedSessionId || actionBusy !== null} onClick={() => runAction('recover')}>
                  {actionBusy === 'recover' ? 'Recovering…' : 'Recover'}
                </Button>
              ) : (
                <Button size="sm" variant="ghost" disabled={!selectedSessionId || actionBusy !== null} onClick={() => runAction('archive')}>
                  {actionBusy === 'archive' ? 'Archiving…' : 'Archive'}
                </Button>
              )}
            </CardContent>
          </Card>

          <div className="grid min-h-0 gap-4 lg:grid-cols-[1.08fr_0.92fr]">
            <div className="grid min-h-0 gap-4">
              <Card className="border-none shadow-none" style={{ boxShadow: 'inset 0 0 0 1px rgba(45,42,38,0.08)' }}>
                <CardHeader>
                  <CardDescription className="font-mono text-[10px] uppercase tracking-[0.18em]">
                    Context Assembly
                  </CardDescription>
                  <CardTitle>How Narad rebuilds context</CardTitle>
                </CardHeader>
                <CardContent className="grid gap-3">
                  {(context?.context_order ?? []).map(item => (
                    <div
                      key={item.key}
                      className="rounded-2xl border px-4 py-4"
                      style={{
                        borderColor: 'rgba(45,42,38,0.08)',
                        background: `linear-gradient(145deg, color-mix(in srgb, ${toneForStatus(item.status)} 10%, white) 0%, rgba(255,255,255,0.78) 100%)`,
                      }}
                    >
                      <div className="flex items-center gap-2">
                        <div className="text-sm font-semibold text-foreground">{item.label}</div>
                        <Badge variant="outline" className="text-[10px]" style={{ borderColor: 'rgba(45,42,38,0.12)' }}>
                          {item.status}
                        </Badge>
                      </div>
                      <div className="mt-2 text-[12px] leading-relaxed text-muted-foreground">
                        {item.detail}
                      </div>
                    </div>
                  ))}
                  {context?.rehydration_preview && (
                    <div className="rounded-2xl border border-border/70 bg-secondary/35 px-4 py-4">
                      <div className="text-[10px] font-medium uppercase tracking-[0.18em] text-muted-foreground">
                        Rehydration preview
                      </div>
                      <div className="mt-2 text-[12px] leading-relaxed text-foreground">
                        {lineClamp(context.rehydration_preview, 220)}
                      </div>
                    </div>
                  )}
                </CardContent>
              </Card>

              <Card className="border-none shadow-none" style={{ boxShadow: 'inset 0 0 0 1px rgba(45,42,38,0.08)' }}>
                <CardHeader>
                  <CardDescription className="font-mono text-[10px] uppercase tracking-[0.18em]">
                    Thread Memory
                  </CardDescription>
                  <CardTitle>Exact recent turns</CardTitle>
                </CardHeader>
                <CardContent className="grid gap-3">
                  {context?.thread_plane.summary && (
                    <div className="rounded-2xl border border-border/70 bg-background/75 px-4 py-3 text-[12px] leading-relaxed text-muted-foreground">
                      {context.thread_plane.summary}
                    </div>
                  )}
                  <div className="grid gap-3">
                    {(context?.thread_plane.recent_turns ?? []).map((turn, index) => (
                      <div key={`${turn.ts ?? index}-${index}`} className="rounded-2xl border border-border/70 bg-card/80 px-4 py-3">
                        <div className="mb-2 flex items-center gap-2">
                          <Badge variant="outline" className="text-[10px]">
                            {turn.role}
                          </Badge>
                          {turn.ts && (
                            <span className="text-[10px] text-muted-foreground">
                              {relativeTime(turn.ts)}
                            </span>
                          )}
                        </div>
                        <div className="text-[12px] leading-relaxed text-foreground">
                          {lineClamp(turn.text, 240)}
                        </div>
                      </div>
                    ))}
                    {context && context.thread_plane.recent_turns.length === 0 && (
                      <div className="rounded-xl border border-dashed border-border/70 bg-secondary/35 p-4 text-sm text-muted-foreground">
                        No exact turns are stored for this branch yet. Narad will rely on working-state summaries until the thread grows.
                      </div>
                    )}
                  </div>
                </CardContent>
              </Card>
            </div>

            <div className="grid min-h-0 gap-4">
              <Card className="border-none shadow-none" style={{ boxShadow: 'inset 0 0 0 1px rgba(45,42,38,0.08)' }}>
                <CardHeader>
                  <CardDescription className="font-mono text-[10px] uppercase tracking-[0.18em]">
                    Working-State Plane
                  </CardDescription>
                  <CardTitle>Current orchestration and Karya</CardTitle>
                </CardHeader>
                <CardContent className="grid gap-3">
                  <div className="flex flex-wrap gap-2">
                    {(context?.working_plane.avatars ?? []).map(avatar => (
                      <Badge
                        key={avatar}
                        variant="outline"
                        className="text-[10px]"
                        style={{
                          borderColor: `${AVATAR_COLOURS[avatar as keyof typeof AVATAR_COLOURS] ?? 'var(--marigold)'}33`,
                          color: AVATAR_COLOURS[avatar as keyof typeof AVATAR_COLOURS] ?? 'var(--kajal)',
                          background: 'rgba(255,255,255,0.75)',
                        }}
                      >
                        {avatar}
                      </Badge>
                    ))}
                    {(!context?.working_plane.avatars || context.working_plane.avatars.length === 0) && (
                      <span className="text-sm text-muted-foreground">No active avatars captured yet.</span>
                    )}
                  </div>
                  {context?.working_plane.karya && (
                    <div className="rounded-2xl border border-border/70 bg-background/75 px-4 py-4">
                      <div className="text-[11px] font-medium uppercase tracking-[0.18em] text-muted-foreground">
                        Karya
                      </div>
                      <div className="mt-2 flex flex-wrap gap-3 text-sm text-foreground">
                        <span>{context.working_plane.karya.total ?? 0} tasks</span>
                        <span>{context.working_plane.karya.done_count ?? 0} done</span>
                        <span>{context.working_plane.karya.blocked_count ?? 0} blocked</span>
                      </div>
                      {(context.working_plane.karya.active_titles ?? []).length > 0 && (
                        <div className="mt-3 grid gap-2">
                          {(context.working_plane.karya.active_titles ?? []).map(title => (
                            <div key={title} className="rounded-xl bg-secondary/40 px-3 py-2 text-[12px] text-foreground">
                              {title}
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                  {context?.working_plane.last_trace_session_id && (
                    <div className="text-[12px] leading-relaxed text-muted-foreground">
                      Last trace: <span className="font-mono text-foreground">{context.working_plane.last_trace_session_id}</span>
                    </div>
                  )}
                </CardContent>
              </Card>

              <Card className="border-none shadow-none" style={{ boxShadow: 'inset 0 0 0 1px rgba(45,42,38,0.08)' }}>
                <CardHeader>
                  <CardDescription className="font-mono text-[10px] uppercase tracking-[0.18em]">
                    Smriti Plane
                  </CardDescription>
                  <CardTitle>Durable memory signals</CardTitle>
                </CardHeader>
                <CardContent className="grid gap-3">
                  <div className="grid grid-cols-2 gap-3">
                    <div className="rounded-2xl bg-secondary/35 px-4 py-4">
                      <div className="text-[10px] uppercase tracking-[0.18em] text-muted-foreground">Episodes</div>
                      <div className="mt-2 text-2xl font-semibold text-[var(--mor)]">
                        {context?.smriti_plane.episode_count ?? 0}
                      </div>
                    </div>
                    <div className="rounded-2xl bg-secondary/35 px-4 py-4">
                      <div className="text-[10px] uppercase tracking-[0.18em] text-muted-foreground">Commitments</div>
                      <div className="mt-2 text-2xl font-semibold text-[var(--marigold)]">
                        {context?.smriti_plane.commitment_count ?? 0}
                      </div>
                    </div>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {(context?.smriti_plane.durable_layers ?? []).map(layer => (
                      <Badge key={layer} variant="outline" className="bg-background/70 text-[10px]">
                        {layer}
                      </Badge>
                    ))}
                  </div>
                  <div className="grid gap-2">
                    {(context?.smriti_plane.commitments ?? []).map(commitment => (
                      <div key={commitment.id} className="rounded-xl border border-border/70 bg-card/80 px-3 py-3">
                        <div className="mb-1 text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
                          {commitment.kind}
                        </div>
                        <div className="text-[12px] leading-relaxed text-foreground">
                          {lineClamp(commitment.content, 160)}
                        </div>
                      </div>
                    ))}
                    {context && context.smriti_plane.commitments.length === 0 && (
                      <div className="rounded-xl border border-dashed border-border/70 bg-secondary/35 p-4 text-sm text-muted-foreground">
                        This branch does not yet have session-specific Sankalpa commitments recorded.
                      </div>
                    )}
                  </div>
                </CardContent>
              </Card>

              <Card className="border-none shadow-none" style={{ boxShadow: 'inset 0 0 0 1px rgba(45,42,38,0.08)' }}>
                <CardHeader>
                  <CardDescription className="font-mono text-[10px] uppercase tracking-[0.18em]">
                    Governance Plane
                  </CardDescription>
                  <CardTitle>Dharma, Karma, and Yantra signals</CardTitle>
                </CardHeader>
                <CardContent className="grid gap-3">
                  <div className="flex flex-wrap gap-2">
                    <Badge variant="outline" className="bg-background/70 text-[10px]">
                      Runtime · {context?.governance_plane.runtime_status ?? overview?.runtime.status ?? 'unknown'}
                    </Badge>
                    <Badge variant="outline" className="bg-background/70 text-[10px]">
                      {context?.governance_plane.mutation_count ?? 0} mutations
                    </Badge>
                    <Badge variant="outline" className="bg-background/70 text-[10px]">
                      {context?.governance_plane.swapna_pending ?? overview?.summary.swapna_pending ?? 0} swapna pending
                    </Badge>
                  </div>
                  <div className="grid gap-2">
                    {(context?.governance_plane.recent_mutations ?? []).slice(0, 4).map(mutation => (
                      <div key={mutation.id} className="rounded-xl border border-border/70 bg-card/80 px-3 py-3">
                        <div className="mb-1 flex items-center gap-2">
                          <Badge variant="outline" className="text-[10px]">
                            {mutation.action.replace(/_/g, ' ')}
                          </Badge>
                          <span className="text-[10px] text-muted-foreground">{relativeTime(mutation.ts)}</span>
                        </div>
                        <div className="text-[12px] leading-relaxed text-foreground">
                          {lineClamp(mutation.detail || 'Mutation recorded', 140)}
                        </div>
                      </div>
                    ))}
                    {context && context.governance_plane.recent_mutations.length === 0 && (
                      <div className="rounded-xl border border-dashed border-border/70 bg-secondary/35 p-4 text-sm text-muted-foreground">
                        No session-linked governance mutations have been recorded yet.
                      </div>
                    )}
                  </div>
                </CardContent>
              </Card>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
