import type { CSSProperties } from 'react'
import type { ProjectExecution, ProjectTask } from '@/lib/api'

type TaskAction = 'resume' | 'block' | 'complete'

function truncate(text: string | null | undefined, limit = 150): string {
  const value = (text ?? '').trim()
  if (!value) return '—'
  return value.length > limit ? `${value.slice(0, limit - 1)}…` : value
}

function formatTime(value: string | null | undefined): string {
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

function TaskList({
  title,
  accent,
  tasks,
  empty,
  actionLabel,
  onAction,
}: {
  title: string
  accent: string
  tasks: ProjectTask[]
  empty: string
  actionLabel?: string
  onAction?: (taskId: string) => void
}) {
  return (
    <section style={listCardStyle}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span style={{ width: 10, height: 10, borderRadius: 999, background: accent, display: 'inline-block' }} />
        <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--kajal)' }}>{title}</div>
        <div style={{ marginLeft: 'auto', fontSize: 11, color: 'rgba(26,24,21,0.42)' }}>{tasks.length}</div>
      </div>
      <div style={{ display: 'grid', gap: 10, marginTop: 12 }}>
        {tasks.length === 0 ? (
          <div style={emptyStyle}>{empty}</div>
        ) : (
          tasks.map(task => (
            <article key={task.task_id} style={{ ...subCardStyle, boxShadow: `inset 3px 0 0 ${accent}` }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--kajal)' }}>{truncate(task.title, 170)}</div>
                <div style={{ marginLeft: 'auto', display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                  {task.owner && <span style={tagStyle}>{task.owner}</span>}
                  <span style={tagStyle}>{task.kind.replace(/_/g, ' ')}</span>
                </div>
              </div>
              {task.description && (
                <div style={{ marginTop: 8, fontSize: 12, lineHeight: 1.55, color: 'rgba(26,24,21,0.56)' }}>
                  {truncate(task.description, 180)}
                </div>
              )}
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 10 }}>
                <span style={{ fontSize: 11, color: 'rgba(26,24,21,0.4)' }}>Updated {formatTime(task.updated_at)}</span>
                {actionLabel && onAction && (
                  <button type="button" onClick={() => onAction(task.task_id)} style={actionButtonStyle}>
                    {actionLabel}
                  </button>
                )}
              </div>
            </article>
          ))
        )}
      </div>
    </section>
  )
}

interface Props {
  execution: ProjectExecution | null
  onTaskStatusChange: (taskId: string, action: TaskAction) => void
}

export function ExecutionPanel({ execution, onTaskStatusChange }: Props) {
  if (!execution) {
    return (
      <div style={{ padding: 22, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <div style={{ maxWidth: 520, padding: 24, borderRadius: 20, border: '1px solid rgba(26,24,21,0.08)', background: 'rgba(252,250,242,0.86)', textAlign: 'center' }}>
          <div style={{ fontSize: 22, fontWeight: 700, color: 'var(--kajal)' }}>Execution will appear here</div>
          <p style={{ marginTop: 10, fontSize: 14, lineHeight: 1.6, color: 'rgba(26,24,21,0.56)' }}>
            Pick a project to see what is active now, what should happen next, and what is blocked.
          </p>
        </div>
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', minHeight: 0 }}>
      <div style={{ padding: '18px 18px 14px', borderBottom: '1px solid rgba(26,24,21,0.08)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <div>
            <div style={{ fontSize: 10, letterSpacing: '0.16em', textTransform: 'uppercase', color: 'rgba(26,24,21,0.42)' }}>Execution</div>
            <div style={{ marginTop: 4, fontSize: 22, fontWeight: 700, color: 'var(--kajal)' }}>{execution.project_name}</div>
          </div>
          <div style={{ marginLeft: 'auto', display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <span style={headerTagStyle}>{execution.active_agents.length} active agents</span>
            {execution.active_session?.session_id && <span style={headerTagStyle}>{execution.active_session.session_id.slice(0, 8)}</span>}
          </div>
        </div>
        <div style={{ marginTop: 8, fontSize: 13, color: 'rgba(26,24,21,0.54)' }}>
          {truncate(execution.current_goal || execution.active_session?.query, 180)}
        </div>
      </div>

      <div style={{ padding: 18, display: 'grid', gap: 14 }}>
        <div style={{ display: 'grid', gap: 14, gridTemplateColumns: 'repeat(2, minmax(0, 1fr))' }}>
          <TaskList
            title="Now"
            accent="var(--marigold)"
            tasks={execution.now}
            empty="Nothing is actively in motion yet."
            actionLabel="Complete"
            onAction={taskId => onTaskStatusChange(taskId, 'complete')}
          />
          <TaskList
            title="Next"
            accent="var(--gagan)"
            tasks={execution.next.slice(0, 4)}
            empty="No ready-next tasks yet."
            actionLabel="Start"
            onAction={taskId => onTaskStatusChange(taskId, 'resume')}
          />
        </div>

        <div style={{ display: 'grid', gap: 14, gridTemplateColumns: 'repeat(2, minmax(0, 1fr))' }}>
          <TaskList
            title="Blocked"
            accent="var(--kesari)"
            tasks={execution.blocked}
            empty="No blockers right now."
            actionLabel="Resume"
            onAction={taskId => onTaskStatusChange(taskId, 'resume')}
          />
          <TaskList
            title="Recently done"
            accent="var(--tulsi)"
            tasks={execution.recent_done.slice(0, 4)}
            empty="No completed tasks recorded yet."
          />
        </div>

        <section style={listCardStyle}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--kajal)' }}>Live activity</div>
            <div style={{ marginLeft: 'auto', fontSize: 11, color: 'rgba(26,24,21,0.42)' }}>{execution.recent_events.length} recent events</div>
          </div>
          <div style={{ display: 'grid', gap: 10, marginTop: 12 }}>
            {execution.recent_events.length === 0 ? (
              <div style={emptyStyle}>No recent trace activity yet.</div>
            ) : (
              execution.recent_events.slice(-8).reverse().map((event, index) => (
                <article key={`${event.ts ?? 'evt'}-${index}`} style={subCardStyle}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--loha)', textTransform: 'uppercase' }}>{event.avatar ?? 'system'}</div>
                    <div style={{ marginLeft: 'auto', fontSize: 10, color: 'rgba(26,24,21,0.38)' }}>{formatTime(event.ts)}</div>
                  </div>
                  <div style={{ marginTop: 6, fontSize: 12, lineHeight: 1.55, color: 'var(--kajal)' }}>{truncate(event.task || event.event, 150)}</div>
                </article>
              ))
            )}
          </div>
        </section>
      </div>
    </div>
  )
}

const listCardStyle: CSSProperties = {
  padding: 16,
  borderRadius: 18,
  border: '1px solid rgba(26,24,21,0.08)',
  background: 'rgba(252,250,242,0.86)',
}

const subCardStyle: CSSProperties = {
  padding: '12px 13px',
  borderRadius: 14,
  border: '1px solid rgba(26,24,21,0.08)',
  background: 'rgba(252,250,242,0.95)',
}

const tagStyle: CSSProperties = {
  padding: '4px 7px',
  borderRadius: 999,
  background: 'rgba(243,239,225,0.92)',
  border: '1px solid rgba(26,24,21,0.08)',
  fontSize: 10,
  color: 'rgba(26,24,21,0.52)',
}

const headerTagStyle: CSSProperties = {
  ...tagStyle,
  fontSize: 11,
}

const emptyStyle: CSSProperties = {
  padding: 14,
  borderRadius: 14,
  border: '1px dashed rgba(26,24,21,0.12)',
  color: 'rgba(26,24,21,0.38)',
  fontSize: 12,
}

const actionButtonStyle: CSSProperties = {
  marginLeft: 'auto',
  padding: '6px 10px',
  borderRadius: 999,
  border: '1px solid rgba(26,24,21,0.08)',
  background: 'rgba(243,239,225,0.92)',
  fontSize: 11,
  fontWeight: 600,
  color: 'var(--kajal)',
  cursor: 'pointer',
}
