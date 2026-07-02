import { useState, type CSSProperties, type FormEvent } from 'react'

interface TaskDraft {
  title: string
  description: string
  owner: string | null
  priority: string
  kind: string
}

interface Props {
  disabled?: boolean
  activeSessionId?: string | null
  onCreate: (draft: TaskDraft) => Promise<void>
}

const INPUT_STYLE: CSSProperties = {
  width: '100%',
  borderRadius: 12,
  border: '1px solid rgba(26,24,21,0.12)',
  background: 'rgba(252,250,242,0.94)',
  color: 'var(--kajal)',
  fontSize: 13,
  padding: '10px 12px',
  outline: 'none',
}

export function TaskComposer({ disabled, activeSessionId, onCreate }: Props) {
  const [open, setOpen] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [owner, setOwner] = useState('')
  const [priority, setPriority] = useState('medium')
  const [kind, setKind] = useState('follow_up')

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!title.trim() || submitting) return
    setSubmitting(true)
    try {
      await onCreate({
        title: title.trim(),
        description: description.trim(),
        owner: owner.trim() || null,
        priority,
        kind,
      })
      setTitle('')
      setDescription('')
      setOwner('')
      setPriority('medium')
      setKind('follow_up')
      setOpen(false)
    } finally {
      setSubmitting(false)
    }
  }

  if (!open) {
    return (
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen(true)}
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 8,
          padding: '9px 12px',
          borderRadius: 999,
          border: '1px solid rgba(26,24,21,0.1)',
          background: 'rgba(252,250,242,0.92)',
          color: 'var(--kajal)',
          fontSize: 12,
          fontWeight: 700,
          cursor: disabled ? 'default' : 'pointer',
          opacity: disabled ? 0.5 : 1,
        }}
      >
        + Add task
      </button>
    )
  }

  return (
    <form
      onSubmit={handleSubmit}
      style={{
        padding: 14,
        borderRadius: 18,
        border: '1px solid rgba(26,24,21,0.08)',
        background: 'rgba(252,250,242,0.92)',
        display: 'grid',
        gap: 10,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--kajal)' }}>Create task</div>
        <div style={{ marginLeft: 'auto', fontSize: 11, color: 'rgba(26,24,21,0.42)' }}>
          {activeSessionId ? `linked to ${activeSessionId.slice(0, 8)}` : 'manual task'}
        </div>
      </div>

      <input
        value={title}
        onChange={event => setTitle(event.target.value)}
        placeholder="Task title"
        style={INPUT_STYLE}
      />

      <textarea
        value={description}
        onChange={event => setDescription(event.target.value)}
        placeholder="What should happen or what output is expected?"
        rows={3}
        style={{ ...INPUT_STYLE, resize: 'vertical', minHeight: 84 }}
      />

      <div style={{ display: 'grid', gap: 10, gridTemplateColumns: 'repeat(3, minmax(0, 1fr))' }}>
        <input
          value={owner}
          onChange={event => setOwner(event.target.value)}
          placeholder="Owner"
          style={INPUT_STYLE}
        />
        <select value={priority} onChange={event => setPriority(event.target.value)} style={INPUT_STYLE}>
          <option value="high">High</option>
          <option value="medium">Medium</option>
          <option value="low">Low</option>
        </select>
        <select value={kind} onChange={event => setKind(event.target.value)} style={INPUT_STYLE}>
          <option value="follow_up">Follow-up</option>
          <option value="goal">Goal</option>
          <option value="plan_step">Plan step</option>
          <option value="bug">Bug</option>
          <option value="research">Research</option>
          <option value="implementation">Implementation</option>
          <option value="validation">Validation</option>
        </select>
      </div>

      <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
        <button
          type="button"
          onClick={() => setOpen(false)}
          style={{
            padding: '8px 12px',
            borderRadius: 12,
            border: '1px solid rgba(26,24,21,0.08)',
            background: 'rgba(243,239,225,0.84)',
            color: 'rgba(26,24,21,0.62)',
            cursor: 'pointer',
          }}
        >
          Cancel
        </button>
        <button
          type="submit"
          disabled={submitting || !title.trim()}
          style={{
            padding: '8px 12px',
            borderRadius: 12,
            border: '1px solid rgba(194,65,12,0.14)',
            background: 'rgba(194,65,12,0.1)',
            color: 'var(--marigold)',
            fontWeight: 700,
            cursor: submitting || !title.trim() ? 'default' : 'pointer',
            opacity: submitting || !title.trim() ? 0.55 : 1,
          }}
        >
          {submitting ? 'Creating…' : 'Create'}
        </button>
      </div>
    </form>
  )
}
