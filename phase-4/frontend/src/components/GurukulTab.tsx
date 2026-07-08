import { Component, useCallback, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { apiFetch, apiUrl } from '@/lib/api'
import { MaharishiAvatar, type MaharishiPose } from './MaharishiAvatar'

/**
 * Gurukul — the teaching chamber (G4 of GURU-AND-ONBOARDING-PLAN.md).
 * Left: syllabus tree with mastery. Center: lesson canvas with the ELI5
 * ladder and check questions. Right: study artifacts with LLM iteration.
 */

// ── types (defensive: every remote field optional) ────────────────────────────

interface ConceptAtom {
  id?: string
  name?: string
  prerequisites?: string[]
  eli5?: string
  plain?: string
  precise?: string
  formal?: string
  misconception?: string
  check?: { q?: string; good_answer?: string }
}

interface Syllabus {
  topic?: string
  generator?: string
  atoms?: ConceptAtom[]
}

interface LearnerEntry {
  status?: 'untaught' | 'shaky' | 'mastered'
  attempts?: number
  streak?: number
  next_review?: string
}

interface WorkspaceMeta {
  workspace_id?: string
  topic?: string
  updated_at?: string
  record_count?: number
  artifact_count?: number
}

interface FlashCard { id?: string; front?: string; back?: string; tags?: string[] }
interface MapNode { id?: string; label?: string; note?: string }
interface MapEdge { source?: string; target?: string; label?: string }

interface Artifact {
  artifact_id?: string
  workspace_id?: string
  artifact_type?: string
  topic?: string
  version?: number
  generator?: string
  doc?: { cards?: FlashCard[]; nodes?: MapNode[]; edges?: MapEdge[] }
}

interface WorkspaceDetail extends WorkspaceMeta {
  syllabus?: Syllabus | null
  learner_state?: Record<string, LearnerEntry>
  artifacts?: Artifact[]
}

interface Grade { correct?: boolean; feedback?: string; remediation?: string; state?: LearnerEntry }

type Rung = 'eli5' | 'plain' | 'precise' | 'formal'

const RUNGS: Array<{ id: Rung; icon: string; label: string }> = [
  { id: 'eli5', icon: '🧒', label: 'Like I’m five' },
  { id: 'plain', icon: '📖', label: 'Plain English' },
  { id: 'precise', icon: '🎯', label: 'Precise' },
  { id: 'formal', icon: '🎓', label: 'Formal' },
]

const MASTERY_COLOUR: Record<string, string> = {
  untaught: 'rgba(26,24,21,0.22)',
  shaky: '#e8a33d',
  mastered: '#065f46',
}

function postJson(path: string, body: unknown): Promise<Response> {
  return apiFetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

/** Topological depth per atom (0 = no prerequisites). Cycle-safe. */
function atomDepths(atoms: ConceptAtom[]): Map<string, number> {
  const depths = new Map<string, number>()
  const byId = new Map(atoms.map(a => [String(a.id ?? ''), a]))
  const visiting = new Set<string>()
  const depthOf = (id: string): number => {
    if (depths.has(id)) return depths.get(id)!
    if (visiting.has(id)) return 0 // cycle guard
    visiting.add(id)
    const atom = byId.get(id)
    const prereqs = (atom?.prerequisites ?? []).filter(p => byId.has(String(p)))
    const depth = prereqs.length === 0 ? 0 : Math.max(...prereqs.map(p => depthOf(String(p)))) + 1
    visiting.delete(id)
    depths.set(id, depth)
    return depth
  }
  atoms.forEach(a => depthOf(String(a.id ?? '')))
  return depths
}

// ── error boundary: one bad payload must never kill the harness ──────────────

class GurukulBoundary extends Component<{ children: ReactNode }, { error: string | null }> {
  state = { error: null as string | null }
  static getDerivedStateFromError(error: Error) {
    return { error: error?.message ?? 'render error' }
  }
  render() {
    if (this.state.error) {
      return (
        <div style={{ padding: 24, fontSize: 12, color: 'rgba(26,24,21,0.6)' }}>
          The Gurukul hit a rendering problem ({this.state.error}).{' '}
          <button
            onClick={() => this.setState({ error: null })}
            style={{ fontSize: 11, padding: '2px 10px', borderRadius: 4, border: '1px solid rgba(26,24,21,0.2)', background: 'transparent', cursor: 'pointer' }}
          >Retry</button>
        </div>
      )
    }
    return this.props.children
  }
}

// ── main component ─────────────────────────────────────────────────────────────

interface Props { userId?: string }

export function GurukulTab({ userId = 'default' }: Props) {
  return (
    <GurukulBoundary>
      <GurukulInner userId={userId} />
    </GurukulBoundary>
  )
}

function GurukulInner({ userId }: { userId: string }) {
  const [workspaces, setWorkspaces] = useState<WorkspaceMeta[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [detail, setDetail] = useState<WorkspaceDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [generating, setGenerating] = useState(false)
  const [newTopic, setNewTopic] = useState('')
  const [selectedAtomId, setSelectedAtomId] = useState<string | null>(null)
  const [rung, setRung] = useState<Rung>('eli5')
  const [answer, setAnswer] = useState('')
  const [grading, setGrading] = useState(false)
  const [grade, setGrade] = useState<Grade | null>(null)
  const [answerFocused, setAnswerFocused] = useState(false)

  const loadWorkspaces = useCallback(async (): Promise<WorkspaceMeta[]> => {
    try {
      const response = await apiFetch(apiUrl('/learning/workspaces', { user_id: userId }))
      if (!response.ok) return []
      const data = await response.json()
      const items: WorkspaceMeta[] = Array.isArray(data?.workspaces) ? data.workspaces : []
      setWorkspaces(items)
      return items
    } catch { return [] }
  }, [userId])

  const loadDetail = useCallback(async (workspaceId: string) => {
    setLoading(true)
    try {
      const response = await apiFetch(apiUrl(`/learning/workspaces/${workspaceId}`, { user_id: userId }))
      setDetail(response.ok ? await response.json() : null)
    } catch {
      setDetail(null)
    } finally {
      setLoading(false)
    }
  }, [userId])

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      const items = await loadWorkspaces()
      if (cancelled) return
      const first = items[0]?.workspace_id
      if (first) {
        setActiveId(first)
      } else {
        setLoading(false)
      }
    })()
    return () => { cancelled = true }
  }, [loadWorkspaces])

  useEffect(() => {
    if (activeId) {
      setSelectedAtomId(null)
      setGrade(null)
      setAnswer('')
      loadDetail(activeId)
    }
  }, [activeId, loadDetail])

  const syllabus = detail?.syllabus ?? null
  const atoms = useMemo(() => (syllabus?.atoms ?? []).filter(a => a && a.id), [syllabus])
  const learnerState = detail?.learner_state ?? {}
  const depths = useMemo(() => atomDepths(atoms), [atoms])
  const orderedAtoms = useMemo(
    () => [...atoms].sort((a, b) => (depths.get(String(a.id)) ?? 0) - (depths.get(String(b.id)) ?? 0)),
    [atoms, depths],
  )
  const selectedAtom = useMemo(
    () => atoms.find(a => String(a.id) === selectedAtomId) ?? null,
    [atoms, selectedAtomId],
  )
  const masteredCount = atoms.filter(a => learnerState[String(a.id)]?.status === 'mastered').length
  const allMastered = atoms.length > 0 && masteredCount === atoms.length

  const createWorkspace = useCallback(async () => {
    const topic = newTopic.trim()
    if (!topic) return
    setGenerating(true)
    try {
      const response = await postJson(apiUrl('/learning/workspaces', { user_id: userId }), { topic })
      if (!response.ok) return
      const data = await response.json()
      const workspaceId = data?.workspace?.workspace_id
      if (!workspaceId) return
      await postJson(apiUrl(`/learning/workspaces/${workspaceId}/syllabus`, { user_id: userId }), { topic })
      setNewTopic('')
      await loadWorkspaces()
      setActiveId(workspaceId)
    } catch { /* surfaced via empty state */ } finally {
      setGenerating(false)
    }
  }, [newTopic, userId, loadWorkspaces])

  const generateSyllabus = useCallback(async (force: boolean) => {
    if (!activeId) return
    setGenerating(true)
    try {
      await postJson(apiUrl(`/learning/workspaces/${activeId}/syllabus`, { user_id: userId }), { force })
      await loadDetail(activeId)
    } catch { /* stays on previous syllabus */ } finally {
      setGenerating(false)
    }
  }, [activeId, userId, loadDetail])

  const submitCheck = useCallback(async () => {
    if (!activeId || !selectedAtomId || !answer.trim()) return
    setGrading(true)
    setGrade(null)
    try {
      const response = await postJson(
        apiUrl(`/learning/workspaces/${activeId}/check`, { user_id: userId }),
        { atom_id: selectedAtomId, answer: answer.trim() },
      )
      if (!response.ok) return
      const result: Grade = await response.json()
      setGrade(result)
      if (result?.state) {
        setDetail(prev => prev ? {
          ...prev,
          learner_state: { ...(prev.learner_state ?? {}), [selectedAtomId]: result.state! },
        } : prev)
      }
    } catch { /* keep answer for retry */ } finally {
      setGrading(false)
    }
  }, [activeId, selectedAtomId, answer, userId])

  // ── maharishi pose state machine ─────────────────────────────────────────────
  const pose: MaharishiPose = useMemo(() => {
    if (generating || loading || grading) return 'thinking'
    if (!activeId || atoms.length === 0) return 'meditating'
    if (grade) return grade.correct ? 'celebrating' : 'reading'
    if (allMastered) return 'blessing'
    if (answerFocused || answer.trim()) return 'quizzing'
    if (selectedAtom) return 'teaching'
    return 'idle'
  }, [generating, loading, grading, activeId, atoms.length, grade, allMastered, answerFocused, answer, selectedAtom])

  const bubble: string = useMemo(() => {
    if (generating) return 'Let me break this into pieces a child could hold…'
    if (loading) return 'One moment…'
    if (grading) return 'Hmm, let me read your answer…'
    if (grade) return grade.correct ? (grade.feedback || 'Shabash! You have it.') : (grade.feedback || 'Not yet — walk with me once more.')
    if (!activeId || atoms.length === 0) return 'Name a topic, and we shall begin at the beginning.'
    if (allMastered) return 'You have mastered every atom. The student has become the river.'
    if (answerFocused || answer.trim()) return 'Take your time. Understanding cannot be rushed.'
    if (selectedAtom) return `Let us sit with “${selectedAtom.name ?? 'this idea'}”. Start at the rung that feels comfortable.`
    return 'Pick an atom from the tree, and we begin.'
  }, [generating, loading, grading, grade, activeId, atoms.length, allMastered, answerFocused, answer, selectedAtom])

  // ── render ────────────────────────────────────────────────────────────────────
  return (
    <div style={{ height: '100%', minHeight: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden', background: 'var(--paper)', fontFamily: 'var(--font-body)' }}>
      {/* header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 16px', flexShrink: 0, background: 'var(--kajal)', borderBottom: '1px solid rgba(245,235,215,0.1)' }}>
        <span style={{ fontFamily: 'var(--font-deva)', fontSize: 18, color: 'var(--marigold)' }}>गुरुकुल</span>
        <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--paper)', letterSpacing: 1 }}>GURUKUL</span>
        {atoms.length > 0 && (
          <span style={{ marginLeft: 4, fontFamily: 'var(--font-mono)', fontSize: 10, color: 'rgba(245,235,215,0.45)' }}>
            {masteredCount}/{atoms.length} atoms mastered
          </span>
        )}
        {syllabus?.generator === 'template' && (
          <span style={{ fontSize: 9, padding: '2px 8px', borderRadius: 4, background: 'rgba(232,163,61,0.2)', color: 'var(--marigold)' }}>
            offline syllabus — connect a model &amp; regenerate
          </span>
        )}
        {activeId && (
          <button
            onClick={() => generateSyllabus(true)}
            disabled={generating}
            style={{ marginLeft: 'auto', fontSize: 10, padding: '2px 8px', background: 'rgba(245,235,215,0.08)', border: '1px solid rgba(245,235,215,0.15)', borderRadius: 4, color: 'rgba(245,235,215,0.55)', cursor: 'pointer' }}
          >{generating ? 'Generating…' : '↻ Regenerate syllabus'}</button>
        )}
      </div>

      <div style={{ flex: 1, minHeight: 0, display: 'flex', overflow: 'hidden' }}>
        {/* left — workspaces + syllabus tree */}
        <div style={{ width: 250, flexShrink: 0, borderRight: '1px solid rgba(26,24,21,0.08)', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
          <div style={{ padding: '10px 12px', borderBottom: '1px solid rgba(26,24,21,0.07)' }}>
            <div style={{ display: 'flex', gap: 6 }}>
              <input
                value={newTopic}
                onChange={e => setNewTopic(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter') createWorkspace() }}
                placeholder="Teach me…"
                style={{ flex: 1, fontSize: 11, padding: '6px 8px', borderRadius: 6, border: '1px solid rgba(26,24,21,0.15)', background: 'rgba(255,255,255,0.6)', outline: 'none' }}
              />
              <button
                onClick={createWorkspace}
                disabled={generating || !newTopic.trim()}
                style={{ fontSize: 11, padding: '6px 10px', borderRadius: 6, border: 'none', background: 'var(--kajal)', color: 'var(--paper)', cursor: 'pointer', opacity: newTopic.trim() ? 1 : 0.4 }}
              >Begin</button>
            </div>
            {workspaces.length > 0 && (
              <select
                value={activeId ?? ''}
                onChange={e => setActiveId(e.target.value || null)}
                style={{ marginTop: 8, width: '100%', fontSize: 11, padding: '5px 6px', borderRadius: 6, border: '1px solid rgba(26,24,21,0.15)', background: 'rgba(255,255,255,0.6)' }}
              >
                {workspaces.map(w => (
                  <option key={w.workspace_id} value={w.workspace_id}>{w.topic ?? w.workspace_id}</option>
                ))}
              </select>
            )}
          </div>
          <div style={{ padding: '8px 12px 4px', flexShrink: 0 }}>
            <span style={{ fontSize: 10, fontWeight: 600, letterSpacing: 0.5, color: 'rgba(26,24,21,0.4)', textTransform: 'uppercase' }}>
              Concept atoms · prerequisites first
            </span>
          </div>
          <div style={{ flex: 1, overflow: 'auto', padding: '0 12px 12px' }}>
            {orderedAtoms.length === 0 && !loading && (
              <p style={{ fontSize: 11, color: 'rgba(26,24,21,0.35)', padding: '16px 0', textAlign: 'center' }}>
                No syllabus yet.
              </p>
            )}
            {orderedAtoms.map(atom => {
              const id = String(atom.id)
              const status = learnerState[id]?.status ?? 'untaught'
              const depth = depths.get(id) ?? 0
              const active = id === selectedAtomId
              return (
                <button
                  key={id}
                  onClick={() => { setSelectedAtomId(id); setGrade(null); setAnswer(''); setRung('eli5') }}
                  style={{
                    display: 'flex', alignItems: 'center', gap: 8, width: '100%', textAlign: 'left',
                    padding: '7px 8px', marginBottom: 4, marginLeft: depth * 10, maxWidth: `calc(100% - ${depth * 10}px)`,
                    borderRadius: 8, cursor: 'pointer',
                    border: active ? '1px solid var(--marigold)' : '1px solid rgba(26,24,21,0.07)',
                    background: active ? 'rgba(232,163,61,0.12)' : 'rgba(255,255,255,0.5)',
                  }}
                >
                  <span style={{ width: 9, height: 9, borderRadius: '50%', flexShrink: 0, background: MASTERY_COLOUR[status] ?? MASTERY_COLOUR.untaught }} />
                  <span style={{ fontSize: 11.5, lineHeight: 1.3, color: 'rgba(26,24,21,0.8)' }}>{atom.name ?? id}</span>
                </button>
              )
            })}
          </div>
        </div>

        {/* center — maharishi + lesson canvas */}
        <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', overflow: 'auto' }}>
          <div style={{ display: 'flex', alignItems: 'flex-end', gap: 14, padding: '18px 20px 6px', flexShrink: 0 }}>
            <MaharishiAvatar pose={pose} size={88} />
            <div style={{
              position: 'relative', marginBottom: 16, maxWidth: 420, padding: '10px 14px', borderRadius: 12,
              background: 'rgba(26,24,21,0.05)', border: '1px solid rgba(26,24,21,0.08)',
              fontSize: 12.5, lineHeight: 1.5, color: 'rgba(26,24,21,0.75)', fontStyle: 'italic',
            }}>
              {bubble}
            </div>
          </div>

          {selectedAtom ? (
            <div style={{ padding: '4px 20px 24px' }}>
              <h2 style={{ margin: '4px 0 2px', fontSize: 19, fontFamily: 'var(--font-hero, inherit)', color: 'var(--kajal)' }}>
                {selectedAtom.name ?? selectedAtom.id}
              </h2>
              {(selectedAtom.prerequisites ?? []).length > 0 && (
                <p style={{ margin: '0 0 10px', fontSize: 10.5, color: 'rgba(26,24,21,0.45)' }}>
                  builds on: {(selectedAtom.prerequisites ?? []).join(', ')}
                </p>
              )}
              {/* rung ladder */}
              <div style={{ display: 'flex', gap: 6, margin: '10px 0' }}>
                {RUNGS.map(r => (
                  <button
                    key={r.id}
                    onClick={() => setRung(r.id)}
                    style={{
                      fontSize: 11, padding: '5px 10px', borderRadius: 16, cursor: 'pointer',
                      border: rung === r.id ? '1px solid var(--marigold)' : '1px solid rgba(26,24,21,0.12)',
                      background: rung === r.id ? 'rgba(232,163,61,0.15)' : 'transparent',
                      fontWeight: rung === r.id ? 700 : 400, color: 'rgba(26,24,21,0.75)',
                    }}
                  >{r.icon} {r.label}</button>
                ))}
              </div>
              <div style={{ padding: '14px 16px', borderRadius: 12, background: 'rgba(255,255,255,0.65)', border: '1px solid rgba(26,24,21,0.08)', fontSize: 13.5, lineHeight: 1.65, color: 'rgba(26,24,21,0.85)' }}>
                {selectedAtom[rung] || <span style={{ opacity: 0.4 }}>This rung is empty — regenerate the syllabus with a model connected.</span>}
              </div>
              {selectedAtom.misconception && (
                <div style={{ marginTop: 10, padding: '10px 14px', borderRadius: 10, background: 'rgba(194,65,12,0.07)', borderLeft: '3px solid #c2410c', fontSize: 12, lineHeight: 1.55, color: 'rgba(26,24,21,0.7)' }}>
                  <strong style={{ color: '#c2410c' }}>Watch out:</strong> {selectedAtom.misconception}
                </div>
              )}
              {/* check question */}
              {selectedAtom.check?.q && (
                <div style={{ marginTop: 18 }}>
                  <div style={{ fontSize: 10, fontWeight: 600, letterSpacing: 0.5, color: 'rgba(26,24,21,0.4)', textTransform: 'uppercase', marginBottom: 6 }}>
                    Check yourself
                  </div>
                  <p style={{ margin: '0 0 8px', fontSize: 13.5, fontWeight: 600, color: 'var(--kajal)' }}>{selectedAtom.check.q}</p>
                  <textarea
                    value={answer}
                    onChange={e => { setAnswer(e.target.value); if (grade) setGrade(null) }}
                    onFocus={() => setAnswerFocused(true)}
                    onBlur={() => setAnswerFocused(false)}
                    placeholder="Answer in your own words…"
                    rows={3}
                    style={{ width: '100%', boxSizing: 'border-box', fontSize: 12.5, lineHeight: 1.5, padding: '8px 10px', borderRadius: 8, border: '1px solid rgba(26,24,21,0.15)', background: 'rgba(255,255,255,0.7)', resize: 'vertical', fontFamily: 'inherit', outline: 'none' }}
                  />
                  <button
                    onClick={submitCheck}
                    disabled={grading || !answer.trim()}
                    style={{ marginTop: 8, fontSize: 12, padding: '7px 16px', borderRadius: 8, border: 'none', background: 'var(--kajal)', color: 'var(--paper)', cursor: 'pointer', opacity: answer.trim() && !grading ? 1 : 0.4 }}
                  >{grading ? 'Reading…' : 'Submit to the Guru'}</button>
                  {grade && (
                    <div style={{
                      marginTop: 10, padding: '10px 14px', borderRadius: 10, fontSize: 12.5, lineHeight: 1.55,
                      background: grade.correct ? 'rgba(6,95,70,0.08)' : 'rgba(232,163,61,0.1)',
                      borderLeft: `3px solid ${grade.correct ? '#065f46' : '#e8a33d'}`,
                      color: 'rgba(26,24,21,0.78)',
                    }}>
                      <strong style={{ color: grade.correct ? '#065f46' : '#9a6a12' }}>
                        {grade.correct ? '✓ Mastered.' : '↻ Almost.'}
                      </strong>{' '}
                      {grade.feedback}
                      {!grade.correct && grade.remediation && (
                        <p style={{ margin: '8px 0 0', fontStyle: 'italic' }}>{grade.remediation}</p>
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>
          ) : (
            !loading && (
              <div style={{ padding: '10px 20px', fontSize: 12, color: 'rgba(26,24,21,0.4)' }}>
                {atoms.length > 0
                  ? 'Select a concept atom from the left to begin the lesson.'
                  : 'Enter a topic on the left — the Guru will decompose it into atoms a five-year-old could climb.'}
              </div>
            )
          )}
        </div>

        {/* right — artifact rail */}
        <ArtifactRail
          userId={userId}
          workspaceId={activeId}
          topic={detail?.topic ?? ''}
          artifacts={detail?.artifacts ?? []}
          onChanged={() => activeId && loadDetail(activeId)}
        />
      </div>
    </div>
  )
}

// ── artifact rail ──────────────────────────────────────────────────────────────

function ArtifactRail({ userId, workspaceId, topic, artifacts, onChanged }: {
  userId: string
  workspaceId: string | null
  topic: string
  artifacts: Artifact[]
  onChanged: () => void
}) {
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [instruction, setInstruction] = useState('')
  const [flipped, setFlipped] = useState<Record<string, boolean>>({})

  const selected = useMemo(
    () => artifacts.find(a => a.artifact_id === selectedId) ?? artifacts[0] ?? null,
    [artifacts, selectedId],
  )

  const createArtifact = useCallback(async (type: 'flashcards' | 'concept_map') => {
    if (!workspaceId || busy) return
    setBusy(true)
    try {
      const response = await postJson(apiUrl('/learning/artifacts', { user_id: userId }), {
        workspace_id: workspaceId,
        topic,
        artifact_type: type,
      })
      if (response.ok) {
        const data = await response.json()
        if (data?.artifact?.artifact_id) setSelectedId(data.artifact.artifact_id)
        onChanged()
      }
    } catch { /* rail stays as-is */ } finally {
      setBusy(false)
    }
  }, [workspaceId, topic, userId, busy, onChanged])

  const iterate = useCallback(async () => {
    if (!selected?.artifact_id || !instruction.trim() || busy) return
    setBusy(true)
    try {
      const response = await postJson(
        apiUrl(`/learning/artifacts/${selected.artifact_id}/update`, { user_id: userId }),
        { instruction: instruction.trim(), workspace_id: workspaceId },
      )
      if (response.ok) {
        setInstruction('')
        onChanged()
      }
    } catch { /* keep instruction for retry */ } finally {
      setBusy(false)
    }
  }, [selected, instruction, userId, workspaceId, busy, onChanged])

  const cards = selected?.doc?.cards ?? []
  const nodes = selected?.doc?.nodes ?? []
  const edges = selected?.doc?.edges ?? []
  const isMap = selected?.artifact_type === 'concept_map'

  return (
    <div style={{ width: 300, flexShrink: 0, borderLeft: '1px solid rgba(26,24,21,0.08)', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      <div style={{ padding: '10px 12px', borderBottom: '1px solid rgba(26,24,21,0.07)', flexShrink: 0 }}>
        <div style={{ fontSize: 10, fontWeight: 600, letterSpacing: 0.5, color: 'rgba(26,24,21,0.4)', textTransform: 'uppercase', marginBottom: 8 }}>
          Study artifacts
        </div>
        <div style={{ display: 'flex', gap: 6 }}>
          <button
            onClick={() => createArtifact('flashcards')}
            disabled={!workspaceId || busy}
            style={{ flex: 1, fontSize: 10.5, padding: '6px 4px', borderRadius: 6, border: '1px solid rgba(26,24,21,0.15)', background: 'rgba(255,255,255,0.6)', cursor: 'pointer' }}
          >+ Flashcards</button>
          <button
            onClick={() => createArtifact('concept_map')}
            disabled={!workspaceId || busy}
            style={{ flex: 1, fontSize: 10.5, padding: '6px 4px', borderRadius: 6, border: '1px solid rgba(26,24,21,0.15)', background: 'rgba(255,255,255,0.6)', cursor: 'pointer' }}
          >+ Concept map</button>
        </div>
        {artifacts.length > 1 && (
          <select
            value={selected?.artifact_id ?? ''}
            onChange={e => setSelectedId(e.target.value)}
            style={{ marginTop: 8, width: '100%', fontSize: 10.5, padding: '5px 6px', borderRadius: 6, border: '1px solid rgba(26,24,21,0.15)', background: 'rgba(255,255,255,0.6)' }}
          >
            {artifacts.map(a => (
              <option key={a.artifact_id} value={a.artifact_id}>
                {(a.artifact_type === 'concept_map' ? 'Map' : 'Cards')} · v{a.version ?? 1}
              </option>
            ))}
          </select>
        )}
      </div>

      <div style={{ flex: 1, overflow: 'auto', padding: 12 }}>
        {!selected && (
          <p style={{ fontSize: 11, color: 'rgba(26,24,21,0.35)', textAlign: 'center', padding: '20px 0' }}>
            {busy ? 'Creating…' : 'No artifacts yet — create flashcards or a concept map.'}
          </p>
        )}

        {selected && !isMap && cards.map((card, i) => {
          const key = card.id ?? `card-${i}`
          const isFlipped = !!flipped[key]
          return (
            <button
              key={key}
              onClick={() => setFlipped(prev => ({ ...prev, [key]: !prev[key] }))}
              style={{
                display: 'block', width: '100%', textAlign: 'left', marginBottom: 8, padding: '10px 12px',
                borderRadius: 10, cursor: 'pointer', minHeight: 58,
                border: '1px solid rgba(26,24,21,0.1)',
                background: isFlipped ? 'rgba(232,163,61,0.1)' : 'rgba(255,255,255,0.65)',
              }}
            >
              <div style={{ fontSize: 8.5, fontWeight: 700, letterSpacing: 0.5, textTransform: 'uppercase', color: isFlipped ? '#9a6a12' : 'rgba(26,24,21,0.35)', marginBottom: 4 }}>
                {isFlipped ? 'Answer · tap to flip' : 'Question · tap to flip'}
              </div>
              <div style={{ fontSize: 11.5, lineHeight: 1.45, color: 'rgba(26,24,21,0.8)' }}>
                {isFlipped ? (card.back ?? '') : (card.front ?? '')}
              </div>
            </button>
          )
        })}

        {selected && isMap && <ConceptMapView nodes={nodes} edges={edges} />}
      </div>

      {selected && (
        <div style={{ padding: '10px 12px', borderTop: '1px solid rgba(26,24,21,0.07)', flexShrink: 0 }}>
          <div style={{ display: 'flex', gap: 6 }}>
            <input
              value={instruction}
              onChange={e => setInstruction(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') iterate() }}
              placeholder='Iterate: "add a card about…"'
              style={{ flex: 1, fontSize: 11, padding: '6px 8px', borderRadius: 6, border: '1px solid rgba(26,24,21,0.15)', background: 'rgba(255,255,255,0.6)', outline: 'none' }}
            />
            <button
              onClick={iterate}
              disabled={busy || !instruction.trim()}
              style={{ fontSize: 11, padding: '6px 10px', borderRadius: 6, border: 'none', background: 'var(--kajal)', color: 'var(--paper)', cursor: 'pointer', opacity: instruction.trim() && !busy ? 1 : 0.4 }}
            >{busy ? '…' : '✦'}</button>
          </div>
          <div style={{ marginTop: 6, fontFamily: 'var(--font-mono)', fontSize: 9, color: 'rgba(26,24,21,0.3)' }}>
            v{selected.version ?? 1}{selected.generator ? ` · ${selected.generator}` : ''}
          </div>
        </div>
      )}
    </div>
  )
}

// ── concept map (layered SVG, no deps) ─────────────────────────────────────────

function ConceptMapView({ nodes, edges }: { nodes: MapNode[]; edges: MapEdge[] }) {
  const layout = useMemo(() => {
    const ids = nodes.map(n => String(n.id ?? ''))
    const idSet = new Set(ids)
    // depth = longest incoming chain (edges point source → target)
    const incoming = new Map<string, string[]>()
    ids.forEach(id => incoming.set(id, []))
    edges.forEach(e => {
      const s = String(e.source ?? ''), t = String(e.target ?? '')
      if (idSet.has(s) && idSet.has(t)) incoming.get(t)!.push(s)
    })
    const depth = new Map<string, number>()
    const visiting = new Set<string>()
    const depthOf = (id: string): number => {
      if (depth.has(id)) return depth.get(id)!
      if (visiting.has(id)) return 0
      visiting.add(id)
      const parents = incoming.get(id) ?? []
      const d = parents.length === 0 ? 0 : Math.max(...parents.map(depthOf)) + 1
      visiting.delete(id)
      depth.set(id, d)
      return d
    }
    ids.forEach(depthOf)
    const rows = new Map<number, string[]>()
    ids.forEach(id => {
      const d = depth.get(id) ?? 0
      rows.set(d, [...(rows.get(d) ?? []), id])
    })
    const positions = new Map<string, { x: number; y: number }>()
    const rowH = 64
    const width = 268
    ;[...rows.entries()].forEach(([d, rowIds]) => {
      rowIds.forEach((id, i) => {
        positions.set(id, {
          x: ((i + 1) / (rowIds.length + 1)) * width,
          y: 30 + d * rowH,
        })
      })
    })
    const height = 30 + (Math.max(0, ...[...rows.keys()]) + 1) * rowH
    return { positions, width, height }
  }, [nodes, edges])

  if (nodes.length === 0) {
    return <p style={{ fontSize: 11, color: 'rgba(26,24,21,0.35)', textAlign: 'center' }}>Empty map.</p>
  }

  return (
    <svg viewBox={`0 0 ${layout.width} ${layout.height}`} width="100%" style={{ display: 'block' }}>
      {edges.map((e, i) => {
        const s = layout.positions.get(String(e.source ?? ''))
        const t = layout.positions.get(String(e.target ?? ''))
        if (!s || !t) return null
        const midX = (s.x + t.x) / 2
        const midY = (s.y + t.y) / 2
        return (
          <g key={`e-${i}`}>
            <line x1={s.x} y1={s.y} x2={t.x} y2={t.y} stroke="rgba(26,24,21,0.18)" strokeWidth="1.2" />
            {e.label && (
              <text x={midX} y={midY - 3} textAnchor="middle" fontSize="6.5" fill="rgba(26,24,21,0.4)" fontStyle="italic">
                {String(e.label).slice(0, 18)}
              </text>
            )}
          </g>
        )
      })}
      {nodes.map((n, i) => {
        const p = layout.positions.get(String(n.id ?? ''))
        if (!p) return null
        const label = String(n.label ?? n.id ?? '').slice(0, 20)
        const w = Math.max(44, label.length * 5.4 + 14)
        return (
          <g key={n.id ?? `n-${i}`}>
            <rect x={p.x - w / 2} y={p.y - 11} width={w} height={22} rx={11} fill="rgba(232,163,61,0.14)" stroke="var(--marigold, #e8a33d)" strokeWidth="1" />
            <text x={p.x} y={p.y + 3} textAnchor="middle" fontSize="8" fontWeight="600" fill="var(--kajal, #2d2a26)">
              {label}
            </text>
            {n.note && <title>{n.note}</title>}
          </g>
        )
      })}
    </svg>
  )
}
