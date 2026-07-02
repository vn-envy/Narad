import { useMemo, useState, type CSSProperties } from 'react'
import type { ProjectTask } from '@/lib/api'
import { TaskComposer } from './TaskComposer'

type TaskAction = 'resume' | 'block' | 'complete'

const GROUPS = [
  { key: 'in_progress', label: 'In progress', accent: 'var(--marigold)' },
  { key: 'review', label: 'Review', accent: 'var(--haldi)' },
  { key: 'todo', label: 'Ready next', accent: 'var(--gagan)' },
  { key: 'blocked', label: 'Blocked', accent: 'var(--kesari)' },
  { key: 'done', label: 'Recently done', accent: 'var(--tulsi)' },
] as const

function truncate(text: string | null | undefined, limit = 150): string {
  const value = (text ?? '').trim()
  if (!value) return '—'
  return value.length > limit ? `${value.slice(0, limit - 1)}…` : value
}

function sortWeight(priority: string): number {
  if (priority === 'high') return 0
  if (priority === 'medium') return 1
  return 2
}

interface Props {
  tasks: ProjectTask[]
  activeSessionId?: string | null
  onTaskStatusChange: (taskId: string, action: TaskAction) => void
  onTaskCreate: (draft: {
    title: string
    description: string
    owner: string | null
    priority: string
    kind: string
  }) => Promise<void>
  onTaskPatch: (taskId: string, patch: Partial<ProjectTask>) => Promise<void>
}

export function BacklogBoardPanel({
  tasks,
  activeSessionId,
  onTaskStatusChange,
  onTaskCreate,
  onTaskPatch,
}: Props) {
  const [assigneeFilter, setAssigneeFilter] = useState('all')
  const [statusFilter, setStatusFilter] = useState('all')
  const [priorityFilter, setPriorityFilter] = useState('all')
  const [activeOnly, setActiveOnly] = useState(false)
  const [editingTaskId, setEditingTaskId] = useState<string | null>(null)
  const [editingDraft, setEditingDraft] = useState<Partial<ProjectTask>>({})

  const assignees = useMemo(() => (
    Array.from(
      new Set(tasks.map(task => task.owner).filter(Boolean) as string[])
    ).sort((a, b) => a.localeCompare(b))
  ), [tasks])

  const filteredTasks = useMemo(() => {
    return tasks
      .filter(task => assigneeFilter === 'all' || task.owner === assigneeFilter)
      .filter(task => statusFilter === 'all' || task.status === statusFilter)
      .filter(task => priorityFilter === 'all' || task.priority === priorityFilter)
      .filter(task => !activeOnly || task.source_session_id === activeSessionId)
      .sort((left, right) => {
        if (left.status !== right.status) return 0
        const priorityDelta = sortWeight(left.priority) - sortWeight(right.priority)
        if (priorityDelta !== 0) return priorityDelta
        return (right.updated_at || '').localeCompare(left.updated_at || '')
      })
  }, [activeOnly, activeSessionId, assigneeFilter, priorityFilter, statusFilter, tasks])

  const grouped = useMemo(() => {
    return GROUPS.map(group => ({
      ...group,
      tasks: filteredTasks.filter(task => task.status === group.key),
    }))
  }, [filteredTasks])

  const editingTask = editingTaskId ? tasks.find(task => task.task_id === editingTaskId) ?? null : null

  async function saveEdit() {
    if (!editingTaskId) return
    await onTaskPatch(editingTaskId, editingDraft)
    setEditingTaskId(null)
    setEditingDraft({})
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', minHeight: 0 }}>
      <div style={{ padding: '18px 18px 14px', borderBottom: '1px solid rgba(26,24,21,0.08)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <div>
            <div style={{ fontSize: 10, letterSpacing: '0.16em', textTransform: 'uppercase', color: 'rgba(26,24,21,0.42)' }}>Backlog</div>
            <div style={{ marginTop: 4, fontSize: 22, fontWeight: 700, color: 'var(--kajal)' }}>Durable project tasks</div>
          </div>
          <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <TaskComposer activeSessionId={activeSessionId} onCreate={onTaskCreate} />
          </div>
        </div>

        <div style={{ display: 'grid', gap: 8, marginTop: 14, gridTemplateColumns: 'repeat(4, minmax(0, 1fr))' }}>
          <select value={assigneeFilter} onChange={event => setAssigneeFilter(event.target.value)} style={filterStyle}>
            <option value="all">All assignees</option>
            {assignees.map(assignee => <option key={assignee} value={assignee}>{assignee}</option>)}
          </select>
          <select value={statusFilter} onChange={event => setStatusFilter(event.target.value)} style={filterStyle}>
            <option value="all">All statuses</option>
            {GROUPS.map(group => <option key={group.key} value={group.key}>{group.label}</option>)}
          </select>
          <select value={priorityFilter} onChange={event => setPriorityFilter(event.target.value)} style={filterStyle}>
            <option value="all">All priorities</option>
            <option value="high">High priority</option>
            <option value="medium">Medium priority</option>
            <option value="low">Low priority</option>
          </select>
          <button
            type="button"
            onClick={() => setActiveOnly(value => !value)}
            style={{
              ...filterStyle,
              cursor: 'pointer',
              background: activeOnly ? 'rgba(194,65,12,0.1)' : 'rgba(252,250,242,0.94)',
              color: activeOnly ? 'var(--marigold)' : 'rgba(26,24,21,0.72)',
            }}
          >
            {activeOnly ? 'Active session only' : 'All session tasks'}
          </button>
        </div>
      </div>

      <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: 18, display: 'grid', gap: 14 }}>
        {grouped.map(group => (
          <section
            key={group.key}
            style={{
              padding: 14,
              borderRadius: 18,
              border: '1px solid rgba(26,24,21,0.08)',
              background: 'rgba(252,250,242,0.84)',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <span style={{ width: 10, height: 10, borderRadius: 999, background: group.accent, display: 'inline-block' }} />
              <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--kajal)' }}>{group.label}</div>
              <div style={{ marginLeft: 'auto', fontSize: 11, color: 'rgba(26,24,21,0.42)' }}>{group.tasks.length}</div>
            </div>

            <div style={{ display: 'grid', gap: 10, marginTop: 12 }}>
              {group.tasks.length === 0 ? (
                <div style={{ padding: 14, borderRadius: 14, border: '1px dashed rgba(26,24,21,0.12)', color: 'rgba(26,24,21,0.38)', fontSize: 12 }}>
                  No tasks in this group.
                </div>
              ) : (
                group.tasks.map(task => {
                  const editing = task.task_id === editingTaskId
                  return (
                    <article
                      key={task.task_id}
                      style={{
                        padding: '13px 14px',
                        borderRadius: 16,
                        border: '1px solid rgba(26,24,21,0.08)',
                        background: 'rgba(252,250,242,0.95)',
                        boxShadow: `inset 3px 0 0 ${group.accent}`,
                      }}
                    >
                      {editing ? (
                        <div style={{ display: 'grid', gap: 8 }}>
                          <input
                            value={String(editingDraft.title ?? editingTask?.title ?? '')}
                            onChange={event => setEditingDraft(draft => ({ ...draft, title: event.target.value }))}
                            style={inputStyle}
                          />
                          <textarea
                            value={String(editingDraft.description ?? editingTask?.description ?? '')}
                            onChange={event => setEditingDraft(draft => ({ ...draft, description: event.target.value }))}
                            rows={3}
                            style={{ ...inputStyle, resize: 'vertical', minHeight: 84 }}
                          />
                          <div style={{ display: 'grid', gap: 8, gridTemplateColumns: 'repeat(3, minmax(0, 1fr))' }}>
                            <input
                              value={String(editingDraft.owner ?? editingTask?.owner ?? '')}
                              onChange={event => setEditingDraft(draft => ({ ...draft, owner: event.target.value || null }))}
                              placeholder="Owner"
                              style={inputStyle}
                            />
                            <select
                              value={String(editingDraft.priority ?? editingTask?.priority ?? 'medium')}
                              onChange={event => setEditingDraft(draft => ({ ...draft, priority: event.target.value }))}
                              style={inputStyle}
                            >
                              <option value="high">High</option>
                              <option value="medium">Medium</option>
                              <option value="low">Low</option>
                            </select>
                            <select
                              value={String(editingDraft.status ?? editingTask?.status ?? 'todo')}
                              onChange={event => setEditingDraft(draft => ({ ...draft, status: event.target.value }))}
                              style={inputStyle}
                            >
                              {GROUPS.map(option => <option key={option.key} value={option.key}>{option.label}</option>)}
                            </select>
                          </div>
                          <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
                            <button type="button" onClick={() => { setEditingTaskId(null); setEditingDraft({}) }} style={secondaryButtonStyle}>Cancel</button>
                            <button type="button" onClick={saveEdit} style={primaryButtonStyle}>Save</button>
                          </div>
                        </div>
                      ) : (
                        <>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                            <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--kajal)' }}>{truncate(task.title, 170)}</div>
                            <div style={{ marginLeft: 'auto', display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                              {task.owner && <span style={tagStyle}>{task.owner}</span>}
                              <span style={tagStyle}>{task.priority}</span>
                              <span style={tagStyle}>{task.kind.replace(/_/g, ' ')}</span>
                            </div>
                          </div>
                          {task.description && (
                            <div style={{ marginTop: 8, fontSize: 12, lineHeight: 1.55, color: 'rgba(26,24,21,0.56)' }}>
                              {truncate(task.description, 220)}
                            </div>
                          )}
                          <div style={{ display: 'flex', gap: 8, marginTop: 10, flexWrap: 'wrap' }}>
                            {task.status !== 'done' && <button type="button" onClick={() => onTaskStatusChange(task.task_id, 'complete')} style={secondaryButtonStyle}>Complete</button>}
                            {task.status !== 'blocked' && task.status !== 'done' && <button type="button" onClick={() => onTaskStatusChange(task.task_id, 'block')} style={secondaryButtonStyle}>Block</button>}
                            {(task.status === 'todo' || task.status === 'blocked') && <button type="button" onClick={() => onTaskStatusChange(task.task_id, 'resume')} style={secondaryButtonStyle}>Resume</button>}
                            <button
                              type="button"
                              onClick={() => {
                                setEditingTaskId(task.task_id)
                                setEditingDraft({
                                  title: task.title,
                                  description: task.description,
                                  owner: task.owner,
                                  priority: task.priority,
                                  status: task.status,
                                })
                              }}
                              style={secondaryButtonStyle}
                            >
                              Edit
                            </button>
                            {task.source_session_id && (
                              <span style={{ marginLeft: 'auto', fontSize: 11, color: 'rgba(26,24,21,0.38)' }}>
                                {task.source_session_id.slice(0, 8)}
                              </span>
                            )}
                          </div>
                        </>
                      )}
                    </article>
                  )
                })
              )}
            </div>
          </section>
        ))}
      </div>
    </div>
  )
}

const filterStyle: CSSProperties = {
  width: '100%',
  padding: '9px 11px',
  borderRadius: 12,
  border: '1px solid rgba(26,24,21,0.1)',
  background: 'rgba(252,250,242,0.94)',
  color: 'rgba(26,24,21,0.72)',
  fontSize: 12,
}

const inputStyle: CSSProperties = {
  width: '100%',
  padding: '9px 11px',
  borderRadius: 12,
  border: '1px solid rgba(26,24,21,0.1)',
  background: 'rgba(252,250,242,0.94)',
  color: 'var(--kajal)',
  fontSize: 12,
}

const tagStyle: CSSProperties = {
  padding: '4px 7px',
  borderRadius: 999,
  background: 'rgba(243,239,225,0.92)',
  border: '1px solid rgba(26,24,21,0.08)',
  fontSize: 10,
  color: 'rgba(26,24,21,0.52)',
}

const secondaryButtonStyle: CSSProperties = {
  padding: '6px 10px',
  borderRadius: 999,
  border: '1px solid rgba(26,24,21,0.08)',
  background: 'rgba(243,239,225,0.92)',
  fontSize: 11,
  fontWeight: 600,
  color: 'var(--kajal)',
  cursor: 'pointer',
}

const primaryButtonStyle: CSSProperties = {
  ...secondaryButtonStyle,
  background: 'rgba(194,65,12,0.1)',
  border: '1px solid rgba(194,65,12,0.14)',
  color: 'var(--marigold)',
}
