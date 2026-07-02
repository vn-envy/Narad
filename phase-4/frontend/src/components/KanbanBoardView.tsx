import { useEffect, useMemo, useState } from 'react'
import type { KanbanUpdatePayload } from '../hooks/useAvatara'
import { apiFetch } from '@/lib/api'

interface KanbanStep {
  session_id: string
  step_id: number
  title: string
  owner: string
  status: string
  started_at: string | null
  completed_at: string | null
  result_digest: string | null
}

interface KanbanBoard {
  session_id: string
  columns: Record<string, KanbanStep[]>
  total: number
  done_count: number
  blocked_count: number
}

export interface KaryaBoardContext {
  sessionId: string | null
  total: number
  doneCount: number
  blockedCount: number
  inMotionCount: number
  backlogCount: number
  reviewCount: number
  owners: number
  activeTitles: string[]
}

const STATUS_ORDER = ['backlog', 'in_progress', 'review', 'done', 'blocked'] as const

const STATUS_META: Record<(typeof STATUS_ORDER)[number], { label: string; color: string; background: string }> = {
  backlog: {
    label: 'Backlog',
    color: 'var(--loha)',
    background: 'rgba(87,83,78,0.08)',
  },
  in_progress: {
    label: 'In Progress',
    color: 'var(--marigold)',
    background: 'rgba(194,65,12,0.08)',
  },
  review: {
    label: 'Review',
    color: 'var(--haldi)',
    background: 'rgba(252,211,77,0.16)',
  },
  done: {
    label: 'Done',
    color: 'var(--tulsi)',
    background: 'rgba(6,95,70,0.08)',
  },
  blocked: {
    label: 'Blocked',
    color: 'var(--kesari)',
    background: 'rgba(154,52,18,0.08)',
  },
}

const OWNER_META: Record<string, string> = {
  Matsya: 'var(--tulsi)',
  Rama: 'var(--kajal)',
  Krishna: 'var(--mor)',
  Parashurama: 'var(--loha)',
}

function formatTime(value: string | null): string {
  if (!value) return '—'
  try {
    return new Date(value).toLocaleString('en-US', {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return value
  }
}

function sessionLabel(sessionId: string): string {
  return sessionId.length > 18 ? `${sessionId.slice(0, 8)}…${sessionId.slice(-4)}` : sessionId
}

function truncate(text: string | null | undefined, limit = 120): string {
  const value = (text ?? '').trim()
  if (!value) return '—'
  return value.length > limit ? `${value.slice(0, limit - 1)}…` : value
}

interface Props {
  sessionId: string | null
  kanbanUpdate: KanbanUpdatePayload | null
  preferredSessionIds?: string[]
  projectName?: string | null
  onBoardContextChange?: (context: KaryaBoardContext) => void
}

function pickPreferredBoard(
  boards: KanbanBoard[],
  selectedSessionId: string | null,
  preferredSessionIds: string[],
  fallbackSessionId: string | null
): string | null {
  const boardIds = new Set(boards.map(board => board.session_id))
  if (selectedSessionId && boardIds.has(selectedSessionId)) return selectedSessionId
  const preferredMatch = preferredSessionIds.find(id => boardIds.has(id))
  if (preferredMatch) return preferredMatch
  if (fallbackSessionId && boardIds.has(fallbackSessionId)) return fallbackSessionId
  return boards[0]?.session_id ?? null
}

function KaryaStat({ label, value, hint }: { label: string; value: string; hint: string }) {
  return (
    <div style={{ padding: '12px 14px', borderRadius: 16, background: 'rgba(252,250,242,0.82)', border: '1px solid rgba(26,24,21,0.08)' }}>
      <div style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.14em', color: 'rgba(26,24,21,0.42)' }}>{label}</div>
      <div style={{ marginTop: 6, fontSize: 18, fontWeight: 700, color: 'var(--kajal)' }}>{value}</div>
      <div style={{ marginTop: 2, fontSize: 12, color: 'rgba(26,24,21,0.5)' }}>{hint}</div>
    </div>
  )
}

export function KanbanBoardView({
  sessionId,
  kanbanUpdate,
  preferredSessionIds = [],
  projectName,
  onBoardContextChange,
}: Props) {
  const [boards, setBoards] = useState<KanbanBoard[]>([])
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(sessionId)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false

    async function loadBoards() {
      setLoading(true)
      setError(null)

      try {
        if (kanbanUpdate) {
          const liveBoard = kanbanUpdate as unknown as KanbanBoard
          if (!cancelled) {
            setBoards([liveBoard])
            setSelectedSessionId(liveBoard.session_id)
            setLoading(false)
          }
          return
        }

        const nextBoards: KanbanBoard[] = []
        const seen = new Set<string>()

        if (sessionId) {
          const sessionRes = await apiFetch(`/kanban/${sessionId}`)
          const sessionBoard: KanbanBoard | null = sessionRes.ok ? await sessionRes.json() : null
          if (sessionBoard && sessionBoard.total > 0) {
            nextBoards.push(sessionBoard)
            seen.add(sessionBoard.session_id)
          }
        }

        const res = await apiFetch('/kanban')
        const data = res.ok ? await res.json() : { boards: [] }
        const activeBoards: KanbanBoard[] = Array.isArray(data.boards) ? data.boards : []
        for (const board of activeBoards) {
          if (seen.has(board.session_id)) continue
          nextBoards.push(board)
          seen.add(board.session_id)
        }

        if (!cancelled) {
          setBoards(nextBoards)
          setSelectedSessionId(current =>
            pickPreferredBoard(nextBoards, current, preferredSessionIds, sessionId)
          )
          setLoading(false)
        }
      } catch (caught) {
        if (!cancelled) {
          setBoards([])
          setError(String(caught))
          setLoading(false)
        }
      }
    }

    loadBoards()
    return () => {
      cancelled = true
    }
  }, [kanbanUpdate, preferredSessionIds, sessionId])

  const visibleBoards = useMemo(() => {
    if (preferredSessionIds.length === 0) return boards
    const related = boards.filter(board => preferredSessionIds.includes(board.session_id))
    return related.length > 0 ? related : boards
  }, [boards, preferredSessionIds])

  const selectedBoard = useMemo(() => {
    const match = visibleBoards.find(board => board.session_id === selectedSessionId)
    return match ?? visibleBoards[0] ?? null
  }, [selectedSessionId, visibleBoards])

  const allSteps = useMemo(() => {
    if (!selectedBoard) return []
    return STATUS_ORDER.flatMap(status => selectedBoard.columns[status] ?? [])
  }, [selectedBoard])

  const focusSteps = useMemo(() => {
    if (!selectedBoard) return []
    return [
      ...(selectedBoard.columns.in_progress ?? []),
      ...(selectedBoard.columns.review ?? []),
      ...(selectedBoard.columns.backlog ?? []).slice(0, 2),
    ].slice(0, 5)
  }, [selectedBoard])

  const blockedSteps = useMemo(() => (selectedBoard?.columns.blocked ?? []).slice(0, 4), [selectedBoard])

  useEffect(() => {
    if (!onBoardContextChange) return
    if (!selectedBoard) {
      onBoardContextChange({
        sessionId: null,
        total: 0,
        doneCount: 0,
        blockedCount: 0,
        inMotionCount: 0,
        backlogCount: 0,
        reviewCount: 0,
        owners: 0,
        activeTitles: [],
      })
      return
    }

    const inProgress = selectedBoard.columns.in_progress ?? []
    const review = selectedBoard.columns.review ?? []
    const backlog = selectedBoard.columns.backlog ?? []

    onBoardContextChange({
      sessionId: selectedBoard.session_id,
      total: selectedBoard.total,
      doneCount: selectedBoard.done_count,
      blockedCount: selectedBoard.blocked_count,
      inMotionCount: inProgress.length + review.length,
      backlogCount: backlog.length,
      reviewCount: review.length,
      owners: new Set(allSteps.map(step => step.owner)).size,
      activeTitles: [...inProgress, ...review].slice(0, 4).map(step => step.title),
    })
  }, [allSteps, onBoardContextChange, selectedBoard])

  if (loading) {
    return <div style={{ padding: 24, color: 'rgba(26,24,21,0.45)' }}>Loading task boards…</div>
  }

  if (error) {
    return <div style={{ padding: 24, color: 'var(--kesari)' }}>Failed to load kanban boards: {error}</div>
  }

  if (!selectedBoard || selectedBoard.total === 0) {
    const hasProjectContext = preferredSessionIds.length > 0 || Boolean(projectName)
    return (
      <div
        style={{
          height: '100%',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          padding: 32,
        }}
      >
        <div
          style={{
            maxWidth: 560,
            padding: 28,
            borderRadius: 24,
            background: 'rgba(252,250,242,0.82)',
            border: '1px solid rgba(26,24,21,0.08)',
            textAlign: 'center',
          }}
        >
          <div style={{ fontSize: 24, fontWeight: 700, color: 'var(--kajal)' }}>
            {hasProjectContext ? 'No active Karya board for this project yet' : 'No task board yet'}
          </div>
          <p style={{ marginTop: 10, fontSize: 14, lineHeight: 1.6, color: 'rgba(26,24,21,0.55)' }}>
            {hasProjectContext
              ? `Ask Rama to break ${projectName ?? 'this project'} into steps and the board will appear here as the execution spine of the work.`
              : 'Ask Rama to create a plan and the steps will appear here as a live Karya board.'}
          </p>
        </div>
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
      <div style={{ padding: '18px 18px 14px', borderBottom: '1px solid rgba(26,24,21,0.08)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <div>
            <div style={{ fontSize: 10, letterSpacing: '0.16em', textTransform: 'uppercase', color: 'rgba(26,24,21,0.42)' }}>
              Karya
            </div>
            <div style={{ marginTop: 4, fontSize: 22, fontWeight: 700, color: 'var(--kajal)' }}>
              {projectName ? `${projectName} Board` : 'Karya Board'}
            </div>
          </div>
          <div style={{ marginLeft: 'auto', display: 'flex', gap: 10, flexWrap: 'wrap' }}>
            <span style={{ fontSize: 12, color: 'rgba(26,24,21,0.52)' }}>{selectedBoard.total} steps</span>
            <span style={{ fontSize: 12, color: 'rgba(26,24,21,0.52)' }}>{selectedBoard.done_count} done</span>
            {selectedBoard.blocked_count > 0 && <span style={{ fontSize: 12, color: 'var(--kesari)' }}>{selectedBoard.blocked_count} blocked</span>}
          </div>
        </div>

        {visibleBoards.length > 1 && (
          <div style={{ display: 'flex', gap: 8, marginTop: 14, flexWrap: 'wrap' }}>
            {visibleBoards.map(board => (
              <button
                key={board.session_id}
                type="button"
                onClick={() => setSelectedSessionId(board.session_id)}
                style={{
                  padding: '8px 12px',
                  borderRadius: 999,
                  border:
                    board.session_id === selectedBoard.session_id
                      ? '1px solid rgba(194,65,12,0.18)'
                      : '1px solid rgba(26,24,21,0.08)',
                  background:
                    board.session_id === selectedBoard.session_id
                      ? 'rgba(194,65,12,0.08)'
                      : 'rgba(252,250,242,0.78)',
                  color:
                    board.session_id === selectedBoard.session_id
                      ? 'var(--marigold)'
                      : 'rgba(26,24,21,0.58)',
                  fontSize: 12,
                  fontWeight: 600,
                  cursor: 'pointer',
                }}
              >
                {sessionLabel(board.session_id)} · {board.total}
              </button>
            ))}
          </div>
        )}
      </div>

      <div style={{ padding: '14px 18px 0', display: 'grid', gap: 12, gridTemplateColumns: 'repeat(4, minmax(0, 1fr))' }}>
        <KaryaStat label="In motion" value={String((selectedBoard.columns.in_progress ?? []).length + (selectedBoard.columns.review ?? []).length)} hint="active + waiting for review" />
        <KaryaStat label="Next up" value={String((selectedBoard.columns.backlog ?? []).length)} hint="ready to be picked up" />
        <KaryaStat label="Blocked" value={String((selectedBoard.columns.blocked ?? []).length)} hint="needs intervention" />
        <KaryaStat label="Owners" value={String(new Set(allSteps.map(step => step.owner)).size)} hint="avatars touching this board" />
      </div>

      <div style={{ padding: '14px 18px 0', display: 'grid', gap: 12, gridTemplateColumns: 'minmax(0, 1.2fr) minmax(280px, 0.8fr)' }}>
        <section style={{ padding: 16, borderRadius: 18, background: 'rgba(252,250,242,0.82)', border: '1px solid rgba(26,24,21,0.08)' }}>
          <div style={{ fontSize: 11, letterSpacing: '0.12em', textTransform: 'uppercase', color: 'rgba(26,24,21,0.44)' }}>Immediate focus</div>
          <div style={{ display: 'grid', gap: 10, marginTop: 12 }}>
            {focusSteps.length === 0 ? (
              <div style={{ padding: 14, borderRadius: 14, border: '1px dashed rgba(26,24,21,0.12)', color: 'rgba(26,24,21,0.38)', fontSize: 12 }}>
                No active or ready steps yet.
              </div>
            ) : (
              focusSteps.map(step => {
                const meta = STATUS_META[(step.status as keyof typeof STATUS_META) ?? 'backlog']
                return (
                  <article
                    key={`${step.session_id}-${step.step_id}`}
                    style={{
                      padding: '12px 13px',
                      borderRadius: 16,
                      background: 'rgba(252,250,242,0.9)',
                      border: '1px solid rgba(26,24,21,0.08)',
                      boxShadow: `inset 3px 0 0 ${meta.color}`,
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                      <span
                        style={{
                          padding: '4px 8px',
                          borderRadius: 999,
                          background: `${OWNER_META[step.owner] ?? 'var(--loha)'}18`,
                          color: OWNER_META[step.owner] ?? 'var(--loha)',
                          fontSize: 10,
                          fontWeight: 700,
                          letterSpacing: '0.08em',
                          textTransform: 'uppercase',
                        }}
                      >
                        {step.owner}
                      </span>
                      <span style={{ fontSize: 10, color: meta.color, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em' }}>
                        {meta.label}
                      </span>
                      <span style={{ marginLeft: 'auto', fontSize: 10, color: 'rgba(26,24,21,0.38)' }}>Step {step.step_id}</span>
                    </div>
                    <div style={{ fontSize: 13, lineHeight: 1.55, color: 'var(--kajal)' }}>{truncate(step.title, 150)}</div>
                    {(step.started_at || step.completed_at || step.result_digest) && (
                      <div style={{ marginTop: 8, fontSize: 11, lineHeight: 1.45, color: 'rgba(26,24,21,0.48)' }}>
                        {step.started_at && <div>Started: {formatTime(step.started_at)}</div>}
                        {step.completed_at && <div>Completed: {formatTime(step.completed_at)}</div>}
                        {step.result_digest && <div>{truncate(step.result_digest, 95)}</div>}
                      </div>
                    )}
                  </article>
                )
              })
            )}
          </div>
        </section>

        <section style={{ padding: 16, borderRadius: 18, background: 'rgba(252,250,242,0.82)', border: '1px solid rgba(26,24,21,0.08)' }}>
          <div style={{ fontSize: 11, letterSpacing: '0.12em', textTransform: 'uppercase', color: 'rgba(26,24,21,0.44)' }}>Blockers and lane health</div>
          <div style={{ display: 'grid', gap: 10, marginTop: 12 }}>
            {blockedSteps.length === 0 ? (
              <div style={{ padding: 14, borderRadius: 14, border: '1px dashed rgba(26,24,21,0.12)', color: 'rgba(26,24,21,0.38)', fontSize: 12 }}>
                No blocked steps right now. The board can keep moving.
              </div>
            ) : (
              blockedSteps.map(step => (
                <article
                  key={`${step.session_id}-${step.step_id}`}
                  style={{
                    padding: '12px 13px',
                    borderRadius: 16,
                    background: 'rgba(252,250,242,0.9)',
                    border: '1px solid rgba(26,24,21,0.08)',
                    boxShadow: 'inset 3px 0 0 var(--kesari)',
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                    <span style={{ fontSize: 10, fontWeight: 700, textTransform: 'uppercase', color: 'var(--kesari)', letterSpacing: '0.08em' }}>
                      {step.owner}
                    </span>
                    <span style={{ marginLeft: 'auto', fontSize: 10, color: 'rgba(26,24,21,0.38)' }}>Step {step.step_id}</span>
                  </div>
                  <div style={{ fontSize: 12, lineHeight: 1.55, color: 'var(--kajal)' }}>{truncate(step.title, 120)}</div>
                  {step.result_digest && (
                    <div style={{ marginTop: 8, fontSize: 11, lineHeight: 1.45, color: 'rgba(26,24,21,0.48)' }}>
                      {truncate(step.result_digest, 90)}
                    </div>
                  )}
                </article>
              ))
            )}
          </div>
        </section>
      </div>

      <div style={{ flex: 1, minHeight: 0, overflowX: 'auto', overflowY: 'hidden', padding: 18 }}>
        <div style={{ display: 'grid', gap: 14, minWidth: 980, gridTemplateColumns: 'repeat(5, minmax(180px, 1fr))', height: '100%' }}>
          {STATUS_ORDER.map(status => {
            const meta = STATUS_META[status]
            const steps = selectedBoard.columns[status] ?? []
            return (
              <section
                key={status}
                style={{
                  display: 'flex',
                  flexDirection: 'column',
                  minHeight: 0,
                  borderRadius: 20,
                  background: 'rgba(252,250,242,0.7)',
                  border: '1px solid rgba(26,24,21,0.08)',
                  overflow: 'hidden',
                }}
              >
                <div
                  style={{
                    padding: '12px 14px',
                    borderBottom: '1px solid rgba(26,24,21,0.08)',
                    background: meta.background,
                    display: 'flex',
                    alignItems: 'center',
                    gap: 10,
                  }}
                >
                  <span style={{ width: 10, height: 10, borderRadius: 999, background: meta.color, display: 'inline-block' }} />
                  <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--kajal)' }}>{meta.label}</span>
                  <span style={{ marginLeft: 'auto', fontSize: 11, color: 'rgba(26,24,21,0.45)' }}>{steps.length}</span>
                </div>

                <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: 12 }}>
                  {steps.length === 0 ? (
                    <div
                      style={{
                        padding: 14,
                        borderRadius: 14,
                        border: '1px dashed rgba(26,24,21,0.12)',
                        color: 'rgba(26,24,21,0.35)',
                        fontSize: 12,
                        textAlign: 'center',
                      }}
                    >
                      No tasks here yet
                    </div>
                  ) : (
                    steps.map(step => (
                      <article
                        key={`${step.session_id}-${step.step_id}`}
                        style={{
                          marginBottom: 10,
                          padding: '12px 12px 11px',
                          borderRadius: 16,
                          background: 'rgba(252,250,242,0.9)',
                          border: '1px solid rgba(26,24,21,0.08)',
                          boxShadow: `inset 3px 0 0 ${meta.color}`,
                        }}
                      >
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                          <span
                            style={{
                              padding: '4px 8px',
                              borderRadius: 999,
                              background: `${OWNER_META[step.owner] ?? 'var(--loha)'}18`,
                              color: OWNER_META[step.owner] ?? 'var(--loha)',
                              fontSize: 10,
                              fontWeight: 700,
                              letterSpacing: '0.08em',
                              textTransform: 'uppercase',
                            }}
                          >
                            {step.owner}
                          </span>
                          <span style={{ marginLeft: 'auto', fontSize: 10, color: 'rgba(26,24,21,0.38)' }}>Step {step.step_id}</span>
                        </div>
                        <div style={{ fontSize: 13, lineHeight: 1.55, color: 'var(--kajal)' }}>{step.title}</div>
                        {(step.started_at || step.completed_at || step.result_digest) && (
                          <div style={{ marginTop: 10, fontSize: 11, lineHeight: 1.5, color: 'rgba(26,24,21,0.48)' }}>
                            {step.started_at && <div>Started: {formatTime(step.started_at)}</div>}
                            {step.completed_at && <div>Completed: {formatTime(step.completed_at)}</div>}
                            {step.result_digest && <div>{truncate(step.result_digest, 100)}</div>}
                          </div>
                        )}
                      </article>
                    ))
                  )}
                </div>
              </section>
            )
          })}
        </div>
      </div>
    </div>
  )
}
