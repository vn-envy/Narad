import { useState, useCallback, useRef, useEffect } from 'react'
import { toast } from 'sonner'
import { apiPath, apiUrl, apiFetch } from '@/lib/api'

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

export interface Message {
  id: string
  role: 'user' | 'assistant'
  text: string
  avatarsInvolved?: AvatarName[]
  sessionId?: string
  tokenEstimate?: number
  totalDurationMs?: number
  clientTokPerSec?: number
  usage?: TokenUsage
  avatarUsage?: Record<string, TokenUsage>
  avatarLatencies?: Record<string, number>
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

export interface KanbanUpdatePayload {
  session_id: string
  columns: Record<string, unknown[]>
  total: number
  done_count: number
  blocked_count: number
}

export interface AndonAlertPayload {
  avatar: string
  trigger: string
  task_preview?: string
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
  kanbanUpdate: KanbanUpdatePayload | null
  andonAlert: AndonAlertPayload | null
}

const AVATAR_NAMES: AvatarName[] = ['Matsya', 'Rama', 'Krishna', 'Parashurama']

function initialAvatars(): Record<AvatarName, AvatarStatus> {
  return Object.fromEntries(
    AVATAR_NAMES.map(name => [name, { name, state: 'idle' as AvatarState }])
  ) as Record<AvatarName, AvatarStatus>
}

const SESSION_KEY = 'avatara_messages'
const CONVO_SESSION_KEY = 'avatara_convo_session_id'

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

function emitKarmaRuntimeEvent(type: string, data: Record<string, unknown>): void {
  if (typeof window === 'undefined') return
  window.dispatchEvent(new CustomEvent('narad:karma-event', {
    detail: { type, data, ts: Date.now() },
  }))
}

function loadMessages(): Message[] {
  try {
    const raw = readStorage(SESSION_KEY)
    return raw ? (JSON.parse(raw) as Message[]) : []
  } catch {
    return []
  }
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
function getOrCreateConvoSessionId(): string {
  try {
    const existing = readStorage(CONVO_SESSION_KEY)
    if (existing) return existing
    const id = crypto.randomUUID()
    writeStorage(CONVO_SESSION_KEY, id)
    return id
  } catch {
    return crypto.randomUUID()
  }
}

// Called after a backend error — rotates the session ID so we don't keep
// hitting a server-side session that was deleted due to corruption.
function rotateConvoSessionId(): string {
  const id = crypto.randomUUID()
  writeStorage(CONVO_SESSION_KEY, id)
  return id
}

export function useAvatara(userId = 'default') {
  const initialMessages = loadMessages()
  const initialSessionId = getOrCreateConvoSessionId()
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
    kanbanUpdate: null,
    andonAlert: null,
  })

  // Set to Date.now() on the FIRST narad_synthesis chunk — intentionally excludes
  // all avatar/tool call time so tok/sec reflects only LLM generation speed.
  const synthStartRef = useRef<number | null>(null)
  // Per-message token usage captured from the `usage` SSE event (fires before `done`)
  const msgUsageRef = useRef<TokenUsage | null>(null)
  const sessionAvatarsRef = useRef<AvatarName[]>([])
  const synthRef = useRef('')
  const msgIdRef = useRef('')
  const convoSessionId = useRef(fallbackSessionId ?? initialSessionId)
  const abortRef = useRef<AbortController | null>(null)

  useEffect(() => {
    writeStorage(CONVO_SESSION_KEY, convoSessionId.current)
  }, [])

  // Persist messages to sessionStorage whenever they change
  useEffect(() => {
    try {
      writeStorage(SESSION_KEY, JSON.stringify(state.messages))
    } catch {
      // storage unavailable — silent fail
    }
  }, [state.messages])

  useEffect(() => {
    let cancelled = false
    const hydrateThread = (
      sessionId: string,
      turns: Array<{ role: 'user' | 'assistant'; text: string }>,
      workingState?: Record<string, unknown> | null,
    ) => {
      if (cancelled || turns.length === 0) return
      convoSessionId.current = sessionId
      writeStorage(CONVO_SESSION_KEY, sessionId)
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
            .then((data: { turns?: Array<{ role: 'user' | 'assistant'; text: string }>; working_state?: Record<string, unknown> | null } | null) => {
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
        .then((data: { turns?: Array<{ role: 'user' | 'assistant'; text: string }>; working_state?: Record<string, unknown> | null } | null) => {
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

  const send = useCallback(async (query: string, images: string[] = []) => {
    if (!query.trim() || state.streaming) return

    // Append user message
    const userMsg: Message = { id: crypto.randomUUID(), role: 'user', text: query }
    sessionAvatarsRef.current = []
    synthRef.current = ''
    msgIdRef.current = crypto.randomUUID()
    synthStartRef.current = null  // set on first narad_synthesis chunk, not send()
    msgUsageRef.current = null

    setState(s => ({
      ...s,
      messages: [...s.messages, userMsg],
      avatars: initialAvatars(),
      naradActive: true,
      streaming: true,
      error: null,
      currentSession: null,
      stepEvents: [],
      kanbanUpdate: null,
      andonAlert: null,
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

            case 'thread_restored': {
              emitKarmaRuntimeEvent(evt.type, evt.data)
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
              emitKarmaRuntimeEvent(evt.type, evt.data)
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
              const chunk = evt.data.text as string
              if (synthStartRef.current === null) synthStartRef.current = Date.now()
              synthRef.current += chunk
              const captured = synthRef.current
              const id = msgIdRef.current
              setState(s => {
                const existing = s.messages.find(m => m.id === id)
                if (existing) {
                  return {
                    ...s,
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
                return { ...s, messages: [...s.messages, assistantMsg] }
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
              emitKarmaRuntimeEvent(evt.type, evt.data)
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
              writeStorage(CONVO_SESSION_KEY, sessionId)
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

            case 'kanban_update': {
              const d = evt.data as unknown as KanbanUpdatePayload
              emitKarmaRuntimeEvent(evt.type, evt.data)
              setState(s => ({ ...s, kanbanUpdate: d }))
              break
            }

            case 'project_state_changed':
            case 'task_state_changed':
            case 'execution_state_changed': {
              emitKarmaRuntimeEvent(evt.type, evt.data)
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
              convoSessionId.current = rotateConvoSessionId()
              const errMsg = evt.data.message as string
              toast.error('Error', { description: errMsg, duration: 6000 })
              setState(s => ({
                ...s,
                streaming: false,
                naradActive: false,
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
          turns?: Array<{ role: 'user' | 'assistant'; text: string }>
        }
        const turns = Array.isArray(data.turns) ? data.turns : []
        const last = turns[turns.length - 1]
        // Only counts as recovery if the thread ends with an assistant answer
        // to *this* user turn (our query is the preceding user message).
        if (!last || last.role !== 'assistant') return false
        const prevUser = turns[turns.length - 2]
        if (!prevUser || prevUser.role !== 'user' || prevUser.text.trim() !== query.trim()) return false

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
      const res = await fetch(apiPath('/chat'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          query,
          user_id: userId,
          session_id: convoSessionId.current,
          images,
          active_artifact_id: state.activeArtifactSession?.artifactId ?? null,
          active_artifact_workspace_id: state.activeArtifactSession?.workspaceId ?? null,
          active_artifact_type: state.activeArtifactSession?.artifactType ?? null,
        }),
        signal: abortRef.current.signal,
      })

      if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`)
      await consumeStream(res.body)
    } catch (err) {
      if (err instanceof Error && err.name === 'AbortError') {
        setState(s => ({ ...s, streaming: false, naradActive: false, avatars: initialAvatars() }))
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
        setState(s => ({ ...s, streaming: false, naradActive: false, avatars: initialAvatars() }))
        return
      }
      await sleep(800 * (attempt + 1))
      try {
        const attach = await fetch(apiPath(`/chat/attach/${turnSessionId}`), {
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
          setState(s => ({ ...s, streaming: false, naradActive: false, avatars: initialAvatars() }))
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
  }, [state.streaming, state.activeArtifactSession, userId])

  const clearArtifact = useCallback(() => {
    setState(s => ({ ...s, activeArtifactSession: null }))
  }, [])

  const clearToolUi = useCallback(() => {
    setState(s => ({ ...s, pendingToolUi: null }))
  }, [])

  const clearAndon = useCallback(() => {
    setState(s => ({ ...s, andonAlert: null }))
  }, [])

  const resumeSession = useCallback(async (sessionId: string) => {
    try {
      const response = await apiFetch(apiUrl(`/thread/${sessionId}`, { user_id: userId }))
      if (!response.ok) return false
      const data = await response.json() as {
        turns?: Array<{ role: 'user' | 'assistant'; text: string }>
        thread_summary?: string
        working_state?: Record<string, unknown> | null
      }
      const turns = Array.isArray(data.turns) ? data.turns : []
      const restoredMessages: Message[] = turns.map((turn, index) => ({
        id: `${sessionId}-${index}`,
        role: turn.role,
        text: turn.text,
        sessionId,
      }))
      convoSessionId.current = sessionId
      writeStorage(CONVO_SESSION_KEY, sessionId)
      writeStorage(SESSION_KEY, JSON.stringify(restoredMessages))
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
    removeStorage(SESSION_KEY)
    removeStorage(CONVO_SESSION_KEY)
    if (previousSessionId) {
      apiFetch(apiUrl(`/thread/${previousSessionId}`, { user_id: userId }), { method: 'DELETE' }).catch(() => {})
    }
    convoSessionId.current = crypto.randomUUID()
    writeStorage(CONVO_SESSION_KEY, convoSessionId.current)
    setState(s => ({
      ...s,
      messages:       [],
      stepEvents:     [],
      currentSession: null,
      error:          null,
      avatars:        initialAvatars(),
      pendingToolUi:  null,
      activeArtifactSession: null,
    }))
  }, [userId])

  return { ...state, send, stop, clearArtifact, clearToolUi, clearAndon, clearSession, resumeSession }
}
