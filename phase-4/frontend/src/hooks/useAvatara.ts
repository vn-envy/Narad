import { useState, useCallback, useRef, useEffect } from 'react'
import { toast } from 'sonner'
import { apiPath, apiUrl, apiFetch, type ApprovalProposal } from '@/lib/api'
import type { KriyaTask } from '@/lib/tasks'

export type AvatarName = 'Matsya' | 'Rama' | 'Krishna' | 'Parashurama'

export type AvatarState = 'idle' | 'active' | 'done'

export interface AvatarStatus {
  name: AvatarName
  state: AvatarState
  discipline?: string
  task?: string
  latencyMs?: number
  startedAt?: number
}

export interface TokenUsage {
  promptTokens:     number
  completionTokens: number
  totalTokens:      number
  tokPerSec?:       number
  synthDurationMs?: number
  /** M4.1: server-priced cost for this turn (USD) — client keeps no price table. */
  costUsd?:         number
}

export interface ChatAttachment {
  attachment_id: string
  batch_id: string
  name: string
  relative_path: string
  mime_type: string
  kind: 'image' | 'audio' | 'video' | 'document' | 'archive' | 'data' | 'code' | 'text' | 'file'
  size_bytes: number
  sha256: string
  content_url: string
}

export interface ChatAttachmentBatch {
  batch_id: string
  source: 'files' | 'folder'
  label: string
  file_count: number
  size_bytes: number
  created_at: string
  attachments: ChatAttachment[]
}

interface StoredThreadTurn {
  role: 'user' | 'assistant'
  text: string
  metadata?: Record<string, unknown>
}

function storedTurnAttachments(turn: StoredThreadTurn): ChatAttachment[] | undefined {
  const value = turn.metadata?.attachments
  return Array.isArray(value) ? value as ChatAttachment[] : undefined
}

export interface Message {
  id: string
  /** 'approval': an Anumati card, never a reply to speak, copy or replay.
   *  'task': a Kriya task card (a background errand), likewise. */
  role: 'user' | 'assistant' | 'approval' | 'task'
  text: string
  avatarsInvolved?: AvatarName[]
  sessionId?: string
  tokenEstimate?: number
  totalDurationMs?: number
  clientTokPerSec?: number
  usage?: TokenUsage
  avatarUsage?: Record<string, TokenUsage>
  avatarLatencies?: Record<string, number>
  attachments?: ChatAttachment[]
  /** G7: present when this message is a guided-mode (guru) card, not prose. */
  guru?: GuruPayload
  /** Anumati: present when this message is an approval card, not prose. */
  approval?: ApprovalProposal
  /** Kriya: present when this message is a task card, not prose. */
  task?: KriyaTask
}

export interface SessionInfo {
  sessionId: string
  avatarsFired: AvatarName[]
  totalMs?: number
  totalTokens?: number
  promptTokens?: number
  completionTokens?: number
  tokPerSec?: number
}

export interface StepEvent {
  id: string
  avatar: string
  kind: 'tool_call' | 'tool_result' | 'text'
  discipline?: string
  tool?: string
  preview: string
  ts: number
}

export interface ArtifactFlashcard {
  id: string
  front: string
  back: string
  tags?: string[]
}

export interface ArtifactConceptNode {
  id: string
  label: string
  note: string
}

export interface ArtifactConceptEdge {
  source: string
  target: string
  label?: string
}

export interface ActiveArtifactSession {
  artifactId: string
  topic: string
  artifactType: 'flashcards' | 'concept_map'
  workspaceId: string
  version: number
  status: string
  updatedAt?: string
  recordIds: string[]
  doc: {
    cards?: ArtifactFlashcard[]
    nodes?: ArtifactConceptNode[]
    edges?: ArtifactConceptEdge[]
  }
}

// ── Guided mode (G7): /teach's step-by-step session loop ─────────────────────

export interface GuidedQuiz {
  type: 'mcq' | 'free'
  question: string
  options?: string[]
}

export interface GuidedProgress {
  total: number
  mastered: number
  shaky: number
  topic: string
  mode: string
}

export interface GuidedStep {
  kind: 'atom' | 'complete'
  atom_id?: string
  name?: string
  narration?: string
  artifact_html?: string
  quiz?: GuidedQuiz
  message?: string
  progress: GuidedProgress
  avatar: string
}

export interface GuidedGrade {
  correct: boolean
  feedback: string
  remediation: string
  grader?: string
  choiceIndex?: number
}

export interface GuidedSessionMeta {
  workspaceId: string
  topic: string
  mode: string
  status: string
  workflowRunId?: string | null
}

export interface SendOptions {
  workflowRunId?: string | null
  replyLanguage?: string | null
}

export type GuruPayload =
  | { kind: 'step'; step: GuidedStep; grade?: GuidedGrade; answered?: boolean }
  | { kind: 'complete'; message: string; progress?: GuidedProgress }
  | { kind: 'exit'; message: string; progress?: GuidedProgress }
  | { kind: 'info'; message: string }

export interface ToolArtifact {
  type: string
  label: string
  url?: string
  path?: string
  description?: string
  mime_type?: string
  metadata?: Record<string, unknown>
}

export interface ToolCitation {
  title: string
  url: string
  source?: string
  snippet?: string
  metadata?: Record<string, unknown>
}

export interface PendingToolUi {
  avatar: string
  tool: string
  status: string
  summary: string
  requiresConfirmation?: boolean
  artifacts: ToolArtifact[]
  citations: ToolCitation[]
  ui?: {
    kind?: string
    title?: string
    summary?: string
    tone?: string
    primary_artifact_label?: string
    sections?: Array<{ title: string; body: string }>
  } | null
}

export interface AndonAlertPayload {
  avatar: string
  trigger: string
  task_preview?: string
}

/** The answer being written: streamed text_delta events, before narad_synthesis. */
export interface LiveAnswer {
  source: string
  text: string
  avatars: AvatarName[]
}

// Streamed text per source ('narad' or an avatar), in the order sources began.
interface LiveStreams {
  order: string[]
  sources: Record<string, { text: string; handoff: boolean }>
}

// Floor between live re-renders: markdown re-parses on every one, and a phone
// reads comfortably at 20 updates a second.
const LIVE_MIN_INTERVAL_MS = 50

function emptyLiveStreams(): LiveStreams {
  return { order: [], sources: {} }
}

/** Narad's own text is the reply; otherwise a handed-off avatar's is. Drafts an
 *  avatar writes for Narad to combine (handoff=false) never show as the answer. */
function pickLiveAnswer(streams: LiveStreams, completedAvatars: AvatarName[]): LiveAnswer | null {
  const narad = streams.sources.narad
  if (narad?.text) return { source: 'narad', text: narad.text, avatars: [...completedAvatars] }
  for (const source of streams.order) {
    const entry = streams.sources[source]
    if (source !== 'narad' && entry?.handoff && entry.text) {
      return { source, text: entry.text, avatars: [source as AvatarName] }
    }
  }
  return null
}

interface AvatararState {
  messages: Message[]
  avatars: Record<AvatarName, AvatarStatus>
  naradActive: boolean
  currentSession: SessionInfo | null
  streaming: boolean
  error: string | null
  stepEvents: StepEvent[]
  sessionTotals: { promptTokens: number; completionTokens: number; totalTokens: number; costUsd: number }
  activeArtifactSession: ActiveArtifactSession | null
  pendingToolUi: PendingToolUi | null
  andonAlert: AndonAlertPayload | null
  guidedSession: GuidedSessionMeta | null
  /** Streamed answer text; kept out of `messages` so it is never persisted mid-stream. */
  liveAnswer: LiveAnswer | null
}

const AVATAR_NAMES: AvatarName[] = ['Matsya', 'Rama', 'Krishna', 'Parashurama']

function initialAvatars(): Record<AvatarName, AvatarStatus> {
  return Object.fromEntries(
    AVATAR_NAMES.map(name => [name, { name, state: 'idle' as AvatarState }])
  ) as Record<AvatarName, AvatarStatus>
}

const SESSION_KEY = 'avatara_messages'
const CONVO_SESSION_KEY = 'avatara_convo_session_id'
const GUIDED_KEY = 'narad_guided_session'

function scopedKey(key: string, userId: string): string {
  return `${key}:${userId}`
}

/** Parse guided-mode slash commands. Returns null for normal chat messages. */
export function parseGuidedCommand(query: string):
  | { action: 'teach'; topic: string }
  | { action: 'teach-empty' }
  | { action: 'exit' }
  | null {
  const q = query.trim()
  const teach = q.match(/^\/teach\b(?:\s+me\b)?(?:\s+about\b)?\s*(.*)$/i)
  if (teach) {
    const topic = (teach[1] ?? '').trim().replace(/[?.!]+$/, '').trim()
    return topic ? { action: 'teach', topic } : { action: 'teach-empty' }
  }
  if (/^\/exit\b/i.test(q)) return { action: 'exit' }
  return null
}

function loadGuidedSession(userId: string): GuidedSessionMeta | null {
  try {
    const raw = readStorage(scopedKey(GUIDED_KEY, userId))
    return raw ? (JSON.parse(raw) as GuidedSessionMeta) : null
  } catch {
    return null
  }
}

function readStorage(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    try {
      return sessionStorage.getItem(key)
    } catch {
      return null
    }
  }
}

function writeStorage(key: string, value: string): void {
  try {
    localStorage.setItem(key, value)
  } catch {
    try {
      sessionStorage.setItem(key, value)
    } catch {
      // ignore storage failures
    }
  }
}

function removeStorage(key: string): void {
  try { localStorage.removeItem(key) } catch { /* ignore */ }
  try { sessionStorage.removeItem(key) } catch { /* ignore */ }
}

function emitWorkflowRuntimeEvent(type: string, data: Record<string, unknown>): void {
  if (typeof window === 'undefined') return
  window.dispatchEvent(new CustomEvent('narad:workflow-event', {
    detail: { type, data, ts: Date.now() },
  }))
}

function emitGuidedWorkflowUpdate(raw: unknown): void {
  if (!raw || typeof raw !== 'object') return
  const run = raw as Record<string, unknown>
  const runId = String(run.run_id ?? '')
  if (!runId) return
  emitWorkflowRuntimeEvent('workflow_updated', { workflow_run_id: runId, run })
}

function loadMessages(userId: string): Message[] {
  try {
    const raw = readStorage(scopedKey(SESSION_KEY, userId))
    return raw ? (JSON.parse(raw) as Message[]) : []
  } catch {
    return []
  }
}

/** Put an approval card in the chat, or refresh the one already showing it.
 *  `replaces` is the proposal an edit superseded; `append` false only updates. */
function upsertApprovalMessage(
  messages: Message[],
  proposal: ApprovalProposal,
  { replaces, append = true }: { replaces?: string; append?: boolean } = {},
): Message[] {
  const index = messages.findIndex(m => m.approval && (m.approval.id === proposal.id || m.approval.id === replaces))
  if (index < 0) {
    if (!append) return messages
    return [...messages, { id: `approval-${proposal.id}`, role: 'approval', text: proposal.summary, approval: proposal }]
  }
  const next = [...messages]
  next[index] = { ...next[index], text: proposal.summary, approval: proposal }
  return next
}

/** Put a task card in the chat, or refresh the one already showing that task. */
function upsertTaskMessage(messages: Message[], task: KriyaTask): Message[] {
  const index = messages.findIndex(m => m.task?.id === task.id)
  if (index < 0) return [...messages, { id: `task-${task.id}`, role: 'task', text: task.goal, task }]
  const next = [...messages]
  next[index] = { ...next[index], text: task.goal, task }
  return next
}

function lastKnownSessionId(messages: Message[]): string | null {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const sessionId = messages[index]?.sessionId
    if (sessionId) return sessionId
  }
  return null
}

function toActiveArtifactSession(raw: Record<string, unknown> | null | undefined): ActiveArtifactSession | null {
  if (!raw) return null
  const source = (!('artifact_id' in raw) && raw.active_artifact && typeof raw.active_artifact === 'object')
    ? raw.active_artifact as Record<string, unknown>
    : raw
  const artifactId = String(source.artifact_id ?? source.artifactId ?? '').trim()
  const topic = String(source.topic ?? '').trim()
  const resolvedWorkspaceId = String(source.workspace_id ?? source.workspaceId ?? '').trim()
  const artifactTypeRaw = String(source.artifact_type ?? source.artifactType ?? 'flashcards').trim().toLowerCase()
  const status = String(source.status ?? 'active')
  if (!artifactId || !resolvedWorkspaceId || !topic) return null
  return {
    artifactId,
    workspaceId: resolvedWorkspaceId,
    topic,
    artifactType: artifactTypeRaw === 'concept_map' || artifactTypeRaw === 'diagram' ? 'concept_map' : 'flashcards',
    version: Number(source.version ?? 1) || 1,
    status,
    updatedAt: source.updated_at ? String(source.updated_at) : undefined,
    recordIds: Array.isArray(source.record_ids) ? source.record_ids.map(item => String(item)) : [],
    doc: (source.doc && typeof source.doc === 'object') ? source.doc as ActiveArtifactSession['doc'] : {},
  }
}

// One stable session ID for the whole browser session — reused across all messages
// so the backend's InMemorySessionService accumulates conversation history.
function getOrCreateConvoSessionId(userId: string): string {
  const key = scopedKey(CONVO_SESSION_KEY, userId)
  try {
    const existing = readStorage(key)
    if (existing) return existing
    const id = crypto.randomUUID()
    writeStorage(key, id)
    return id
  } catch {
    return crypto.randomUUID()
  }
}

// Called after a backend error — rotates the session ID so we don't keep
// hitting a server-side session that was deleted due to corruption.
function rotateConvoSessionId(userId: string): string {
  const id = crypto.randomUUID()
  writeStorage(scopedKey(CONVO_SESSION_KEY, userId), id)
  return id
}

export function useAvatara(userId = 'default') {
  const messageStorageKey = scopedKey(SESSION_KEY, userId)
  const conversationStorageKey = scopedKey(CONVO_SESSION_KEY, userId)
  const guidedStorageKey = scopedKey(GUIDED_KEY, userId)
  const initialMessages = loadMessages(userId)
  const initialSessionId = getOrCreateConvoSessionId(userId)
  const fallbackSessionId = lastKnownSessionId(initialMessages)
  const [state, setState] = useState<AvatararState>({
    messages: initialMessages,
    avatars: initialAvatars(),
    naradActive: false,
    currentSession: null,
    streaming: false,
    error: null,
    stepEvents: [],
    sessionTotals: { promptTokens: 0, completionTokens: 0, totalTokens: 0, costUsd: 0 },
    activeArtifactSession: null,
    pendingToolUi: null,
    andonAlert: null,
    guidedSession: loadGuidedSession(userId),
    liveAnswer: null,
  })

  // Set to Date.now() when the answer's first text shows (the first visible
  // text_delta, else the first narad_synthesis chunk) — intentionally excludes
  // routing and tool time so tok/sec reflects only LLM generation speed.
  const synthStartRef = useRef<number | null>(null)
  const liveStreamsRef = useRef<LiveStreams>(emptyLiveStreams())
  const liveFrameRef = useRef<number | null>(null)
  const liveRenderedAtRef = useRef(0)
  // Per-message token usage captured from the `usage` SSE event (fires before `done`)
  const msgUsageRef = useRef<TokenUsage | null>(null)
  const sessionAvatarsRef = useRef<AvatarName[]>([])
  const synthRef = useRef('')
  const msgIdRef = useRef('')
  const convoSessionId = useRef(fallbackSessionId ?? initialSessionId)
  const abortRef = useRef<AbortController | null>(null)

  // Deltas land in a ref; the view catches up at most once per animation frame
  // (and every LIVE_MIN_INTERVAL_MS), so a fast stream never janks a phone.
  const scheduleLiveRender = useCallback(() => {
    if (liveFrameRef.current !== null) return
    const tick = () => {
      if (performance.now() - liveRenderedAtRef.current < LIVE_MIN_INTERVAL_MS) {
        liveFrameRef.current = requestAnimationFrame(tick)
        return
      }
      liveFrameRef.current = null
      liveRenderedAtRef.current = performance.now()
      const live = pickLiveAnswer(liveStreamsRef.current, sessionAvatarsRef.current)
      setState(s => (
        s.liveAnswer?.text === live?.text && s.liveAnswer?.source === live?.source
          ? s
          : { ...s, liveAnswer: live }
      ))
    }
    liveFrameRef.current = requestAnimationFrame(tick)
  }, [])

  /** Forget streamed text; the caller clears state.liveAnswer in its own update. */
  const resetLiveStreams = useCallback(() => {
    if (liveFrameRef.current !== null) cancelAnimationFrame(liveFrameRef.current)
    liveFrameRef.current = null
    liveStreamsRef.current = emptyLiveStreams()
  }, [])

  useEffect(() => resetLiveStreams, [resetLiveStreams])

  useEffect(() => {
    writeStorage(conversationStorageKey, convoSessionId.current)
  }, [conversationStorageKey])

  // Persist messages to sessionStorage whenever they change
  useEffect(() => {
    try {
      writeStorage(messageStorageKey, JSON.stringify(state.messages))
    } catch {
      // storage unavailable — silent fail
    }
  }, [messageStorageKey, state.messages])

  useEffect(() => {
    let cancelled = false
    const hydrateThread = (
      sessionId: string,
      turns: StoredThreadTurn[],
      workingState?: Record<string, unknown> | null,
    ) => {
      if (cancelled || turns.length === 0) return
      convoSessionId.current = sessionId
      writeStorage(conversationStorageKey, sessionId)
      const activeArtifact = toActiveArtifactSession(workingState)
      setState(current => {
        if (current.messages.length >= turns.length && current.currentSession?.sessionId === sessionId) {
          return {
            ...current,
            activeArtifactSession: activeArtifact ?? current.activeArtifactSession,
          }
        }
        const restoredMessages: Message[] = turns.map((turn, index) => ({
          id: `${sessionId}-${index}`,
          role: turn.role,
          text: turn.text,
          sessionId,
          attachments: storedTurnAttachments(turn),
        }))
        return {
          ...current,
          currentSession: current.currentSession?.sessionId === sessionId
            ? current.currentSession
            : {
                sessionId,
                avatarsFired: current.currentSession?.avatarsFired ?? [],
              },
          messages: restoredMessages,
          activeArtifactSession: activeArtifact ?? current.activeArtifactSession,
        }
      })
    }

    const fetchLatestThread = () =>
      apiFetch(apiUrl('/threads/latest', { user_id: userId }))
        .then(response => (response.ok ? response.json() : null))
        .then((payload: {
          thread?: { session_id?: string } | null
          has_thread?: boolean
        } | null) => {
          const latestSessionId = payload?.thread?.session_id
          if (cancelled || !payload?.has_thread || !latestSessionId) return
          return apiFetch(apiUrl(`/thread/${latestSessionId}`, { user_id: userId }))
            .then(response => (response.ok ? response.json() : null))
            .then((data: { turns?: StoredThreadTurn[]; working_state?: Record<string, unknown> | null } | null) => {
              if (!Array.isArray(data?.turns) || data.turns.length === 0) return
              hydrateThread(latestSessionId, data.turns, data.working_state)
            })
        })
        .catch(() => {})

    const sessionId = convoSessionId.current
    if (!sessionId) {
      fetchLatestThread()
    } else {
      apiFetch(apiUrl(`/thread/${sessionId}`, { user_id: userId }))
        .then(response => (response.ok ? response.json() : null))
        .then((data: { turns?: StoredThreadTurn[]; working_state?: Record<string, unknown> | null } | null) => {
          if (Array.isArray(data?.turns) && data.turns.length > 0) {
            hydrateThread(sessionId, data.turns, data.working_state)
            return
          }
          return fetchLatestThread()
        })
        .catch(() => fetchLatestThread())
    }

    return () => {
      cancelled = true
    }
  }, [userId])

  const stop = useCallback(() => {
    abortRef.current?.abort()
  }, [])

  // ── Guided mode (G7): /teach loop, deterministic server state machine ───────
  // Ref mirrors state.guidedSession so send()'s closure never goes stale.
  const guidedRef = useRef<GuidedSessionMeta | null>(loadGuidedSession(userId))

  const setGuided = useCallback((meta: GuidedSessionMeta | null) => {
    guidedRef.current = meta
    if (meta) writeStorage(guidedStorageKey, JSON.stringify(meta))
    else removeStorage(guidedStorageKey)
    setState(s => ({ ...s, guidedSession: meta }))
  }, [guidedStorageKey])

  const appendMessages = useCallback((newMessages: Message[]) => {
    setState(s => ({ ...s, messages: [...s.messages, ...newMessages] }))
  }, [])

  /** Convert a server step payload into a guru chat message. */
  const stepToMessage = useCallback((step: GuidedStep): Message => {
    if (step.kind === 'complete') {
      return {
        id: crypto.randomUUID(),
        role: 'assistant',
        text: step.message ?? 'All atoms mastered.',
        avatarsInvolved: ['Krishna'],
        guru: { kind: 'complete', message: step.message ?? 'All atoms mastered.', progress: step.progress },
      }
    }
    return {
      id: crypto.randomUUID(),
      role: 'assistant',
      // text mirrors narration so copy + per-message speak buttons work as-is
      text: step.narration ?? '',
      avatarsInvolved: ['Krishna'],
      guru: { kind: 'step', step },
    }
  }, [])

  const guidedPost = useCallback(async (path: string, body: Record<string, unknown>) => {
    const response = await apiFetch(apiUrl(path, { user_id: userId }), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    const data = await response.json().catch(() => ({}))
    if (!response.ok) {
      throw new Error(typeof data.detail === 'string' ? data.detail : `HTTP ${response.status}`)
    }
    return data
  }, [userId])

  const startGuided = useCallback(async (rawQuery: string, topic: string, mode = 'teach', workflowRunId?: string | null) => {
    const userMsg: Message = { id: crypto.randomUUID(), role: 'user', text: rawQuery }
    appendMessages([userMsg])
    setState(s => ({ ...s, streaming: true, error: null }))
    try {
      const data = await guidedPost('/learning/guided/start', { topic, mode, workflow_run_id: workflowRunId ?? null })
      const session = data.session as { workspace_id: string; topic: string; mode: string; status: string }
      const step = data.step as GuidedStep
      emitGuidedWorkflowUpdate(data.workflow_run)
      const resolvedWorkflowRunId = String((data.workflow_run as Record<string, unknown> | undefined)?.run_id ?? workflowRunId ?? '') || null
      if (step.kind === 'complete') {
        setGuided(null)
      } else {
        setGuided({
          workspaceId: session.workspace_id,
          topic: session.topic,
          mode: session.mode,
          status: 'active',
          workflowRunId: resolvedWorkflowRunId,
        })
      }
      const intro: Message[] = []
      if (data.resumed && step.kind === 'atom') {
        intro.push({
          id: crypto.randomUUID(),
          role: 'assistant',
          text: `Welcome back — resuming ${session.topic} where you left off.`,
          avatarsInvolved: ['Krishna'],
          guru: { kind: 'info', message: `Welcome back — resuming ${session.topic} where you left off.` },
        })
      }
      appendMessages([...intro, stepToMessage(step)])
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Could not start the lesson.'
      appendMessages([{
        id: crypto.randomUUID(),
        role: 'assistant',
        text: message,
        avatarsInvolved: ['Krishna'],
        guru: { kind: 'info', message },
      }])
    } finally {
      setState(s => ({ ...s, streaming: false }))
    }
  }, [appendMessages, guidedPost, setGuided, stepToMessage])

  const answerGuided = useCallback(async (messageId: string, answer?: string, choiceIndex?: number) => {
    const meta = guidedRef.current
    if (!meta) return
    setState(s => ({ ...s, streaming: true }))
    try {
      const data = await guidedPost('/learning/guided/answer', {
        workspace_id: meta.workspaceId,
        answer: answer ?? '',
        choice_index: choiceIndex ?? null,
        workflow_run_id: meta.workflowRunId ?? null,
      })
      emitGuidedWorkflowUpdate(data.workflow_run)
      const grade: GuidedGrade = {
        correct: Boolean(data.grade?.correct),
        feedback: String(data.grade?.feedback ?? ''),
        remediation: String(data.grade?.remediation ?? ''),
        grader: data.grade?.grader ? String(data.grade.grader) : undefined,
        choiceIndex,
      }
      // Stamp the grade onto the quiz card that was answered.
      setState(s => ({
        ...s,
        messages: s.messages.map(m =>
          m.id === messageId && m.guru?.kind === 'step'
            ? { ...m, guru: { ...m.guru, grade, answered: grade.correct || m.guru.step.quiz?.type === 'mcq' } }
            : m
        ),
      }))
      if (data.advanced && data.step) {
        const step = data.step as GuidedStep
        if (step.kind === 'complete') setGuided(null)
        appendMessages([stepToMessage(step)])
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Grading failed — try again.'
      setState(s => ({ ...s, error: message }))
    } finally {
      setState(s => ({ ...s, streaming: false }))
    }
  }, [appendMessages, guidedPost, setGuided, stepToMessage])

  const skipGuided = useCallback(async () => {
    const meta = guidedRef.current
    if (!meta) return
    setState(s => ({ ...s, streaming: true }))
    try {
      const data = await guidedPost('/learning/guided/skip', { workspace_id: meta.workspaceId, workflow_run_id: meta.workflowRunId ?? null })
      emitGuidedWorkflowUpdate(data.workflow_run)
      const step = data.step as GuidedStep
      if (step.kind === 'complete') setGuided(null)
      appendMessages([stepToMessage(step)])
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Could not skip.'
      setState(s => ({ ...s, error: message }))
    } finally {
      setState(s => ({ ...s, streaming: false }))
    }
  }, [appendMessages, guidedPost, setGuided, stepToMessage])

  const exitGuided = useCallback(async (rawQuery?: string) => {
    const meta = guidedRef.current
    if (!meta) return
    if (rawQuery) {
      appendMessages([{ id: crypto.randomUUID(), role: 'user', text: rawQuery }])
    }
    try {
      const data = await guidedPost('/learning/guided/exit', { workspace_id: meta.workspaceId, workflow_run_id: meta.workflowRunId ?? null })
      emitGuidedWorkflowUpdate(data.workflow_run)
      const message = String(data.message ?? 'Teaching workflow closed.')
      appendMessages([{
        id: crypto.randomUUID(),
        role: 'assistant',
        text: message,
        avatarsInvolved: ['Krishna'],
        guru: { kind: 'exit', message, progress: data.progress as GuidedProgress | undefined },
      }])
    } catch {
      // Exit must never trap the user — clear locally even if the server call failed.
    } finally {
      setGuided(null)
    }
  }, [appendMessages, guidedPost, setGuided])

  const send = useCallback(async (query: string, attachments: ChatAttachment[] = [], options: SendOptions = {}) => {
    if ((!query.trim() && attachments.length === 0) || state.streaming) return

    const resolvedQuery = query.trim() || 'Review the attached inputs and summarize what matters.'

    // G7: slash commands route to the guided-mode state machine, not /chat.
    const guidedCmd = attachments.length === 0 ? parseGuidedCommand(resolvedQuery) : null
    if (guidedCmd?.action === 'teach') {
      await startGuided(resolvedQuery, guidedCmd.topic, 'teach', options.workflowRunId)
      return
    }
    if (guidedCmd?.action === 'teach-empty') {
      appendMessages([
        { id: crypto.randomUUID(), role: 'user', text: query.trim() },
        {
          id: crypto.randomUUID(),
          role: 'assistant',
          text: 'Tell me what to teach — for example: /teach me virtual memory',
          avatarsInvolved: ['Krishna'],
          guru: { kind: 'info', message: 'Tell me what to teach — for example: /teach me virtual memory' },
        },
      ])
      return
    }
    if (guidedCmd?.action === 'exit' && guidedRef.current) {
      await exitGuided(query.trim())
      return
    }

    // Append user message
    const userMsg: Message = {
      id: crypto.randomUUID(),
      role: 'user',
      text: resolvedQuery,
      attachments,
    }
    sessionAvatarsRef.current = []
    synthRef.current = ''
    msgIdRef.current = crypto.randomUUID()
    synthStartRef.current = null  // set when the answer's first text shows, not send()
    msgUsageRef.current = null
    resetLiveStreams()

    setState(s => ({
      ...s,
      messages: [...s.messages, userMsg],
      avatars: initialAvatars(),
      naradActive: true,
      streaming: true,
      error: null,
      currentSession: null,
      stepEvents: [],
      andonAlert: null,
      liveAnswer: null,
    }))

    // Terminal-event flag shared by the initial stream and any re-attached
    // stream: 'done' and 'error' both mark the turn as finished.
    let gotTerminal = false
    const turnSessionId = convoSessionId.current

    const consumeStream = async (body: ReadableStream<Uint8Array>) => {
      const reader = body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })

        // SSE lines: "data: {...}\n\n"
        const lines = buffer.split('\n')
        buffer = lines.pop() ?? ''

        for (const line of lines) {
          const trimmed = line.trim()
          if (!trimmed.startsWith('data:')) continue
          const raw = trimmed.slice(5).trim()
          if (!raw) continue

          let evt: { type: string; data: Record<string, unknown> }
          try { evt = JSON.parse(raw) } catch { continue }

          switch (evt.type) {
            case 'step_event': {
              const d = evt.data as { avatar: string; discipline?: string; kind: string; tool?: string; preview: string }
              const step: StepEvent = {
                id: crypto.randomUUID(),
                avatar: d.avatar,
                discipline: d.discipline,
                kind: d.kind as StepEvent['kind'],
                tool: d.tool,
                preview: d.preview,
                ts: Date.now(),
              }
              setState(s => ({ ...s, stepEvents: [...s.stepEvents, step].slice(-200) }))
              break
            }

            case 'avatar_start': {
              const avatar = evt.data.avatar as AvatarName
              const task = evt.data.task as string
              const discipline = evt.data.discipline as string | undefined
              const routeStep: StepEvent = {
                id: crypto.randomUUID(),
                avatar: 'narad',
                kind: 'text',
                discipline,
                preview: `→ routing to ${avatar}${discipline ? ` (${discipline})` : ''}: ${task.slice(0, 120)}${task.length > 120 ? '…' : ''}`,
                ts: Date.now(),
              }
              setState(s => ({
                ...s,
                naradActive: false,
                avatars: {
                  ...s.avatars,
                  [avatar]: { name: avatar, state: 'active', discipline, task, startedAt: Date.now() },
                },
                stepEvents: [...s.stepEvents, routeStep].slice(-200),
              }))
              break
            }

            case 'route': {
              // The pre-router sent this turn straight to its owner (no routing call).
              const avatar = String(evt.data.avatar ?? '')
              const reason = String(evt.data.reason ?? '').replace(/_/g, ' ')
              const routeStep: StepEvent = {
                id: crypto.randomUUID(),
                avatar: 'narad',
                kind: 'text',
                preview: `→ direct to ${avatar}${reason ? ` · ${reason}` : ''}`,
                ts: Date.now(),
              }
              setState(s => ({ ...s, stepEvents: [...s.stepEvents, routeStep].slice(-200) }))
              break
            }

            case 'text_delta': {
              const source = String(evt.data.source ?? 'narad')
              const streams = liveStreamsRef.current
              let entry = streams.sources[source]
              if (!entry) {
                entry = { text: '', handoff: false }
                streams.sources[source] = entry
                streams.order.push(source)
              }
              entry.text += String(evt.data.text ?? '')
              entry.handoff = source === 'narad' || Boolean(evt.data.handoff)
              if (synthStartRef.current === null && pickLiveAnswer(streams, sessionAvatarsRef.current)) {
                synthStartRef.current = Date.now()
              }
              scheduleLiveRender()
              break
            }

            case 'text_reset': {
              // Routing chatter or a retried attempt: drop that source's text.
              const source = String(evt.data.source ?? 'narad')
              const streams = liveStreamsRef.current
              delete streams.sources[source]
              streams.order = streams.order.filter(item => item !== source)
              scheduleLiveRender()
              break
            }

            case 'thread_restored': {
              const turnCount = Number(evt.data.turn_count ?? 0)
              const lastTraceSessionId = evt.data.last_trace_session_id as string | undefined
              const threadSummary = evt.data.thread_summary as string | undefined
              const crossThread = Boolean(evt.data.cross_thread)
              const sourceSessions = Array.isArray(evt.data.source_sessions)
                ? evt.data.source_sessions as string[]
                : []
              const preview = crossThread
                ? `recovered continuity from ${sourceSessions.length || 1} recent session${sourceSessions.length === 1 ? '' : 's'}`
                : turnCount > 0
                ? `restored ${turnCount} prior turn${turnCount === 1 ? '' : 's'}${lastTraceSessionId ? ` · trace ${lastTraceSessionId}` : ''}`
                : 'restored prior session state'
              toast('Session restored', {
                description: preview,
                duration: 3500,
              })
              setState(s => ({
                ...s,
                stepEvents: [
                  ...s.stepEvents,
                  {
                    id: crypto.randomUUID(),
                    avatar: 'smriti',
                    kind: 'text' as const,
                    preview: threadSummary
                      ? `${preview} · ${threadSummary.slice(0, 120)}${threadSummary.length > 120 ? '…' : ''}`
                      : preview,
                    ts: Date.now(),
                  } satisfies StepEvent,
                ].slice(-200),
              }))
              break
            }

            case 'context_budget': {
              const model = String(evt.data.model ?? evt.data.selected_model ?? '')
              const predicted = Number(evt.data.predicted_input_tokens ?? 0)
              const hard = Number(evt.data.hard_input_budget_tokens ?? 0)
              const preview = model
                ? `context budget · ${model} · ${predicted.toLocaleString()} / ${hard.toLocaleString()} tok`
                : `context budget · ${predicted.toLocaleString()} / ${hard.toLocaleString()} tok`
              setState(s => ({
                ...s,
                stepEvents: [
                  ...s.stepEvents,
                  {
                    id: crypto.randomUUID(),
                    avatar: 'smriti',
                    kind: 'text' as const,
                    preview,
                    ts: Date.now(),
                  } satisfies StepEvent,
                ].slice(-200),
              }))
              break
            }

            case 'context_compacted': {
              const reasons = Array.isArray(evt.data.reasons) ? evt.data.reasons as string[] : []
              const compactedFrom = Number(evt.data.compacted_from_tokens ?? 0)
              const preview = reasons.length > 0
                ? `context compacted · ${reasons.join(', ')}${compactedFrom ? ` · from ${compactedFrom.toLocaleString()} tok` : ''}`
                : 'context compacted to fit model budget'
              toast('Context compacted', {
                description: preview,
                duration: 3200,
              })
              setState(s => ({
                ...s,
                stepEvents: [
                  ...s.stepEvents,
                  {
                    id: crypto.randomUUID(),
                    avatar: 'smriti',
                    kind: 'text' as const,
                    preview,
                    ts: Date.now(),
                  } satisfies StepEvent,
                ].slice(-200),
              }))
              break
            }

            case 'context_escalated': {
              const fromModel = String(evt.data.from_model ?? '')
              const toModel = String(evt.data.to_model ?? '')
              const preview = fromModel && toModel
                ? `context escalated · ${fromModel} → ${toModel}`
                : 'context escalated to a larger window model'
              toast('Model escalated', {
                description: preview,
                duration: 3500,
              })
              setState(s => ({
                ...s,
                stepEvents: [
                  ...s.stepEvents,
                  {
                    id: crypto.randomUUID(),
                    avatar: 'narad',
                    kind: 'text' as const,
                    preview,
                    ts: Date.now(),
                  } satisfies StepEvent,
                ].slice(-200),
              }))
              break
            }

            case 'avatar_done': {
              const avatar = evt.data.avatar as AvatarName
              const discipline = evt.data.discipline as string | undefined
              sessionAvatarsRef.current = [...sessionAvatarsRef.current, avatar]
              setState(s => {
                const prev = s.avatars[avatar]
                const latencyMs = prev?.startedAt ? Date.now() - prev.startedAt : undefined
                if (latencyMs !== undefined) {
                  toast(`${avatar} done`, {
                    description: `${(latencyMs / 1000).toFixed(1)}s`,
                    duration: 2500,
                  })
                }
                const doneStep: StepEvent = {
                  id: crypto.randomUUID(),
                  avatar,
                  kind: 'text',
                  discipline,
                  preview: `✓ completed${latencyMs != null ? ` in ${(latencyMs / 1000).toFixed(1)}s` : ''}`,
                  ts: Date.now(),
                }
                return {
                  ...s,
                  avatars: {
                  ...s.avatars,
                    [avatar]: { name: avatar, state: 'done', discipline: discipline ?? prev?.discipline, task: prev?.task, latencyMs },
                  },
                  stepEvents: [...s.stepEvents, doneStep].slice(-200),
                }
              })
              break
            }

            case 'narad_synthesis': {
              // The complete reply replaces the streamed text in the same render.
              const chunk = evt.data.text as string
              if (synthStartRef.current === null) synthStartRef.current = Date.now()
              resetLiveStreams()
              synthRef.current += chunk
              const captured = synthRef.current
              const id = msgIdRef.current
              setState(s => {
                const existing = s.messages.find(m => m.id === id)
                if (existing) {
                  return {
                    ...s,
                    liveAnswer: null,
                    messages: s.messages.map(m =>
                      m.id === id ? { ...m, text: captured } : m
                    ),
                  }
                }
                const assistantMsg: Message = {
                  id,
                  role: 'assistant',
                  text: captured,
                  avatarsInvolved: sessionAvatarsRef.current,
                }
                return { ...s, liveAnswer: null, messages: [...s.messages, assistantMsg] }
              })
              break
            }

            case 'usage': {
              // Store raw token counts only — timing (tokPerSec, synthDurationMs)
              // is computed in the `done` handler when synthesis is definitively complete.
              const d = evt.data as { prompt_tokens: number; completion_tokens: number; total_tokens: number; cost_usd?: number }
              const usage: TokenUsage = {
                promptTokens:     d.prompt_tokens,
                completionTokens: d.completion_tokens,
                totalTokens:      d.total_tokens,
                costUsd:          d.cost_usd ?? 0,
              }
              msgUsageRef.current = usage
              setState(s => ({
                ...s,
                sessionTotals: {
                  promptTokens:     s.sessionTotals.promptTokens     + d.prompt_tokens,
                  completionTokens: s.sessionTotals.completionTokens + d.completion_tokens,
                  totalTokens:      s.sessionTotals.totalTokens      + d.total_tokens,
                  costUsd:          s.sessionTotals.costUsd          + (d.cost_usd ?? 0),
                },
                messages: s.messages.map(m =>
                  m.id === msgIdRef.current ? { ...m, usage } : m
                ),
              }))
              break
            }

            case 'done': {
              gotTerminal = true
              const sessionId = evt.data.session_id as string
              const tokenEstimate = Math.ceil(synthRef.current.length / 4)
              // Synthesis duration: first chunk → done. This is the correct window
              // for timing because `usage` now only fires on the final response event
              // (after synthesis has already started).
              const synthDurationMs = synthStartRef.current
                ? Date.now() - synthStartRef.current
                : undefined
              const totalDurationMs = synthDurationMs
              const turnUsage = msgUsageRef.current
              const completionToks = turnUsage?.completionTokens
                ?? (synthDurationMs ? tokenEstimate : 0)
              const tokPerSec = synthDurationMs && synthDurationMs > 100 && completionToks > 0
                ? Math.round(completionToks / (synthDurationMs / 1000))
                : undefined
              const clientTokPerSec = tokPerSec
              const finalUsage: TokenUsage | undefined = turnUsage
                ? { ...turnUsage, tokPerSec, synthDurationMs }
                : undefined
              resetLiveStreams()

              setState(s => {
                // Snapshot avatar wall-clock latencies before avatars state resets next turn
                const avatarLatencies: Record<string, number> = Object.fromEntries(
                  Object.entries(s.avatars)
                    .filter(([, av]) => av.latencyMs != null && av.state === 'done')
                    .map(([name, av]) => [name, av.latencyMs!])
                )
                const session: SessionInfo = {
                  sessionId,
                  avatarsFired:     [...sessionAvatarsRef.current],
                  totalTokens:      finalUsage?.totalTokens      ?? tokenEstimate,
                  promptTokens:     finalUsage?.promptTokens     ?? 0,
                  completionTokens: finalUsage?.completionTokens ?? 0,
                  tokPerSec,
                  totalMs:          totalDurationMs,
                }
                return {
                  ...s,
                  streaming: false,
                  naradActive: false,
                  liveAnswer: null,
                  currentSession: session,
                  messages: s.messages.map(m =>
                    m.id === msgIdRef.current
                      ? {
                          ...m,
                          avatarsInvolved: sessionAvatarsRef.current,
                          sessionId,
                          tokenEstimate,
                          totalDurationMs,
                          clientTokPerSec,
                          usage:           finalUsage ?? m.usage,
                          avatarLatencies: Object.keys(avatarLatencies).length > 0
                                             ? avatarLatencies : m.avatarLatencies,
                        }
                      : m
                  ),
                }
              })
              convoSessionId.current = sessionId
              writeStorage(conversationStorageKey, sessionId)
              break
            }

            case 'artifact_opened':
            case 'artifact_updated': {
              const artifactSession = toActiveArtifactSession(evt.data)
              if (!artifactSession) break
              setState(s => ({
                ...s,
                activeArtifactSession: artifactSession,
                pendingToolUi: null,
              }))
              break
            }

            case 'artifact_closed': {
              setState(s => ({
                ...s,
                activeArtifactSession: null,
              }))
              break
            }

            case 'tool_ui': {
              const d = evt.data as {
                avatar: string
                tool: string
                payload?: {
                  status?: string
                  summary?: string
                  requires_confirmation?: boolean
                  artifacts?: ToolArtifact[]
                  citations?: ToolCitation[]
                  ui?: PendingToolUi['ui']
                }
              }
              setState(s => ({
                ...s,
                pendingToolUi: {
                  avatar: d.avatar,
                  tool: d.tool,
                  status: d.payload?.status ?? 'ok',
                  summary: d.payload?.summary ?? '',
                  requiresConfirmation: d.payload?.requires_confirmation ?? false,
                  artifacts: Array.isArray(d.payload?.artifacts) ? d.payload.artifacts : [],
                  citations: Array.isArray(d.payload?.citations) ? d.payload.citations : [],
                  ui: d.payload?.ui ?? null,
                },
              }))
              break
            }

            case 'workflow_updated': {
              emitWorkflowRuntimeEvent(evt.type, evt.data)
              break
            }

            case 'approval_requested': {
              // A tool is waiting for this person's OK: show the card in the chat.
              const proposal = evt.data as unknown as ApprovalProposal
              if (!proposal?.id) break
              setState(s => ({ ...s, messages: upsertApprovalMessage(s.messages, proposal) }))
              break
            }

            case 'task_started':
            case 'task_updated': {
              // An errand now runs on the Mac by itself: show its card in the chat.
              const task = evt.data as unknown as KriyaTask
              if (!task?.id) break
              setState(s => ({ ...s, messages: upsertTaskMessage(s.messages, task) }))
              break
            }

            case 'andon_alert': {
              const d = evt.data as unknown as AndonAlertPayload
              setState(s => ({ ...s, andonAlert: d }))
              toast.warning(`Andon: ${d.avatar}`, {
                description: `${d.trigger}${d.task_preview ? ` — ${d.task_preview.slice(0, 60)}` : ''}`,
                duration: 8000,
              })
              break
            }

            case 'andon_diagnosis': {
              // Diagnosis arrives after andon_alert; no UI state needed beyond clearing alert
              setState(s => ({ ...s, andonAlert: null }))
              break
            }

            case 'error': {
              gotTerminal = true
              // Rotate session ID — the backend deleted the corrupt session,
              // so the old ID is dead. Next message gets a fresh session.
              convoSessionId.current = rotateConvoSessionId(userId)
              const errMsg = evt.data.message as string
              toast.error('Error', { description: errMsg, duration: 6000 })
              resetLiveStreams()
              setState(s => ({
                ...s,
                streaming: false,
                naradActive: false,
                liveAnswer: null,
                error: errMsg,
                avatars: initialAvatars(),
              }))
              break
            }
          }
        }
      }
    }

    // Recover the finished answer from the persisted thread (used when the
    // run completed while the phone was locked / the app was backgrounded).
    const hydrateFromThread = async (): Promise<boolean> => {
      try {
        const response = await apiFetch(apiUrl(`/thread/${turnSessionId}`, { user_id: userId }))
        if (!response.ok) return false
        const data = await response.json() as {
          turns?: StoredThreadTurn[]
        }
        const turns = Array.isArray(data.turns) ? data.turns : []
        const last = turns[turns.length - 1]
        // Only counts as recovery if the thread ends with an assistant answer
        // to *this* user turn (our query is the preceding user message).
        if (!last || last.role !== 'assistant') return false
        const prevUser = turns[turns.length - 2]
        if (!prevUser || prevUser.role !== 'user' || prevUser.text.trim() !== resolvedQuery) return false

        const id = msgIdRef.current
        setState(s => {
          const existing = s.messages.find(m => m.id === id)
          const messages = existing
            ? s.messages.map(m => m.id === id ? { ...m, text: last.text, sessionId: turnSessionId } : m)
            : [...s.messages, { id, role: 'assistant' as const, text: last.text, avatarsInvolved: sessionAvatarsRef.current, sessionId: turnSessionId }]
          return { ...s, messages, streaming: false, naradActive: false }
        })
        toast('Answer recovered', {
          description: 'The run finished while the connection was away.',
          duration: 3500,
        })
        return true
      } catch {
        return false
      }
    }

    const sleep = (ms: number) => new Promise<void>(resolve => setTimeout(resolve, ms))

    try {
      abortRef.current = new AbortController()
      const res = await apiFetch(apiPath('/chat'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          query: resolvedQuery,
          user_id: userId,
          session_id: convoSessionId.current,
          attachment_ids: attachments.map(item => item.attachment_id),
          active_artifact_id: state.activeArtifactSession?.artifactId ?? null,
          active_artifact_workspace_id: state.activeArtifactSession?.workspaceId ?? null,
          active_artifact_type: state.activeArtifactSession?.artifactType ?? null,
          workflow_run_id: options.workflowRunId ?? null,
          reply_language: options.replyLanguage ?? null,
        }),
        signal: abortRef.current.signal,
      })

      if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`)
      await consumeStream(res.body)
    } catch (err) {
      if (err instanceof Error && err.name === 'AbortError') {
        setState(s => ({ ...s, streaming: false, naradActive: false, liveAnswer: null, avatars: initialAvatars() }))
        return
      }
      // fall through to the re-attach loop below
    }

    if (gotTerminal) return

    // The stream died without a terminal event (screen lock, network blip).
    // The backend keeps the run alive in _active_tasks — try to re-attach;
    // if the run already finished, recover the answer from the thread.
    let lastErr: string | null = null
    for (let attempt = 0; attempt < 3 && !gotTerminal; attempt++) {
      if (abortRef.current?.signal.aborted) {
        setState(s => ({ ...s, streaming: false, naradActive: false, liveAnswer: null, avatars: initialAvatars() }))
        return
      }
      await sleep(800 * (attempt + 1))
      try {
        const attach = await apiFetch(apiPath(`/chat/attach/${turnSessionId}`), {
          signal: abortRef.current?.signal,
        })
        if (attach.ok && attach.body) {
          await consumeStream(attach.body)
          continue // stream ended — loop re-checks gotTerminal
        }
        if (attach.status === 404) {
          // No active run: either it finished while we were away, or it never
          // started. The thread tells us which.
          if (await hydrateFromThread()) return
          lastErr = 'Connection lost before the run could finish.'
          break
        }
        lastErr = `Re-attach failed (HTTP ${attach.status})`
      } catch (err) {
        if (err instanceof Error && err.name === 'AbortError') {
          setState(s => ({ ...s, streaming: false, naradActive: false, liveAnswer: null, avatars: initialAvatars() }))
          return
        }
        lastErr = err instanceof Error ? err.message : 'Unknown error'
      }
    }

    if (!gotTerminal) {
      setState(s => ({
        ...s,
        streaming: false,
        naradActive: false,
        avatars: initialAvatars(),
        error: lastErr ?? 'Connection lost — check that the server is reachable.',
      }))
    }
  }, [state.streaming, state.activeArtifactSession, userId, startGuided, exitGuided, appendMessages, resetLiveStreams, scheduleLiveRender])

  const clearArtifact = useCallback(() => {
    setState(s => ({ ...s, activeArtifactSession: null }))
  }, [])

  const clearToolUi = useCallback(() => {
    setState(s => ({ ...s, pendingToolUi: null }))
  }, [])

  const clearAndon = useCallback(() => {
    setState(s => ({ ...s, andonAlert: null }))
  }, [])

  /** A card's decision (or an edit that replaced it) updates the chat's copy. */
  const updateApproval = useCallback((proposal: ApprovalProposal, replaces?: string) => {
    setState(s => ({ ...s, messages: upsertApprovalMessage(s.messages, proposal, { replaces, append: false }) }))
  }, [])

  const resumeSession = useCallback(async (sessionId: string) => {
    try {
      const response = await apiFetch(apiUrl(`/thread/${sessionId}`, { user_id: userId }))
      if (!response.ok) return false
      const data = await response.json() as {
        turns?: StoredThreadTurn[]
        thread_summary?: string
        working_state?: Record<string, unknown> | null
      }
      const turns = Array.isArray(data.turns) ? data.turns : []
      const restoredMessages: Message[] = turns.map((turn, index) => ({
        id: `${sessionId}-${index}`,
        role: turn.role,
        text: turn.text,
        sessionId,
        attachments: storedTurnAttachments(turn),
      }))
      convoSessionId.current = sessionId
      writeStorage(conversationStorageKey, sessionId)
      writeStorage(messageStorageKey, JSON.stringify(restoredMessages))
      setState(s => ({
        ...s,
        messages: restoredMessages,
        avatars: initialAvatars(),
        naradActive: false,
        streaming: false,
        currentSession: {
          sessionId,
          avatarsFired: s.currentSession?.avatarsFired ?? [],
        },
        error: null,
        activeArtifactSession: toActiveArtifactSession(data.working_state),
        pendingToolUi: null,
        stepEvents: data.thread_summary
          ? [{
              id: crypto.randomUUID(),
              avatar: 'smriti',
              kind: 'text',
              preview: `resumed session · ${data.thread_summary.slice(0, 120)}${data.thread_summary.length > 120 ? '…' : ''}`,
              ts: Date.now(),
            }]
          : [],
      }))
      toast('Session resumed', {
        description: turns.length > 0 ? `${turns.length} turns restored` : 'Working-state branch restored',
        duration: 3500,
      })
      return true
    } catch {
      return false
    }
  }, [userId])

  const clearSession = useCallback(() => {
    const previousSessionId = convoSessionId.current
    removeStorage(messageStorageKey)
    removeStorage(conversationStorageKey)
    removeStorage(guidedStorageKey)
    guidedRef.current = null
    if (previousSessionId) {
      apiFetch(apiUrl(`/thread/${previousSessionId}`, { user_id: userId }), { method: 'DELETE' }).catch(() => {})
    }
    convoSessionId.current = crypto.randomUUID()
    writeStorage(conversationStorageKey, convoSessionId.current)
    setState(s => ({
      ...s,
      messages:       [],
      stepEvents:     [],
      currentSession: null,
      error:          null,
      avatars:        initialAvatars(),
      pendingToolUi:  null,
      activeArtifactSession: null,
      guidedSession:  null,
    }))
  }, [userId])

  return {
    ...state, send, stop, clearArtifact, clearToolUi, clearAndon, clearSession, resumeSession,
    answerGuided, skipGuided, exitGuided, updateApproval,
  }
}
