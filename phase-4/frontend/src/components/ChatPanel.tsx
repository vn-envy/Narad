/**
 * Chat Panel — left side, the daily-use surface.
 *
 * Rule from visual-system.md:
 *   Daily-use chat surface = calm cream base, kajal text.
 *   Avatar colours only on the active message bubble.
 *   Restraint comes through grid and clarity, not palette muting.
 */

import { useState, useRef, useEffect } from 'react'
import type { Message, AvatarName } from '../hooks/useAvatara'
import { MahatiLogo } from './MahatiLogo'

const AVATAR_COLOURS: Record<AvatarName, string> = {
  Matsya:      '#1E2A5E',
  Varaha:      '#E55A1F',
  Narasimha:   '#C0392B',
  Rama:        '#2E7D4F',
  Krishna:     '#1F7A8C',
  Buddha:      '#F2C14E',
  Parashurama: '#4A4A4A',
}

interface Props {
  messages: Message[]
  streaming: boolean
  error: string | null
  onSend: (query: string) => void
}

export function ChatPanel({ messages, streaming, error, onSend }: Props) {
  const [input, setInput] = useState('')
  const bottomRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const handleSend = () => {
    const q = input.trim()
    if (!q || streaming) return
    onSend(q)
    setInput('')
    if (textareaRef.current) textareaRef.current.style.height = 'auto'
  }

  const handleKey = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const autoResize = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value)
    e.target.style.height = 'auto'
    e.target.style.height = Math.min(e.target.scrollHeight, 140) + 'px'
  }

  return (
    <div style={styles.panel}>
      {/* Header */}
      <div style={styles.header}>
        <MahatiLogo size={32} />
        <div style={styles.wordmark}>
          <span style={styles.wordmarkLatin}>AVATĀRA</span>
          <span style={styles.wordmarkDeva}>अवतारा</span>
        </div>
      </div>

      {/* Messages */}
      <div style={styles.messages}>
        {messages.length === 0 && (
          <div style={styles.empty}>
            <p style={styles.emptyTitle}>नमस्ते</p>
            <p style={styles.emptyHint}>Ask anything. Narad will route it.</p>
          </div>
        )}

        {messages.map(msg => (
          <div
            key={msg.id}
            style={{
              ...styles.msgRow,
              justifyContent: msg.role === 'user' ? 'flex-end' : 'flex-start',
            }}
          >
            <div
              style={{
                ...styles.bubble,
                ...(msg.role === 'user' ? styles.userBubble : styles.assistantBubble),
              }}
            >
              {/* Avatar tags on assistant messages */}
              {msg.role === 'assistant' && msg.avatarsInvolved && msg.avatarsInvolved.length > 0 && (
                <div style={styles.avatarTags}>
                  {msg.avatarsInvolved.map(a => (
                    <span
                      key={a}
                      style={{
                        ...styles.avatarTag,
                        borderColor: AVATAR_COLOURS[a],
                        color: AVATAR_COLOURS[a],
                      }}
                    >
                      {a}
                    </span>
                  ))}
                </div>
              )}

              <p style={styles.msgText}>{msg.text}</p>
            </div>
          </div>
        ))}

        {/* Streaming indicator */}
        {streaming && (
          <div style={{ ...styles.msgRow, justifyContent: 'flex-start' }}>
            <div style={{ ...styles.bubble, ...styles.assistantBubble, ...styles.thinkingBubble }}>
              <span style={styles.dot} />
              <span style={{ ...styles.dot, animationDelay: '0.2s' }} />
              <span style={{ ...styles.dot, animationDelay: '0.4s' }} />
            </div>
          </div>
        )}

        {error && (
          <div style={styles.errorRow}>
            <span style={styles.errorText}>⚠ {error}</span>
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* Input area */}
      <div style={styles.inputArea}>
        <textarea
          ref={textareaRef}
          value={input}
          onChange={autoResize}
          onKeyDown={handleKey}
          placeholder="Ask Narad…"
          disabled={streaming}
          rows={1}
          style={{
            ...styles.textarea,
            opacity: streaming ? 0.5 : 1,
          }}
        />
        <button
          onClick={handleSend}
          disabled={streaming || !input.trim()}
          style={{
            ...styles.sendBtn,
            opacity: streaming || !input.trim() ? 0.4 : 1,
            cursor: streaming || !input.trim() ? 'not-allowed' : 'pointer',
          }}
        >
          ↑
        </button>
      </div>
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  panel: {
    display: 'flex',
    flexDirection: 'column',
    height: '100%',
    background: 'transparent',
  },
  header: {
    display: 'flex',
    alignItems: 'center',
    gap: 12,
    padding: '14px 20px',
    borderBottom: '2.5px solid var(--kajal)',
    background: 'var(--paper)',
  },
  wordmark: {
    display: 'flex',
    flexDirection: 'column',
    gap: 0,
  },
  wordmarkLatin: {
    fontFamily: 'var(--font-hero)',
    fontSize: 22,
    letterSpacing: '0.08em',
    color: 'var(--kajal)',
    lineHeight: 1,
  },
  wordmarkDeva: {
    fontFamily: 'var(--font-deva)',
    fontSize: 12,
    color: 'var(--nila)',
    lineHeight: 1.2,
    opacity: 0.8,
  },
  messages: {
    flex: 1,
    overflowY: 'auto',
    padding: '20px 16px',
    display: 'flex',
    flexDirection: 'column',
    gap: 12,
  },
  empty: {
    flex: 1,
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    opacity: 0.5,
    marginTop: '30%',
  },
  emptyTitle: {
    fontFamily: 'var(--font-deva)',
    fontSize: 32,
    color: 'var(--nila)',
  },
  emptyHint: {
    fontFamily: 'var(--font-body)',
    fontSize: 14,
    color: 'var(--kajal)',
  },
  msgRow: {
    display: 'flex',
    width: '100%',
  },
  bubble: {
    maxWidth: '82%',
    padding: '10px 14px',
    borderRadius: 'var(--radius-md)',
    border: '2px solid var(--kajal)',
    lineHeight: 1.6,
  },
  userBubble: {
    background: 'var(--nila)',
    color: 'var(--paper)',
    borderRadius: '12px 12px 2px 12px',
  },
  assistantBubble: {
    background: 'var(--paper)',
    color: 'var(--kajal)',
    borderRadius: '12px 12px 12px 2px',
  },
  avatarTags: {
    display: 'flex',
    flexWrap: 'wrap',
    gap: 4,
    marginBottom: 8,
  },
  avatarTag: {
    fontFamily: 'var(--font-mono)',
    fontSize: 10,
    padding: '1px 7px',
    borderRadius: 20,
    border: '1.5px solid',
    fontWeight: 600,
  },
  msgText: {
    fontFamily: 'var(--font-body)',
    fontSize: 14,
    whiteSpace: 'pre-wrap',
    wordBreak: 'break-word',
  },
  thinkingBubble: {
    display: 'flex',
    alignItems: 'center',
    gap: 5,
    padding: '14px 18px',
  },
  dot: {
    display: 'inline-block',
    width: 7,
    height: 7,
    borderRadius: '50%',
    background: 'var(--marigold)',
    animation: 'bounce 1.2s ease-in-out infinite',
  },
  errorRow: {
    padding: '8px 12px',
    borderRadius: 8,
    background: 'rgba(192,57,43,0.1)',
    border: '1.5px solid var(--sindoor)',
  },
  errorText: {
    fontFamily: 'var(--font-mono)',
    fontSize: 12,
    color: 'var(--sindoor)',
  },
  inputArea: {
    display: 'flex',
    alignItems: 'flex-end',
    gap: 8,
    padding: '12px 16px',
    borderTop: '2.5px solid var(--kajal)',
    background: 'var(--paper)',
  },
  textarea: {
    flex: 1,
    resize: 'none',
    border: '2px solid var(--kajal)',
    borderRadius: 'var(--radius-sm)',
    padding: '10px 12px',
    fontFamily: 'var(--font-body)',
    fontSize: 14,
    background: 'var(--paper)',
    color: 'var(--kajal)',
    lineHeight: 1.5,
    outline: 'none',
    minHeight: 42,
  },
  sendBtn: {
    width: 42,
    height: 42,
    borderRadius: '50%',
    border: '2.5px solid var(--kajal)',
    background: 'var(--marigold)',
    color: 'var(--kajal)',
    fontSize: 20,
    fontWeight: 700,
    cursor: 'pointer',
    flexShrink: 0,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    transition: 'transform 0.1s',
  },
}
