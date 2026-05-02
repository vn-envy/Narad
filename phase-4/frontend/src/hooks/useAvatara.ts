import { useState, useCallback, useRef } from 'react'

export type AvatarName =
  | 'Matsya' | 'Varaha' | 'Narasimha' | 'Rama'
  | 'Krishna' | 'Buddha' | 'Parashurama'

export type AvatarState = 'idle' | 'active' | 'done'

export interface AvatarStatus {
  name: AvatarName
  state: AvatarState
  task?: string
  latencyMs?: number
  startedAt?: number
}

export interface Message {
  id: string
  role: 'user' | 'assistant'
  text: string
  avatarsInvolved?: AvatarName[]
  sessionId?: string
}

export interface SessionInfo {
  sessionId: string
  avatarsFired: AvatarName[]
  totalMs?: number
}

interface AvatararState {
  messages: Message[]
  avatars: Record<AvatarName, AvatarStatus>
  naradActive: boolean
  currentSession: SessionInfo | null
  streaming: boolean
  error: string | null
}

const AVATAR_NAMES: AvatarName[] = [
  'Matsya', 'Varaha', 'Narasimha', 'Rama', 'Krishna', 'Buddha', 'Parashurama'
]

function initialAvatars(): Record<AvatarName, AvatarStatus> {
  return Object.fromEntries(
    AVATAR_NAMES.map(name => [name, { name, state: 'idle' as AvatarState }])
  ) as Record<AvatarName, AvatarStatus>
}

export function useAvatara(userId = 'default') {
  const [state, setState] = useState<AvatararState>({
    messages: [],
    avatars: initialAvatars(),
    naradActive: false,
    currentSession: null,
    streaming: false,
    error: null,
  })

  const sessionAvatarsRef = useRef<AvatarName[]>([])
  const synthRef = useRef('')
  const msgIdRef = useRef('')

  const send = useCallback(async (query: string) => {
    if (!query.trim() || state.streaming) return

    // Append user message
    const userMsg: Message = { id: crypto.randomUUID(), role: 'user', text: query }
    sessionAvatarsRef.current = []
    synthRef.current = ''
    msgIdRef.current = crypto.randomUUID()

    setState(s => ({
      ...s,
      messages: [...s.messages, userMsg],
      avatars: initialAvatars(),
      naradActive: true,
      streaming: true,
      error: null,
      currentSession: null,
    }))

    try {
      const res = await fetch('/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query, user_id: userId }),
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
            case 'avatar_start': {
              const avatar = evt.data.avatar as AvatarName
              const task = evt.data.task as string
              setState(s => ({
                ...s,
                naradActive: false,
                avatars: {
                  ...s.avatars,
                  [avatar]: { name: avatar, state: 'active', task, startedAt: Date.now() },
                },
              }))
              break
            }

            case 'avatar_done': {
              const avatar = evt.data.avatar as AvatarName
              sessionAvatarsRef.current = [...sessionAvatarsRef.current, avatar]
              setState(s => {
                const prev = s.avatars[avatar]
                const latencyMs = prev?.startedAt ? Date.now() - prev.startedAt : undefined
                return {
                  ...s,
                  avatars: {
                    ...s.avatars,
                    [avatar]: { name: avatar, state: 'done', task: prev?.task, latencyMs },
                  },
                }
              })
              break
            }

            case 'narad_synthesis': {
              const chunk = evt.data.text as string
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

            case 'done': {
              const sessionId = evt.data.session_id as string
              const session: SessionInfo = {
                sessionId,
                avatarsFired: [...sessionAvatarsRef.current],
              }
              setState(s => ({
                ...s,
                streaming: false,
                naradActive: false,
                currentSession: session,
                messages: s.messages.map(m =>
                  m.id === msgIdRef.current
                    ? { ...m, avatarsInvolved: sessionAvatarsRef.current, sessionId }
                    : m
                ),
              }))
              break
            }

            case 'error': {
              setState(s => ({
                ...s,
                streaming: false,
                naradActive: false,
                error: evt.data.message as string,
              }))
              break
            }
          }
        }
      }
    } catch (err) {
      setState(s => ({
        ...s,
        streaming: false,
        naradActive: false,
        error: err instanceof Error ? err.message : 'Unknown error',
      }))
    }
  }, [state.streaming, userId])

  return { ...state, send }
}
