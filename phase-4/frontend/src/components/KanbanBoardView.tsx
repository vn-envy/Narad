import { useEffect, useState } from 'react'
import type { KanbanUpdatePayload } from '../hooks/useAvatara'

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

interface KanbanData extends Omit<KanbanUpdatePayload, 'columns'> {
  columns: Record<string, KanbanStep[]>
}

const STATUS_ORDER = ['backlog', 'in_progress', 'review', 'done', 'blocked']
const STATUS_LABELS: Record<string, string> = {
  backlog:     'Backlog',
  in_progress: 'In Progress',
  review:      'Review',
  done:        'Done',
  blocked:     'Blocked',
}
const STATUS_COLOURS: Record<string, string> = {
  backlog:     'rgba(252,250,242,0.12)',
  in_progress: '#c2410c',
  review:      '#92610a',
  done:        '#065f46',
  blocked:     '#7f1d1d',
}

const OWNER_COLOURS: Record<string, string> = {
  Matsya:      '#065f46',
  Varaha:      '#c2410c',
  Narasimha:   '#c2410c',
  Rama:        '#4a5728',
  Krishna:     '#065f46',
  Buddha:      '#92610a',
  Parashurama: '#57534e',
  Vamana:      '#78716c',
}

interface Props {
  sessionId: string | null
  kanbanUpdate: KanbanUpdatePayload | null
}

export function KanbanBoardView({ sessionId, kanbanUpdate }: Props) {
  const [board, setBoard] = useState<KanbanData | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Load board from API when sessionId changes
  useEffect(() => {
    if (!sessionId) return
    setLoading(true)
    fetch(`/kanban/${sessionId}`)
      .then(r => r.json())
      .then(data => { setBoard(data); setLoading(false) })
      .catch(e => { setError(String(e)); setLoading(false) })
  }, [sessionId])

  // Apply live SSE updates
  useEffect(() => {
    if (kanbanUpdate) setBoard(kanbanUpdate as unknown as KanbanData)
  }, [kanbanUpdate])

  if (!sessionId) {
    return (
      <div className="flex items-center justify-center h-full opacity-40 text-sm"
        style={{ color: 'rgba(252,250,242,0.5)', fontFamily: 'Space Grotesk, sans-serif' }}>
        No active plan session
      </div>
    )
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full opacity-40 text-sm"
        style={{ color: 'rgba(252,250,242,0.5)' }}>
        Loading board…
      </div>
    )
  }

  if (error) {
    return (
      <div className="p-4 text-sm" style={{ color: '#c2410c' }}>
        Failed to load kanban: {error}
      </div>
    )
  }

  if (!board || board.total === 0) {
    return (
      <div className="flex items-center justify-center h-full opacity-40 text-sm"
        style={{ color: 'rgba(252,250,242,0.5)' }}>
        No plan steps yet — ask Rama to create a plan
      </div>
    )
  }

  const columns = board.columns || {}

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Header */}
      <div
        className="flex items-center justify-between px-4 py-2 flex-shrink-0"
        style={{ borderBottom: '1px solid rgba(252,250,242,0.08)' }}
      >
        <span className="font-mono text-[10px] uppercase tracking-widest opacity-50"
          style={{ color: 'rgba(252,250,242,0.5)' }}>
          {board.total} steps · {board.done_count} done
          {board.blocked_count > 0 && ` · ${board.blocked_count} blocked`}
        </span>
      </div>

      {/* Board columns */}
      <div className="flex gap-3 p-4 overflow-x-auto flex-1 min-h-0">
        {STATUS_ORDER.map(status => {
          const steps = columns[status] || []
          const colour = STATUS_COLOURS[status]

          return (
            <div
              key={status}
              className="flex flex-col flex-shrink-0"
              style={{ width: 160, minWidth: 140 }}
            >
              {/* Column header */}
              <div
                className="flex items-center gap-2 px-2 py-1.5 rounded-t mb-2"
                style={{ background: `${colour}22`, borderBottom: `2px solid ${colour}` }}
              >
                <span
                  className="text-[10px] font-semibold uppercase tracking-wider"
                  style={{ color: colour === 'rgba(252,250,242,0.12)' ? 'rgba(252,250,242,0.5)' : colour }}
                >
                  {STATUS_LABELS[status]}
                </span>
                {steps.length > 0 && (
                  <span
                    className="ml-auto text-[9px] font-mono px-1.5 py-0.5 rounded-full"
                    style={{
                      background: `${colour}33`,
                      color: colour === 'rgba(252,250,242,0.12)' ? 'rgba(252,250,242,0.4)' : colour,
                    }}
                  >
                    {steps.length}
                  </span>
                )}
              </div>

              {/* Cards */}
              <div className="flex flex-col gap-2 overflow-y-auto flex-1">
                {steps.map(step => (
                  <StepCard key={step.step_id} step={step} />
                ))}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function StepCard({ step }: { step: KanbanStep }) {
  const [showDigest, setShowDigest] = useState(false)
  const ownerColour = OWNER_COLOURS[step.owner] || '#57534e'

  return (
    <div
      className="rounded px-2.5 py-2 cursor-pointer transition-all"
      style={{
        background: 'rgba(252,250,242,0.05)',
        border: '1px solid rgba(252,250,242,0.08)',
      }}
      onClick={() => step.result_digest && setShowDigest(v => !v)}
      title={step.result_digest ? 'Click to see result preview' : undefined}
    >
      <div className="flex items-start gap-1.5">
        {/* Owner badge */}
        <span
          className="flex-shrink-0 text-[8px] font-bold px-1.5 py-0.5 rounded-full mt-0.5"
          style={{ background: `${ownerColour}44`, color: ownerColour }}
        >
          {step.owner.slice(0, 2).toUpperCase()}
        </span>
        <span
          className="text-[11px] leading-tight flex-1"
          style={{ color: 'rgba(252,250,242,0.8)' }}
        >
          {step.title}
        </span>
      </div>

      {showDigest && step.result_digest && (
        <div
          className="mt-2 text-[10px] leading-snug"
          style={{
            color: 'rgba(252,250,242,0.5)',
            borderTop: '1px solid rgba(252,250,242,0.08)',
            paddingTop: 6,
          }}
        >
          {step.result_digest}…
        </div>
      )}

      {step.started_at && (
        <div className="mt-1 text-[9px] font-mono opacity-30"
          style={{ color: 'rgba(252,250,242,0.4)' }}>
          {new Date(step.started_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
        </div>
      )}
    </div>
  )
}
