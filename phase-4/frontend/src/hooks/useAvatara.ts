import { useState, useCallback, useRef, useEffect } from 'react'
import { toast } from 'sonner'

export type AvatarName =
  | 'Matsya' | 'Varaha' | 'Narasimha' | 'Rama'
  | 'Krishna' | 'Buddha' | 'Parashurama' | 'Vamana'

export type AvatarState = 'idle' | 'active' | 'done'

export interface AvatarStatus {
  name: AvatarName
  state: AvatarState
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
  tool?: string
  preview: string
  ts: number
}

export interface PendingArtifact {
  topic: string
  artifactType: 'flashcards' | 'diagram'
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
  sessionTotals: { promptTokens: number; completionTokens: number; totalTokens: number }
  pendingArtifact: PendingArtifact | null
  kanbanUpdate: KanbanUpdatePayload | null
  andonAlert: AndonAlertPayload | null
}

const AVATAR_NAMES: AvatarName[] = [
  'Matsya', 'Varaha', 'Narasimha', 'Rama', 'Krishna', 'Buddha', 'Parashurama', 'Vamana'
]

function initialAvatars(): Record<AvatarName, AvatarStatus> {
  return Object.fromEntries(
    AVATAR_NAMES.map(name => [name, { name, state: 'idle' as AvatarState }])
  ) as Record<AvatarName, AvatarStatus>
}

const SESSION_KEY = 'avatara_messages'
const CONVO_SESSION_KEY = 'avatara_convo_session_id'

function loadMessages(): Message[] {
  try {
    const raw = sessionStorage.getItem(SESSION_KEY)
    return raw ? (JSON.parse(raw) as Message[]) : []
  } catch {
    return []
  }
}

// One stable session ID for the whole browser session — reused across all messages
// so the backend's InMemorySessionService accumulates conversation history.
function getOrCreateConvoSessionId(): string {
  try {
    const existing = sessionStorage.getItem(CONVO_SESSION_KEY)
    if (existing) return existing
    const id = crypto.randomUUID()
    sessionStorage.setItem(CONVO_SESSION_KEY, id)
    return id
  } catch {
    return crypto.randomUUID()
  }
}

// Called after a backend error — rotates the session ID so we don't keep
// hitting a server-side session that was deleted due to corruption.
function rotateConvoSessionId(): string {
  const id = crypto.randomUUID()
  try { sessionStorage.setItem(CONVO_SESSION_KEY, id) } catch { /* ignore */ }
  return id
}

export function useAvatara(userId = 'default') {
  const [state, setState] = useState<AvatararState>({
    messages: loadMessages(),
    avatars: initialAvatars(),
    naradActive: false,
    currentSession: null,
    streaming: false,
    error: null,
    stepEvents: [],
    sessionTotals: { promptTokens: 0, completionTokens: 0, totalTokens: 0 },
    pendingArtifact: null,
    kanbanUpdate: null,
    andonAlert: null,
  })

  // Set to Date.now() on the FIRST narad_synthesis chunk — intentionally excludes
  // all avatar/tool call time so tok/sec reflects only LLM generation speed.
  const synthStartRef = useRef<number | null>(null)
  // Per-message token usage captured from the `usage` SSE event (fires before `done`)
  const msgUsageRef = useRef<TokenUsage | null>(null)

  // Persist messages to sessionStorage whenever they change
  useEffect(() => {
    try {
      sessionStorage.setItem(SESSION_KEY, JSON.stringify(state.messages))
    } catch {
      // sessionStorage full or unavailable — silent fail
    }
  }, [state.messages])

  const sessionAvatarsRef = useRef<AvatarName[]>([])
  const synthRef = useRef('')
  const msgIdRef = useRef('')
  const convoSessionId = useRef(getOrCreateConvoSessionId())
  const abortRef = useRef<AbortController | null>(null)

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
    }))

    try {
      abortRef.current = new AbortController()
      const res = await fetch('/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query, user_id: userId, session_id: convoSessionId.current, images }),
        signal: abortRef.current.signal,
      })

      if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`)

      const reader = res.body.getReader()
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
              const d = evt.data as { avatar: string; kind: string; tool?: string; preview: string }
              const step: StepEvent = {
                id: crypto.randomUUID(),
                avatar: d.avatar,
                kind: d.kind as StepEvent['kind'],
                tool: d.tool,
                preview: d.preview,
                ts: Date.now(),
              }
              setState(s => ({ ...s, stepEvents: [...s.stepEvents, step] }))
              break
            }

            case 'avatar_start': {
              const avatar = evt.data.avatar as AvatarName
              const task = evt.data.task as string
              const routeStep: StepEvent = {
                id: crypto.randomUUID(),
                avatar: 'narad',
                kind: 'text',
                preview: `→ routing to ${avatar}: ${task.slice(0, 120)}${task.length > 120 ? '…' : ''}`,
                ts: Date.now(),
              }
              setState(s => ({
                ...s,
                naradActive: false,
                avatars: {
                  ...s.avatars,
                  [avatar]: { name: avatar, state: 'active', task, startedAt: Date.now() },
                },
                stepEvents: [...s.stepEvents, routeStep],
              }))
              break
            }

            case 'avatar_done': {
              const avatar = evt.data.avatar as AvatarName
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
                  preview: `✓ completed${latencyMs != null ? ` in ${(latencyMs / 1000).toFixed(1)}s` : ''}`,
                  ts: Date.now(),
                }
                return {
                  ...s,
                  avatars: {
                    ...s.avatars,
                    [avatar]: { name: avatar, state: 'done', task: prev?.task, latencyMs },
                  },
                  stepEvents: [...s.stepEvents, doneStep],
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
              const d = evt.data as { prompt_tokens: number; completion_tokens: number; total_tokens: number }
              const usage: TokenUsage = {
                promptTokens:     d.prompt_tokens,
                completionTokens: d.completion_tokens,
                totalTokens:      d.total_tokens,
              }
              msgUsageRef.current = usage
              setState(s => ({
                ...s,
                sessionTotals: {
                  promptTokens:     s.sessionTotals.promptTokens     + d.prompt_tokens,
                  completionTokens: s.sessionTotals.completionTokens + d.completion_tokens,
                  totalTokens:      s.sessionTotals.totalTokens      + d.total_tokens,
                },
                messages: s.messages.map(m =>
                  m.id === msgIdRef.current ? { ...m, usage } : m
                ),
              }))
              break
            }

            case 'done': {
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
              break
            }

            case 'learning_artifact': {
              const d = evt.data as { topic: string; artifact_type: string }
              setState(s => ({
                ...s,
                pendingArtifact: {
                  topic: d.topic,
                  artifactType: d.artifact_type as 'flashcards' | 'diagram',
                },
              }))
              break
            }

            case 'kanban_update': {
              const d = evt.data as unknown as KanbanUpdatePayload
              setState(s => ({ ...s, kanbanUpdate: d }))
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
              }))
              break
            }
          }
        }
      }
    } catch (err) {
      if (err instanceof Error && err.name === 'AbortError') {
        setState(s => ({ ...s, streaming: false, naradActive: false }))
        return
      }
      setState(s => ({
        ...s,
        streaming: false,
        naradActive: false,
        error: err instanceof Error ? err.message : 'Unknown error',
      }))
    }
  }, [state.streaming, userId])

  const clearArtifact = useCallback(() => {
    setState(s => ({ ...s, pendingArtifact: null }))
  }, [])

  const clearAndon = useCallback(() => {
    setState(s => ({ ...s, andonAlert: null }))
  }, [])

  return { ...state, send, stop, clearArtifact, clearAndon }
}
