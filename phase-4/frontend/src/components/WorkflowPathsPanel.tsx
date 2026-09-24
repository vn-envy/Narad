import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  ArrowLeft,
  ArrowRight,
  BriefcaseBusiness,
  CalendarClock,
  Check,
  CirclePause,
  CirclePlay,
  FileChartColumn,
  GraduationCap,
  HeartPulse,
  LoaderCircle,
  Plane,
  RefreshCw,
  ShieldCheck,
  WalletCards,
  X,
} from 'lucide-react'
import { apiFetch, apiPath, apiUrl, type WorkflowDefinition, type WorkflowRun, type WorkflowStage } from '@/lib/api'
import { OPEN_URL_EVENT } from '@/lib/pwa'
import { useIsMobile } from '@/hooks/useIsMobile'

interface Props {
  userId: string
  streaming: boolean
  activeRunId?: string | null
  onContinue: (run: WorkflowRun, prompt: string) => void
}

type PackIcon = typeof BriefcaseBusiness

// Workflows v2 fields on a run: every stage's finish line (done_when) in plain
// words, what proves the current one, and a concrete "what's next".
interface FinishCondition {
  kind?: string
  text: string
  met?: boolean
  detail?: string
}

interface EvidenceRef {
  kind: string
  id: string
  label?: string
  url?: string
  status?: string
  type?: string
}

type PathStage = WorkflowStage & {
  optional?: boolean
  done_when_text?: FinishCondition[]
  check?: { met: boolean; conditions: FinishCondition[]; missing: string[] }
  output?: (NonNullable<WorkflowStage['output']> & { evidence?: EvidenceRef[]; done_when?: FinishCondition[] }) | null
}

interface PathNextAction {
  kind: string
  label: string
  prompt: string
  url?: string
  detail?: string
  questions?: string[]
  can_confirm?: boolean
  can_skip?: boolean
}

type PathRun = Omit<WorkflowRun, 'stages' | 'current_stage' | 'next_action'> & {
  stages: PathStage[]
  current_stage?: PathStage | null
  next_action: PathNextAction
  evidence?: EvidenceRef[]
  intake_questions?: Array<{ key: string; label: string; question: string }>
  applications?: Array<{ record_id: string; company: string; role: string; status: string; next_step?: string; link?: string; reply_seen?: { subject?: string } }>
  findings?: Array<{ record_id: string; kind: string; status: string; at?: string; answer?: string; detail?: string; task_id?: string; replies?: Array<{ company: string; subject: string }> }>
  versions?: Array<{ version: number; label?: string; stage_title?: string; url?: string }>
}

type IntakeField = WorkflowDefinition['intake'][number] & { ask?: string }

/** Required details two at a time, then optional ones, then the rhythm settings. */
function intakeSteps(definition: WorkflowDefinition): IntakeField[][] {
  const rhythmField = (field: IntakeField) => field.kind === 'boolean' || field.kind === 'time' || field.key === 'timezone'
  const pairs = (fields: IntakeField[]) => fields.reduce<IntakeField[][]>((groups, field, index) => {
    if (index % 2 === 0) groups.push([field])
    else groups[groups.length - 1].push(field)
    return groups
  }, [])
  const fields = definition.intake as IntakeField[]
  const rhythm = fields.filter(field => !field.required && rhythmField(field))
  return [
    ...pairs(fields.filter(field => field.required)),
    ...pairs(fields.filter(field => !field.required && !rhythmField(field))),
    ...(rhythm.length ? [rhythm] : []),
  ]
}

/** Evidence links: files open in a new tab; reviews, tasks and approvals open in the app. */
function openEvidence(url?: string) {
  if (!url) return
  if (url.startsWith('/media/')) {
    window.open(apiPath(url), '_blank', 'noopener,noreferrer')
    return
  }
  window.dispatchEvent(new CustomEvent(OPEN_URL_EVENT, { detail: { url } }))
}

const EVIDENCE_WORDS: Record<string, string> = {
  review: 'Review',
  task: 'Web task',
  approval: 'Approval',
  artifact: 'File',
  confirmation: 'You confirmed',
  tool: 'Tool',
}

const PACK_ICONS: Record<string, PackIcon> = {
  career: BriefcaseBusiness,
  health: HeartPulse,
  travel: Plane,
  teach: GraduationCap,
  finance: WalletCards,
  documents: FileChartColumn,
}

const cardStyle = {
  border: '1px solid rgba(var(--rgb-ink),0.1)',
  background: 'rgba(var(--rgb-surface),0.58)',
  borderRadius: 12,
} as const

function readableDate(value?: string | null): string {
  if (!value) return 'Not scheduled'
  const date = new Date(value)
  if (Number.isNaN(date.valueOf())) return value
  return new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  }).format(date)
}

async function responseJson<T>(response: Response): Promise<T> {
  const data = await response.json().catch(() => ({})) as T & { detail?: string }
  if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`)
  return data
}

function defaultsFor(definition: WorkflowDefinition): Record<string, unknown> {
  return Object.fromEntries(
    definition.intake.map(field => [field.key, field.default ?? (field.kind === 'boolean' ? false : '')])
  )
}

function statusLabel(status: string): string {
  return status.replace(/_/g, ' ')
}

export function WorkflowPathsPanel({ userId, streaming, activeRunId, onContinue }: Props) {
  const [definitions, setDefinitions] = useState<WorkflowDefinition[]>([])
  const [runs, setRuns] = useState<WorkflowRun[]>([])
  const [selectedRun, setSelectedRun] = useState<WorkflowRun | null>(null)
  const [intakeDefinition, setIntakeDefinition] = useState<WorkflowDefinition | null>(null)
  const [intake, setIntake] = useState<Record<string, unknown>>({})
  const [intakeStep, setIntakeStep] = useState(0)
  const [answers, setAnswers] = useState<Record<string, string>>({})
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const isMobile = useIsMobile()

  const loadDefinitions = useCallback(async () => {
    const response = await apiFetch('/workflows')
    const data = await responseJson<{ workflows: WorkflowDefinition[] }>(response)
    setDefinitions(data.workflows)
    return data.workflows
  }, [])

  const loadRun = useCallback(async (runId: string) => {
    const response = await apiFetch(apiUrl(`/workflow-runs/${runId}`, { user_id: userId }))
    const data = await responseJson<WorkflowRun>(response)
    setSelectedRun(data)
    return data
  }, [userId])

  const loadRuns = useCallback(async (preferredId?: string | null) => {
    const response = await apiFetch(apiUrl('/workflow-runs', { user_id: userId, limit: 50 }))
    const data = await responseJson<{ runs: WorkflowRun[] }>(response)
    setRuns(data.runs)
    const chosen = preferredId || selectedRun?.run_id || activeRunId || data.runs[0]?.run_id
    if (chosen) await loadRun(chosen)
    return data.runs
  }, [activeRunId, loadRun, selectedRun?.run_id, userId])

  const refresh = useCallback(async (preferredId?: string | null, quiet = false) => {
    if (!quiet) setLoading(true)
    try {
      setError(null)
      if (definitions.length === 0) await loadDefinitions()
      await loadRuns(preferredId)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Workflow state could not be refreshed.')
    } finally {
      if (!quiet) setLoading(false)
    }
  }, [definitions.length, loadDefinitions, loadRuns])

  useEffect(() => {
    void refresh(activeRunId)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (activeRunId && activeRunId !== selectedRun?.run_id) void loadRun(activeRunId).catch(() => {})
  }, [activeRunId, loadRun, selectedRun?.run_id])

  // Intake answers typed on the Paths screen belong to one run.
  useEffect(() => { setAnswers({}) }, [selectedRun?.run_id])

  useEffect(() => {
    const handleRuntimeEvent = (event: Event) => {
      const detail = (event as CustomEvent<{ data?: { workflow_run_id?: string } }>).detail
      const runId = detail?.data?.workflow_run_id
      void refresh(runId || selectedRun?.run_id, true)
    }
    const handleFocus = () => void refresh(selectedRun?.run_id, true)
    window.addEventListener('narad:workflow-event', handleRuntimeEvent)
    window.addEventListener('focus', handleFocus)
    const timer = window.setInterval(() => void refresh(selectedRun?.run_id, true), streaming ? 5000 : 15000)
    return () => {
      window.removeEventListener('narad:workflow-event', handleRuntimeEvent)
      window.removeEventListener('focus', handleFocus)
      window.clearInterval(timer)
    }
  }, [refresh, selectedRun?.run_id, streaming])

  const definitionById = useMemo(
    () => Object.fromEntries(definitions.map(item => [item.id, item])),
    [definitions]
  )

  const startIntake = (definition: WorkflowDefinition) => {
    setIntakeDefinition(definition)
    setIntake(defaultsFor(definition))
    setIntakeStep(0)
    setSelectedRun(null)
    setError(null)
    // Prefilled from the phone's time zone and this person's last run of the path.
    apiFetch(apiUrl(`/workflows/${definition.id}/intake`, { user_id: userId }))
      .then(response => response.ok ? response.json() as Promise<{ values?: Record<string, unknown> }> : null)
      .then(data => { if (data?.values) setIntake(current => ({ ...current, ...data.values })) })
      .catch(() => {})
  }

  const startRun = async () => {
    if (!intakeDefinition) return
    setBusy(true)
    setError(null)
    try {
      const response = await apiFetch(apiUrl(`/workflows/${intakeDefinition.id}/runs`, { user_id: userId }), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ inputs: intake }),
      })
      const data = await responseJson<{ run: WorkflowRun }>(response)
      setIntakeDefinition(null)
      setSelectedRun(data.run)
      await loadRuns(data.run.run_id)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'The path could not be started.')
    } finally {
      setBusy(false)
    }
  }

  const mutateRun = async (action: string, payload: Record<string, unknown> = {}) => {
    if (!selectedRun) return
    setBusy(true)
    setError(null)
    try {
      const response = await apiFetch(apiUrl(`/workflow-runs/${selectedRun.run_id}/actions`, { user_id: userId }), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action, payload }),
      })
      const data = await responseJson<{ run: WorkflowRun }>(response)
      setSelectedRun(data.run)
      await loadRuns(data.run.run_id)
      window.dispatchEvent(new CustomEvent('narad:workflow-event', {
        detail: { type: 'workflow_updated', data: { workflow_run_id: data.run.run_id }, ts: Date.now() },
      }))
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'The path could not be updated.')
    } finally {
      setBusy(false)
    }
  }

  const toggleSchedule = async (scheduleId: string, enabled: boolean) => {
    setBusy(true)
    setError(null)
    try {
      const response = await apiFetch(apiUrl(`/workflow-schedules/${scheduleId}`, { user_id: userId }), {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled }),
      })
      await responseJson(response)
      if (selectedRun) await loadRun(selectedRun.run_id)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'The schedule could not be updated.')
    } finally {
      setBusy(false)
    }
  }

  const renderField = (field: IntakeField) => {
    const value = intake[field.key]
    const update = (next: unknown) => setIntake(current => ({ ...current, [field.key]: next }))
    const inputStyle = {
      width: '100%',
      border: '1px solid rgba(var(--rgb-ink),0.14)',
      borderRadius: 8,
      background: 'rgba(var(--rgb-page),0.82)',
      color: 'var(--kajal)',
      fontSize: 12,
      padding: '9px 10px',
      outline: 'none',
    } as const
    if (field.kind === 'boolean') {
      return (
        <label key={field.key} style={{ ...cardStyle, display: 'flex', alignItems: 'center', gap: 9, padding: '10px 12px', cursor: 'pointer' }}>
          <input type="checkbox" checked={Boolean(value)} onChange={event => update(event.target.checked)} />
          <span style={{ fontSize: 12, fontWeight: 650 }}>{field.label}</span>
        </label>
      )
    }
    return (
      <label key={field.key} style={{ display: 'grid', gap: 5 }}>
        <span style={{ fontSize: 10.5, fontWeight: 750, color: 'rgba(var(--rgb-ink),0.62)' }}>
          {field.ask || field.label}{field.required ? ' *' : ''}
        </span>
        {field.kind === 'textarea' ? (
          <textarea rows={3} value={String(value ?? '')} placeholder={field.placeholder} onChange={event => update(event.target.value)} style={{ ...inputStyle, resize: 'vertical' }} />
        ) : field.kind === 'select' ? (
          <select value={String(value ?? '')} onChange={event => update(event.target.value)} style={inputStyle}>
            {field.options.map(option => <option key={option} value={option}>{option}</option>)}
          </select>
        ) : (
          <input
            type={field.kind === 'number' ? 'number' : field.kind === 'time' ? 'time' : 'text'}
            value={String(value ?? '')}
            placeholder={field.placeholder}
            onChange={event => update(field.kind === 'number' ? Number(event.target.value) : event.target.value)}
            style={inputStyle}
          />
        )}
        {field.help && <span style={{ fontSize: 10, color: 'rgba(var(--rgb-ink),0.45)' }}>{field.help}</span>}
      </label>
    )
  }

  const selectedDefinition = selectedRun ? definitionById[selectedRun.workflow_id] : null
  // The same run with its Workflows v2 fields typed (read only inside the selectedRun branch).
  const pathRun = selectedRun as unknown as PathRun
  const activeRuns = runs.filter(run => !['completed', 'cancelled'].includes(run.status))

  return (
    <div className="panel-scroll" style={{ height: '100%', minHeight: 0, overflow: 'auto', background: 'var(--paper)' }}>
      <div className="paths-body" style={{ minHeight: '100%', display: 'grid', gridTemplateColumns: isMobile ? '1fr' : 'minmax(245px, 0.72fr) minmax(0, 1.8fr)' }}>
        <aside style={{ padding: isMobile ? 14 : 18, borderRight: isMobile ? 0 : '1px solid rgba(var(--rgb-ink),0.09)', borderBottom: isMobile ? '1px solid rgba(var(--rgb-ink),0.09)' : 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
            <div>
              <div style={{ fontFamily: 'var(--font-hero)', fontSize: 18, fontWeight: 750 }}>Paths</div>
              <div style={{ marginTop: 2, fontSize: 11, color: 'rgba(var(--rgb-ink),0.5)' }}>Durable loops, not one-shot prompts.</div>
            </div>
            <button type="button" onClick={() => void refresh(selectedRun?.run_id)} title="Refresh paths" style={{ width: 30, height: 30, borderRadius: 8, border: '1px solid rgba(var(--rgb-ink),0.1)', background: 'transparent', cursor: 'pointer' }}>
              <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
            </button>
          </div>

          <div style={{ display: 'grid', gap: 7, marginTop: 16 }}>
            {definitions.map(definition => {
              const Icon = PACK_ICONS[definition.id] || FileChartColumn
              const selected = intakeDefinition?.id === definition.id
              return (
                <button
                  type="button"
                  key={definition.id}
                  onClick={() => startIntake(definition)}
                  disabled={definition.readiness.status === 'unavailable'}
                  style={{
                    ...cardStyle,
                    display: 'grid',
                    gridTemplateColumns: '34px minmax(0,1fr) auto',
                    alignItems: 'center',
                    gap: 9,
                    padding: '9px 10px',
                    textAlign: 'left',
                    cursor: definition.readiness.status === 'unavailable' ? 'not-allowed' : 'pointer',
                    opacity: definition.readiness.status === 'unavailable' ? 0.52 : 1,
                    boxShadow: selected ? `inset 3px 0 ${definition.accent}` : 'none',
                  }}
                >
                  <span style={{ width: 32, height: 32, display: 'grid', placeItems: 'center', borderRadius: 9, color: definition.accent, background: `${definition.accent}12` }}><Icon size={16} /></span>
                  <span style={{ minWidth: 0 }}>
                    <span style={{ display: 'block', color: 'var(--kajal)', fontSize: 12, fontWeight: 750 }}>{definition.title}</span>
                    <span style={{ display: 'block', marginTop: 1, color: 'rgba(var(--rgb-ink),0.48)', fontSize: 9.5, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{definition.eyebrow}</span>
                  </span>
                  <span title={definition.readiness.missing_optional.join(', ')} style={{ width: 7, height: 7, borderRadius: 99, background: definition.readiness.status === 'ready' ? 'var(--tulsi)' : definition.readiness.status === 'limited' ? 'var(--haldi)' : 'var(--sindoor)' }} />
                </button>
              )
            })}
          </div>

          <div style={{ marginTop: 19, paddingTop: 14, borderTop: '1px solid rgba(var(--rgb-ink),0.09)' }}>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, fontWeight: 750, textTransform: 'uppercase', letterSpacing: '0.12em', color: 'rgba(var(--rgb-ink),0.42)' }}>
              In progress · {activeRuns.length}
            </div>
            <div style={{ display: 'grid', gap: 6, marginTop: 8 }}>
              {activeRuns.length === 0 && <div style={{ fontSize: 11, lineHeight: 1.5, color: 'rgba(var(--rgb-ink),0.44)' }}>Start a path above. Narad will keep its stage, evidence, next actions, and review rhythm.</div>}
              {activeRuns.map(run => (
                <button
                  type="button"
                  key={run.run_id}
                  onClick={() => { setIntakeDefinition(null); void loadRun(run.run_id).catch(() => {}) }}
                  style={{
                    border: 0,
                    borderLeft: `2px solid ${run.definition?.accent || '#b45309'}`,
                    background: run.run_id === selectedRun?.run_id ? 'rgba(var(--rgb-ink),0.055)' : 'transparent',
                    borderRadius: '0 8px 8px 0',
                    padding: '7px 9px',
                    textAlign: 'left',
                    cursor: 'pointer',
                  }}
                >
                  <span style={{ display: 'block', fontSize: 11, fontWeight: 700, color: 'var(--kajal)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{run.title}</span>
                  <span style={{ display: 'block', marginTop: 2, fontSize: 9.5, color: 'rgba(var(--rgb-ink),0.47)' }}>{run.progress_percent}% · {statusLabel(run.status)}</span>
                </button>
              ))}
            </div>
          </div>
        </aside>

        <section style={{ minWidth: 0, padding: isMobile ? 14 : '20px 24px 30px' }}>
          {error && (
            <div style={{ ...cardStyle, display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, marginBottom: 12, padding: '9px 11px', borderColor: 'rgba(194,65,12,0.22)', color: 'var(--sindoor)', fontSize: 11 }}>
              <span>{error}</span>
              <button type="button" onClick={() => setError(null)} style={{ border: 0, background: 'transparent', color: 'inherit', cursor: 'pointer' }}><X size={13} /></button>
            </div>
          )}

          {loading && definitions.length === 0 ? (
            <div style={{ height: '100%', display: 'grid', placeItems: 'center', color: 'rgba(var(--rgb-ink),0.45)' }}><LoaderCircle size={22} className="animate-spin" /></div>
          ) : intakeDefinition ? (
            <div style={{ maxWidth: 780, margin: '0 auto' }}>
              <button type="button" onClick={() => setIntakeDefinition(null)} style={{ border: 0, background: 'transparent', display: 'flex', gap: 5, alignItems: 'center', padding: 0, color: 'rgba(var(--rgb-ink),0.55)', fontSize: 11, cursor: 'pointer' }}><ArrowLeft size={13} /> Back</button>
              <div style={{ marginTop: 14, display: 'flex', gap: 12, alignItems: 'flex-start' }}>
                <div style={{ width: 42, height: 42, flex: '0 0 auto', display: 'grid', placeItems: 'center', borderRadius: 12, background: `${intakeDefinition.accent}12`, color: intakeDefinition.accent }}>
                  {(() => { const Icon = PACK_ICONS[intakeDefinition.id] || FileChartColumn; return <Icon size={20} /> })()}
                </div>
                <div>
                  <div style={{ fontSize: 10, color: intakeDefinition.accent, fontWeight: 800, textTransform: 'uppercase', letterSpacing: '0.12em' }}>Start a durable path</div>
                  <h2 style={{ margin: '3px 0 0', fontFamily: 'var(--font-hero)', fontSize: 25, color: 'var(--kajal)' }}>{intakeDefinition.title}</h2>
                  <p style={{ margin: '6px 0 0', maxWidth: 620, color: 'rgba(var(--rgb-ink),0.57)', fontSize: 12, lineHeight: 1.5 }}>{intakeDefinition.description}</p>
                </div>
              </div>
              {intakeDefinition.readiness.status === 'limited' && (
                <div style={{ ...cardStyle, marginTop: 14, padding: '9px 11px', fontSize: 10.5, color: 'rgba(var(--rgb-ink),0.58)', background: 'rgba(234,179,8,0.06)' }}>
                  Starts in limited mode. Optional connections not ready: {intakeDefinition.readiness.missing_optional.join(', ')}. The path will preserve state and clearly flag unavailable actions.
                </div>
              )}
              {(() => {
                // One or two questions at a time; files come later as chat attachments.
                const steps = intakeSteps(intakeDefinition)
                const step = Math.min(intakeStep, Math.max(steps.length - 1, 0))
                const fields = steps[step] ?? []
                const last = step >= steps.length - 1
                const blocked = fields.some(field => field.required && (intake[field.key] === undefined || intake[field.key] === ''))
                return (
                  <>
                    <div style={{ marginTop: 20, fontFamily: 'var(--font-mono)', fontSize: 9.5, fontWeight: 750, textTransform: 'uppercase', letterSpacing: '0.12em', color: 'rgba(var(--rgb-ink),0.42)' }}>
                      Step {step + 1} of {steps.length}{fields.every(field => !field.required) ? ' · optional' : ''}
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: isMobile ? '1fr' : 'repeat(2, minmax(0,1fr))', gap: 12, marginTop: 10 }}>
                      {fields.map(renderField)}
                    </div>
                    <div style={{ marginTop: 18, display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center' }}>
                      {step > 0 ? (
                        <button type="button" onClick={() => setIntakeStep(step - 1)} style={{ border: 0, background: 'transparent', display: 'flex', gap: 5, alignItems: 'center', padding: 0, color: 'rgba(var(--rgb-ink),0.55)', fontSize: 11, cursor: 'pointer' }}><ArrowLeft size={13} /> Back</button>
                      ) : (
                        <div style={{ display: 'flex', gap: 6, alignItems: 'center', color: 'rgba(var(--rgb-ink),0.45)', fontSize: 10.5 }}><ShieldCheck size={13} /> External actions always pause for approval.</div>
                      )}
                      {last ? (
                        <button type="button" disabled={busy || blocked} onClick={() => void startRun()} style={{ border: 0, borderRadius: 9, background: intakeDefinition.accent, color: '#fff', display: 'flex', gap: 7, alignItems: 'center', padding: '10px 14px', fontSize: 11.5, fontWeight: 750, cursor: busy ? 'wait' : 'pointer', opacity: blocked ? 0.5 : 1 }}>
                          {busy ? <LoaderCircle size={14} className="animate-spin" /> : <ArrowRight size={14} />} Start path
                        </button>
                      ) : (
                        <button type="button" disabled={blocked} onClick={() => setIntakeStep(step + 1)} style={{ border: 0, borderRadius: 9, background: 'var(--kajal)', color: 'var(--paper)', display: 'flex', gap: 7, alignItems: 'center', padding: '10px 14px', fontSize: 11.5, fontWeight: 750, cursor: 'pointer', opacity: blocked ? 0.5 : 1 }}>
                          Next <ArrowRight size={14} />
                        </button>
                      )}
                    </div>
                  </>
                )
              })()}
            </div>
          ) : selectedRun ? (
            <div style={{ maxWidth: 980, margin: '0 auto' }}>
              <div style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'space-between', gap: 14, alignItems: 'flex-start' }}>
                <div style={{ minWidth: 0 }}>
                  <div style={{ color: selectedRun.definition.accent, fontSize: 10, fontWeight: 800, textTransform: 'uppercase', letterSpacing: '0.12em' }}>{selectedRun.definition.title} · cycle {selectedRun.state.cycle || 1}</div>
                  <h2 style={{ margin: '4px 0 0', fontFamily: 'var(--font-hero)', fontSize: 24, lineHeight: 1.1, color: 'var(--kajal)' }}>{selectedRun.title}</h2>
                  <div style={{ marginTop: 6, fontSize: 11, color: 'rgba(var(--rgb-ink),0.48)' }}>Updated {readableDate(selectedRun.updated_at)} · {statusLabel(selectedRun.status)}</div>
                </div>
                <div style={{ display: 'flex', gap: 7 }}>
                  {selectedRun.status === 'paused' ? (
                    <button type="button" disabled={busy} onClick={() => void mutateRun('resume')} style={{ ...cardStyle, display: 'flex', gap: 6, alignItems: 'center', padding: '7px 10px', fontSize: 10.5, cursor: 'pointer' }}><CirclePlay size={13} /> Resume</button>
                  ) : !['completed', 'cancelled'].includes(selectedRun.status) ? (
                    <button type="button" disabled={busy} onClick={() => void mutateRun('pause')} style={{ ...cardStyle, display: 'flex', gap: 6, alignItems: 'center', padding: '7px 10px', fontSize: 10.5, cursor: 'pointer' }}><CirclePause size={13} /> Pause</button>
                  ) : null}
                  {!['completed', 'cancelled'].includes(selectedRun.status) && (
                    <button type="button" disabled={busy} onClick={() => void mutateRun('cancel')} title="Cancel path" style={{ ...cardStyle, width: 31, display: 'grid', placeItems: 'center', color: 'rgba(var(--rgb-ink),0.45)', cursor: 'pointer' }}><X size={13} /></button>
                  )}
                </div>
              </div>

              <div style={{ marginTop: 15, height: 6, borderRadius: 99, overflow: 'hidden', background: 'rgba(var(--rgb-ink),0.08)' }}>
                <div style={{ width: `${selectedRun.progress_percent}%`, height: '100%', borderRadius: 99, background: selectedRun.definition.accent, transition: 'width 300ms ease' }} />
              </div>

              {selectedRun.current_stage && (
                <div style={{ ...cardStyle, marginTop: 16, padding: isMobile ? 14 : 18, background: `linear-gradient(135deg, ${selectedRun.definition.accent}0d, rgba(var(--rgb-surface),0.65))` }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'flex-start', flexWrap: 'wrap' }}>
                    <div style={{ maxWidth: 650 }}>
                      <div style={{ fontSize: 9.5, fontWeight: 800, color: selectedRun.definition.accent, textTransform: 'uppercase', letterSpacing: '0.13em' }}>Now · {selectedRun.current_stage.owner}</div>
                      <div style={{ marginTop: 4, fontFamily: 'var(--font-hero)', fontSize: 20, color: 'var(--kajal)' }}>{selectedRun.current_stage.title}</div>
                      <p style={{ margin: '6px 0 0', fontSize: 12, lineHeight: 1.5, color: 'rgba(var(--rgb-ink),0.6)' }}>{selectedRun.current_stage.purpose}</p>
                    </div>
                    {['chat', 'answer', 'blocked'].includes(pathRun.next_action.kind) && (
                      <button type="button" disabled={busy || streaming || selectedRun.status === 'paused'} onClick={() => onContinue(selectedRun, selectedRun.next_action.prompt)} style={{ border: 0, borderRadius: 9, padding: '10px 13px', display: 'flex', alignItems: 'center', gap: 7, background: 'var(--kajal)', color: 'var(--paper)', fontSize: 11.5, fontWeight: 750, cursor: busy || streaming ? 'wait' : 'pointer' }}>
                        Continue in Chat <ArrowRight size={14} />
                      </button>
                    )}
                    {['review', 'wait'].includes(pathRun.next_action.kind) && pathRun.next_action.url && (
                      <button type="button" onClick={() => openEvidence(pathRun.next_action.url)} style={{ border: 0, borderRadius: 9, padding: '10px 13px', display: 'flex', alignItems: 'center', gap: 7, background: 'var(--kajal)', color: 'var(--paper)', fontSize: 11.5, fontWeight: 750, cursor: 'pointer' }}>
                        {pathRun.next_action.kind === 'review' ? 'Confirm the values' : 'Watch the task'} <ArrowRight size={14} />
                      </button>
                    )}
                    {pathRun.next_action.kind === 'export' && (
                      <button type="button" disabled={busy} onClick={() => void mutateRun('export')} style={{ border: 0, borderRadius: 9, padding: '10px 13px', display: 'flex', alignItems: 'center', gap: 7, background: 'var(--kajal)', color: 'var(--paper)', fontSize: 11.5, fontWeight: 750, cursor: busy ? 'wait' : 'pointer' }}>
                        Export <ArrowRight size={14} />
                      </button>
                    )}
                    {selectedRun.next_action.kind === 'approve' && (
                      <button type="button" disabled={busy} onClick={() => void mutateRun('approve')} style={{ border: 0, borderRadius: 9, padding: '10px 13px', display: 'flex', alignItems: 'center', gap: 7, background: 'var(--tulsi)', color: '#fff', fontSize: 11.5, fontWeight: 750, cursor: busy ? 'wait' : 'pointer' }}>
                        Review and approve <ShieldCheck size={14} />
                      </button>
                    )}
                  </div>
                  {selectedRun.state.confirmation?.status === 'pending' && (
                    <div style={{ marginTop: 13, paddingTop: 12, borderTop: '1px solid rgba(var(--rgb-ink),0.1)' }}>
                      <div style={{ fontSize: 10, fontWeight: 750, color: 'rgba(var(--rgb-ink),0.55)' }}>Exact action preview</div>
                      <div style={{ marginTop: 4, whiteSpace: 'pre-wrap', fontSize: 11.5, lineHeight: 1.5, color: 'rgba(var(--rgb-ink),0.65)' }}>{selectedRun.state.confirmation.summary}</div>
                    </div>
                  )}

                  {/* What's next, in plain words, with the person's own controls. */}
                  <div style={{ marginTop: 13, paddingTop: 12, borderTop: '1px solid rgba(var(--rgb-ink),0.1)' }}>
                    <div style={{ fontSize: 10, fontWeight: 750, color: 'rgba(var(--rgb-ink),0.55)' }}>What's next</div>
                    <div style={{ marginTop: 4, fontSize: 12, fontWeight: 700, color: 'var(--kajal)' }}>{pathRun.next_action.label}</div>
                    {pathRun.next_action.detail && <div style={{ marginTop: 3, fontSize: 11.5, lineHeight: 1.45, color: 'rgba(var(--rgb-ink),0.6)' }}>{pathRun.next_action.detail}</div>}
                    {pathRun.current_stage?.id === 'intake' && (pathRun.intake_questions ?? []).length > 0 ? (
                      <div style={{ display: 'grid', gap: 8, marginTop: 8 }}>
                        {(pathRun.intake_questions ?? []).map(item => (
                          <label key={item.key} style={{ display: 'grid', gap: 4 }}>
                            <span style={{ fontSize: 10.5, fontWeight: 750, color: 'rgba(var(--rgb-ink),0.62)' }}>{item.question}</span>
                            <input value={answers[item.key] ?? ''} onChange={event => setAnswers(current => ({ ...current, [item.key]: event.target.value }))} style={{ width: '100%', border: '1px solid rgba(var(--rgb-ink),0.14)', borderRadius: 8, background: 'rgba(252,250,242,0.82)', color: 'var(--kajal)', fontSize: 12, padding: '9px 10px', outline: 'none' }} />
                          </label>
                        ))}
                        <button type="button" disabled={busy || !Object.values(answers).some(value => value.trim())} onClick={() => { void mutateRun('update_inputs', answers); setAnswers({}) }} style={{ ...cardStyle, justifySelf: 'start', display: 'flex', gap: 6, alignItems: 'center', padding: '7px 10px', fontSize: 10.5, cursor: 'pointer' }}>
                          <Check size={13} /> Save answers
                        </button>
                      </div>
                    ) : (pathRun.next_action.questions ?? []).length > 0 && (
                      <ul style={{ margin: '5px 0 0', paddingLeft: 16, fontSize: 11.5, lineHeight: 1.5, color: 'rgba(var(--rgb-ink),0.65)' }}>
                        {(pathRun.next_action.questions ?? []).map(question => <li key={question}>{question}</li>)}
                      </ul>
                    )}
                    {(pathRun.next_action.can_confirm || pathRun.next_action.can_skip) && (
                      <div style={{ display: 'flex', gap: 7, marginTop: 9, flexWrap: 'wrap' }}>
                        {pathRun.next_action.can_confirm && (
                          <button type="button" disabled={busy} onClick={() => void mutateRun('confirm')} style={{ ...cardStyle, display: 'flex', gap: 6, alignItems: 'center', padding: '7px 10px', fontSize: 10.5, cursor: 'pointer' }}><Check size={13} /> I did this</button>
                        )}
                        {pathRun.next_action.can_skip && (
                          <button type="button" disabled={busy} onClick={() => void mutateRun('skip')} style={{ ...cardStyle, display: 'flex', gap: 6, alignItems: 'center', padding: '7px 10px', fontSize: 10.5, cursor: 'pointer' }}><X size={13} /> Skip this step</button>
                        )}
                      </div>
                    )}
                  </div>

                  {/* The finish line: this stage completes only when these hold. */}
                  <div style={{ marginTop: 13, paddingTop: 12, borderTop: '1px solid rgba(var(--rgb-ink),0.1)' }}>
                    <div style={{ fontSize: 10, fontWeight: 750, color: 'rgba(var(--rgb-ink),0.55)' }}>Done when</div>
                    <div style={{ display: 'grid', gap: 5, marginTop: 6 }}>
                      {(pathRun.current_stage?.check?.conditions ?? pathRun.current_stage?.done_when_text ?? []).map(condition => (
                        <div key={condition.text} style={{ display: 'grid', gridTemplateColumns: '16px minmax(0,1fr)', gap: 6, alignItems: 'start', fontSize: 11.5, lineHeight: 1.4 }}>
                          <span style={{ marginTop: 1, width: 14, height: 14, borderRadius: 99, display: 'grid', placeItems: 'center', border: `1px solid ${condition.met ? 'var(--tulsi)' : 'rgba(var(--rgb-ink),0.2)'}`, background: condition.met ? 'var(--tulsi)' : 'transparent', color: '#fff' }}>{condition.met && <Check size={9} />}</span>
                          <span style={{ color: condition.met ? 'var(--kajal)' : 'rgba(var(--rgb-ink),0.65)' }}>
                            {condition.text}
                            {!condition.met && condition.detail && <span style={{ display: 'block', fontSize: 10.5, color: 'rgba(var(--rgb-ink),0.45)' }}>{condition.detail}</span>}
                          </span>
                        </div>
                      ))}
                    </div>
                    {(pathRun.evidence ?? []).length > 0 && (
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 9 }}>
                        {(pathRun.evidence ?? []).map(ref => (
                          <button type="button" key={`${ref.kind}:${ref.id}`} onClick={() => openEvidence(ref.url)} title={ref.label} style={{ border: '1px solid rgba(var(--rgb-ink),0.12)', background: 'transparent', borderRadius: 999, padding: '5px 9px', fontSize: 9.5, color: 'rgba(var(--rgb-ink),0.62)', cursor: ref.url ? 'pointer' : 'default', maxWidth: 260, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                            {EVIDENCE_WORDS[ref.kind] ?? ref.kind}{ref.status ? ` · ${statusLabel(ref.status)}` : ''}{ref.label ? ` · ${ref.label}` : ''}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              )}

              <div style={{ display: 'grid', gridTemplateColumns: isMobile ? '1fr' : 'minmax(0,1.55fr) minmax(250px,0.75fr)', gap: 14, marginTop: 14 }}>
                <div style={{ ...cardStyle, padding: 15 }}>
                  <div style={{ fontSize: 10, fontWeight: 800, textTransform: 'uppercase', letterSpacing: '0.12em', color: 'rgba(var(--rgb-ink),0.43)' }}>Path stages</div>
                  <div style={{ display: 'grid', gap: 0, marginTop: 10 }}>
                    {pathRun.stages.map((stage, index) => {
                      const done = stage.status === 'done'
                      const skipped = stage.status === 'skipped'
                      const current = stage.id === selectedRun.current_stage_id
                      return (
                        <div key={stage.id} style={{ display: 'grid', gridTemplateColumns: '24px minmax(0,1fr)', gap: 9, minHeight: 48 }}>
                          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
                            <span style={{ width: 20, height: 20, borderRadius: 99, display: 'grid', placeItems: 'center', border: `1px solid ${done || current ? selectedRun.definition.accent : 'rgba(var(--rgb-ink),0.16)'}`, background: done ? selectedRun.definition.accent : current ? `${selectedRun.definition.accent}12` : 'transparent', color: done ? '#fff' : selectedRun.definition.accent, fontSize: 9 }}>{done ? <Check size={11} /> : index + 1}</span>
                            {index < selectedRun.stages.length - 1 && <span style={{ width: 1, flex: 1, minHeight: 18, background: done ? `${selectedRun.definition.accent}55` : 'rgba(var(--rgb-ink),0.11)' }} />}
                          </div>
                          <div style={{ paddingBottom: 12, opacity: !done && !current ? 0.52 : 1 }}>
                            <div style={{ display: 'flex', gap: 7, alignItems: 'baseline', flexWrap: 'wrap' }}><span style={{ fontSize: 11.5, fontWeight: current ? 800 : 650 }}>{stage.title}</span><span style={{ fontSize: 9.5, color: 'rgba(var(--rgb-ink),0.43)' }}>{stage.owner}{skipped ? ' · skipped' : stage.optional && !done ? ' · optional' : ''}</span></div>
                            {!done && !skipped && !current && (stage.done_when_text ?? []).length > 0 && (
                              <div style={{ marginTop: 3, fontSize: 10, lineHeight: 1.4, color: 'rgba(var(--rgb-ink),0.5)' }}>Done when: {(stage.done_when_text ?? []).map(item => item.text).join('; ')}</div>
                            )}
                            {stage.output?.summary && <div style={{ marginTop: 3, fontSize: 10.5, lineHeight: 1.4, color: 'rgba(var(--rgb-ink),0.55)' }}>{stage.output.summary.slice(0, 220)}</div>}
                            {done && (stage.output?.evidence ?? []).length > 0 && (
                              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5, marginTop: 5 }}>
                                {(stage.output?.evidence ?? []).slice(0, 4).map(ref => (
                                  <button type="button" key={`${ref.kind}:${ref.id}`} onClick={() => openEvidence(ref.url)} title={ref.label} style={{ border: '1px solid rgba(var(--rgb-ink),0.1)', background: 'transparent', borderRadius: 999, padding: '3px 7px', fontSize: 9, color: 'rgba(var(--rgb-ink),0.55)', cursor: ref.url ? 'pointer' : 'default', maxWidth: 200, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                                    {EVIDENCE_WORDS[ref.kind] ?? ref.kind}{ref.label ? ` · ${ref.label}` : ''}
                                  </button>
                                ))}
                              </div>
                            )}
                          </div>
                        </div>
                      )
                    })}
                  </div>
                </div>

                <div style={{ display: 'grid', gap: 14, alignContent: 'start' }}>
                  <div style={{ ...cardStyle, padding: 14 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 10, fontWeight: 800, textTransform: 'uppercase', letterSpacing: '0.11em', color: 'rgba(var(--rgb-ink),0.43)' }}><CalendarClock size={12} /> Rhythm</div>
                    <div style={{ display: 'grid', gap: 8, marginTop: 10 }}>
                      {selectedRun.schedules.length === 0 && <div style={{ fontSize: 10.5, color: 'rgba(var(--rgb-ink),0.45)' }}>No recurring checkpoints enabled.</div>}
                      {selectedRun.schedules.map(schedule => (
                        <label key={schedule.schedule_id} style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1fr) auto', gap: 8, cursor: 'pointer' }}>
                          <span><span style={{ display: 'block', fontSize: 10.5, fontWeight: 700 }}>{schedule.title}</span><span style={{ display: 'block', marginTop: 2, fontSize: 9.5, color: 'rgba(var(--rgb-ink),0.43)' }}>{readableDate(schedule.next_run_at)}</span></span>
                          <input type="checkbox" checked={schedule.enabled} disabled={busy} onChange={event => void toggleSchedule(schedule.schedule_id, event.target.checked)} />
                        </label>
                      ))}
                    </div>
                  </div>

                  {(pathRun.applications ?? []).length > 0 && (
                    <div style={{ ...cardStyle, padding: 14 }}>
                      <div style={{ fontSize: 10, fontWeight: 800, textTransform: 'uppercase', letterSpacing: '0.11em', color: 'rgba(var(--rgb-ink),0.43)' }}>Applications</div>
                      <div style={{ display: 'grid', gap: 7, marginTop: 9 }}>
                        {(pathRun.applications ?? []).map(item => (
                          <div key={item.record_id} style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1fr) auto', gap: 8, fontSize: 10.5 }}>
                            <span style={{ minWidth: 0 }}>
                              <span style={{ display: 'block', fontWeight: 700, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{item.company} · {item.role}</span>
                              {(item.next_step || item.reply_seen?.subject) && <span style={{ display: 'block', marginTop: 1, fontSize: 9.5, color: 'rgba(var(--rgb-ink),0.47)' }}>{item.reply_seen?.subject ? `Reply: ${item.reply_seen.subject}` : item.next_step}</span>}
                            </span>
                            <span style={{ fontSize: 9.5, color: 'rgba(var(--rgb-ink),0.55)' }}>{statusLabel(item.status)}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {(pathRun.findings ?? []).length > 0 && (
                    <div style={{ ...cardStyle, padding: 14 }}>
                      <div style={{ fontSize: 10, fontWeight: 800, textTransform: 'uppercase', letterSpacing: '0.11em', color: 'rgba(var(--rgb-ink),0.43)' }}>Watch findings</div>
                      <div style={{ marginTop: 4, fontSize: 10, color: 'rgba(var(--rgb-ink),0.45)' }}>Checks only add findings; they never change the plan.</div>
                      <div style={{ display: 'grid', gap: 7, marginTop: 9 }}>
                        {(pathRun.findings ?? []).slice(0, 5).map(item => (
                          <div key={item.record_id} style={{ fontSize: 10.5, lineHeight: 1.4 }}>
                            <span style={{ fontWeight: 700 }}>{readableDate(item.at)} · {statusLabel(item.status)}</span>
                            {item.answer && <span style={{ display: 'block', color: 'rgba(var(--rgb-ink),0.6)' }}>{item.answer.slice(0, 220)}</span>}
                            {(item.replies ?? []).map(reply => <span key={reply.subject} style={{ display: 'block', color: 'rgba(var(--rgb-ink),0.6)' }}>{reply.company}: {reply.subject}</span>)}
                            {item.task_id && item.status === 'started' && (
                              <button type="button" onClick={() => openEvidence(`/?task=${item.task_id}`)} style={{ border: 0, background: 'transparent', padding: 0, fontSize: 10, color: selectedRun.definition.accent, cursor: 'pointer' }}>Watch the check</button>
                            )}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {(pathRun.versions ?? []).length > 0 && (
                    <div style={{ ...cardStyle, padding: 14 }}>
                      <div style={{ fontSize: 10, fontWeight: 800, textTransform: 'uppercase', letterSpacing: '0.11em', color: 'rgba(var(--rgb-ink),0.43)' }}>Versions</div>
                      <div style={{ display: 'grid', gap: 6, marginTop: 9 }}>
                        {[...(pathRun.versions ?? [])].reverse().map(item => (
                          <button type="button" key={item.version} onClick={() => openEvidence(item.url)} style={{ border: 0, background: 'transparent', padding: 0, textAlign: 'left', fontSize: 10.5, color: 'var(--kajal)', cursor: item.url ? 'pointer' : 'default' }}>
                            <span style={{ fontWeight: 700 }}>v{item.version}</span> · {item.label}{item.stage_title ? <span style={{ color: 'rgba(var(--rgb-ink),0.47)' }}> · {item.stage_title}</span> : null}
                          </button>
                        ))}
                      </div>
                    </div>
                  )}

                  {selectedDefinition && Object.keys(selectedDefinition.feedback_routes).length > 0 && (
                    <div style={{ ...cardStyle, padding: 14 }}>
                      <div style={{ fontSize: 10, fontWeight: 800, textTransform: 'uppercase', letterSpacing: '0.11em', color: 'rgba(var(--rgb-ink),0.43)' }}>Feedback loop</div>
                      <div style={{ marginTop: 5, fontSize: 10.5, lineHeight: 1.4, color: 'rgba(var(--rgb-ink),0.48)' }}>Route new evidence back to the right stage.</div>
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 9 }}>
                        {Object.keys(selectedDefinition.feedback_routes).map(event => (
                          <button type="button" key={event} disabled={busy} onClick={() => void mutateRun('feedback', { event })} style={{ border: '1px solid rgba(var(--rgb-ink),0.12)', background: 'transparent', borderRadius: 999, padding: '5px 8px', fontSize: 9.5, color: 'rgba(var(--rgb-ink),0.62)', cursor: busy ? 'wait' : 'pointer' }}>{statusLabel(event)}</button>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              </div>
            </div>
          ) : (
            <div style={{ minHeight: '70%', display: 'grid', placeItems: 'center', textAlign: 'center' }}>
              <div style={{ maxWidth: 520 }}>
                <div style={{ width: 52, height: 52, display: 'grid', placeItems: 'center', margin: '0 auto', borderRadius: 15, background: 'rgba(180,83,9,0.09)', color: '#b45309' }}><CirclePlay size={23} /></div>
                <h2 style={{ margin: '15px 0 0', fontFamily: 'var(--font-hero)', fontSize: 25 }}>Choose a path with a finish line</h2>
                <p style={{ margin: '8px auto 0', fontSize: 12.5, lineHeight: 1.6, color: 'rgba(var(--rgb-ink),0.55)' }}>Each path remembers its context, advances one useful stage at a time, pauses before external actions, and loops real outcomes back into what happens next.</p>
              </div>
            </div>
          )}
        </section>
      </div>
    </div>
  )
}
