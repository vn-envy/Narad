import { useState, useRef, useEffect } from 'react'
import type { ActiveArtifactSession, Message, AvatarName, AvatarStatus, TokenUsage } from '../hooks/useAvatara'
import { useTTS, VOICE_AVATARS } from '../hooks/useTTS'
import type { TTSAvatar } from '../hooks/useTTS'
import { MahatiLogo } from './MahatiLogo'
import { ZigzagBank } from './Motifs'
import { cn } from '@/lib/utils'
import { Pencil, RotateCcw, Square, Copy, Check, Volume2, VolumeX, Loader, Paperclip, X } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { AVATAR_COLOURS, AVATAR_RGB, DEVA, isAvatarName } from '@/lib/avatara-constants'

const SUGGESTIONS: Array<{ label: string; prompt: string }> = [
  { label: 'Plan my week',        prompt: 'Plan my week from my calendar and open tasks.' },
  { label: 'Research a topic',    prompt: 'Research the latest on ' },
  { label: 'Draft an email',      prompt: 'Draft an email to ' },
  { label: 'Automate something',  prompt: 'Write a script that ' },
]

const MEDIA_RE = /https?:\/\/\S+\/media\/[^\s"')]+\.(mp4|wav|mp3)/gi

function MediaEmbed({ url }: { url: string }) {
  const lc = url.toLowerCase()
  if (lc.endsWith('.mp4')) {
    return (
      <video
        src={url}
        controls
        className="rounded w-full mt-2"
        style={{ maxHeight: '240px', background: 'rgba(45,42,38,0.08)' }}
      />
    )
  }
  if (lc.endsWith('.wav') || lc.endsWith('.mp3')) {
    return (
      <audio
        src={url}
        controls
        className="w-full mt-2"
        style={{ borderRadius: '4px' }}
      />
    )
  }
  return null
}

// Skill-continuation markers like "[Continuing: check]" are internal machinery
// that must survive in server-side history, but shouldn't render raw in chat.
// Strip them here and show a subtle chip instead.
const CONTINUING_RE = /\[Continuing:\s*([^\]]+)\]/gi

function MarkdownMessage({ text }: { text: string }) {
  const phases: string[] = []
  const cleaned = text.replace(CONTINUING_RE, (_m, phase: string) => {
    phases.push(phase.trim())
    return ''
  }).trim()
  const mediaUrls = Array.from(new Set(cleaned.match(MEDIA_RE) ?? []))
  return (
    <>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          p: ({ children }) => (
            <p className="text-[13px] leading-relaxed mb-2 last:mb-0 break-words" style={{ fontFamily: 'var(--font-body)' }}>
              {children}
            </p>
          ),
          h1: ({ children }) => (
            <h1 className="text-[17px] font-semibold mt-4 mb-2 first:mt-0 pb-1.5" style={{ borderBottom: '1px solid rgba(45,42,38,0.12)' }}>
              {children}
            </h1>
          ),
          h2: ({ children }) => (
            <h2 className="text-[15px] font-semibold mt-3 mb-1.5 first:mt-0">{children}</h2>
          ),
          h3: ({ children }) => (
            <h3 className="text-[14px] font-semibold mt-2.5 mb-1 first:mt-0">{children}</h3>
          ),
          h4: ({ children }) => (
            <h4 className="text-[13px] font-semibold mt-2 mb-0.5 first:mt-0 uppercase tracking-wide opacity-70">{children}</h4>
          ),
          ul: ({ children }) => (
            <ul className="pl-5 mb-2 space-y-0.5" style={{ listStyleType: 'disc' }}>{children}</ul>
          ),
          ol: ({ children }) => (
            <ol className="pl-5 mb-2 space-y-0.5" style={{ listStyleType: 'decimal' }}>{children}</ol>
          ),
          li: ({ children }) => (
            <li className="text-[13px] leading-relaxed" style={{ fontFamily: 'var(--font-body)' }}>{children}</li>
          ),
          pre: ({ children }) => (
            <div className="overflow-x-auto rounded mb-2" style={{ background: 'rgba(45,42,38,0.055)', border: '1px solid rgba(45,42,38,0.10)' }}>
              <pre className="p-3 overflow-x-auto">{children}</pre>
            </div>
          ),
          code: ({ children, className }: { children?: React.ReactNode; className?: string }) => {
            const str = String(children ?? '')
            const isBlock = str.includes('\n') || !!className?.startsWith('language-')
            if (!isBlock) {
              return (
                <code className="font-mono text-[11.5px] px-1 py-0.5 rounded" style={{ background: 'rgba(45,42,38,0.09)', color: 'var(--kajal)' }}>
                  {children}
                </code>
              )
            }
            return (
              <code className={cn('font-mono text-[12px] block leading-relaxed', className)} style={{ color: 'var(--kajal)' }}>
                {children}
              </code>
            )
          },
          table: ({ children }) => (
            <div className="overflow-x-auto mb-3 rounded" style={{ border: '1px solid rgba(45,42,38,0.12)' }}>
              <table className="w-full text-[12.5px] border-collapse">{children}</table>
            </div>
          ),
          thead: ({ children }) => (
            <thead style={{ background: 'rgba(45,42,38,0.05)' }}>{children}</thead>
          ),
          tbody: ({ children }) => <tbody>{children}</tbody>,
          tr: ({ children }) => (
            <tr style={{ borderBottom: '1px solid rgba(45,42,38,0.08)' }}>{children}</tr>
          ),
          th: ({ children }) => (
            <th className="font-mono text-[11px] font-semibold text-left px-3 py-2" style={{ color: 'rgba(45,42,38,0.65)', borderRight: '1px solid rgba(45,42,38,0.07)' }}>
              {children}
            </th>
          ),
          td: ({ children }) => (
            <td className="text-[12.5px] px-3 py-1.5" style={{ fontFamily: 'var(--font-body)', borderRight: '1px solid rgba(45,42,38,0.05)' }}>
              {children}
            </td>
          ),
          blockquote: ({ children }) => (
            <blockquote className="pl-3 py-0.5 mb-2 italic" style={{ borderLeft: '3px solid rgba(45,42,38,0.25)', color: 'rgba(45,42,38,0.68)' }}>
              {children}
            </blockquote>
          ),
          strong: ({ children }) => (
            <strong className="font-semibold" style={{ color: 'var(--kajal)' }}>{children}</strong>
          ),
          em: ({ children }) => <em className="italic">{children}</em>,
          a: ({ href, children }) => (
            <a href={href} target="_blank" rel="noopener noreferrer" className="underline" style={{ color: 'var(--sindoor)' }}>
              {children}
            </a>
          ),
          hr: () => <hr className="my-3" style={{ borderColor: 'rgba(45,42,38,0.15)' }} />,
        }}
      >
        {cleaned}
      </ReactMarkdown>
      {mediaUrls.map(url => <MediaEmbed key={url} url={url} />)}
      {phases.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          {phases.map((phase, i) => (
            <span
              key={`${phase}-${i}`}
              className="font-mono text-[10px] px-2 py-0.5 rounded-full"
              style={{
                background: 'rgba(45,42,38,0.06)',
                border: '1px solid rgba(45,42,38,0.12)',
                color: 'rgba(45,42,38,0.55)',
              }}
            >
              lesson continues · {phase}
            </span>
          ))}
        </div>
      )}
    </>
  )
}

function UserMessageText({ text }: { text: string }) {
  return (
    <p className="font-body text-[13px] leading-relaxed whitespace-pre-wrap break-words">
      {text}
    </p>
  )
}

interface TokenTickerProps {
  usage?: TokenUsage
  tokenEstimate?: number
  totalDurationMs?: number
  clientTokPerSec?: number
  avatarsInvolved?: AvatarName[]
  avatarLatencies?: Record<string, number>
}
function TokenTicker({
  usage, tokenEstimate, totalDurationMs, clientTokPerSec,
  avatarsInvolved: _avatarsInvolved, avatarLatencies,
}: TokenTickerProps) {
  const total     = usage?.totalTokens ?? (tokenEstimate ?? null)
  const perSec    = usage?.tokPerSec ?? clientTokPerSec ?? null
  const rawDurMs  = (usage?.synthDurationMs != null && usage.synthDurationMs > 100)
    ? usage.synthDurationMs
    : (totalDurationMs != null && totalDurationMs > 500 ? totalDurationMs : null)
  const durationS = rawDurMs != null ? rawDurMs / 1000 : null
  const isEstimate = !usage?.totalTokens && total != null

  const hasGlobal = total != null
  const avatarEntries = avatarLatencies
    ? Object.entries(avatarLatencies).filter(([, ms]) => ms > 200)
    : []

  if (!hasGlobal && avatarEntries.length === 0) return null

  return (
    <div className="flex flex-col gap-0.5 mt-0.5 pl-0.5">
      {/* Row 1: global metrics */}
      {hasGlobal && (
        <div className="flex items-center gap-1.5 font-mono text-[9px]"
          style={{ color: 'rgba(45,42,38,0.38)' }}>
          <span title={isEstimate ? 'Character-based estimate' : 'Real token count from model'}>
            {isEstimate ? '~' : ''}{total!.toLocaleString()} tok
          </span>
          {perSec != null && (
            <>
              <span style={{ opacity: 0.4 }}>·</span>
              <span>{perSec.toLocaleString()} tok/s</span>
            </>
          )}
          {durationS != null && (
            <>
              <span style={{ opacity: 0.4 }}>·</span>
              <span>{durationS.toFixed(1)}s</span>
            </>
          )}
        </div>
      )}
      {/* Row 2: per-avatar latency chips */}
      {avatarEntries.length > 0 && (
        <div className="flex items-center flex-wrap gap-1">
          {avatarEntries.map(([name, ms]) => {
            const avatarName = name as AvatarName
            const rgb   = AVATAR_RGB[avatarName]   ?? '45,42,38'
            const colour = AVATAR_COLOURS[avatarName] ?? 'rgba(45,42,38,0.6)'
            return (
              <span
                key={name}
                className="text-[8px] font-mono px-1.5 py-px rounded-sm leading-tight"
                style={{
                  color:      colour,
                  background: `rgba(${rgb}, 0.07)`,
                  border:     `1px solid rgba(${rgb}, 0.18)`,
                }}
                title={`${name}: ${(ms / 1000).toFixed(2)}s wall-clock`}
              >
                {name.slice(0, 4).toLowerCase()} {(ms / 1000).toFixed(1)}s
              </span>
            )
          })}
        </div>
      )}
    </div>
  )
}

const ACTION_BTN = cn(
  'flex items-center gap-1 px-1.5 py-1 rounded',
  'font-mono text-[10px] leading-none',
  'border hover:border-kajal/25',
  'text-kajal/40 hover:text-kajal/70',
  'transition-all duration-150 cursor-pointer bg-transparent outline-none',
  'hover:bg-kajal/5 active:scale-95',
  'border-kajal/10'
)

interface Props {
  messages: Message[]
  avatars: Record<AvatarName, AvatarStatus>
  streaming: boolean
  error: string | null
  onSend: (query: string, images?: string[]) => void
  stop: () => void
  onClear?: () => void
  activeArtifact?: ActiveArtifactSession | null
  onCloseArtifact?: () => void
}

export function ChatPanel({
  messages,
  avatars,
  streaming,
  error,
  onSend,
  stop,
  onClear,
  activeArtifact,
  onCloseArtifact,
}: Props) {
  const [input, setInput] = useState('')
  const [pendingImages, setPendingImages] = useState<string[]>([])
  const scrollerRef = useRef<HTMLDivElement>(null)
  const nearBottomRef = useRef(true)
  const [showJump, setShowJump] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const tts = useTTS()

  // Stick-near-bottom autoscroll: only follow the stream when the reader is
  // already at the bottom. Scrolling up to re-read pauses following and shows
  // a "↓ new" pill instead of yanking the viewport on every chunk.
  const scrollToBottom = (smooth = false) => {
    const el = scrollerRef.current
    if (!el) return
    el.scrollTo({ top: el.scrollHeight, behavior: smooth ? 'smooth' : 'auto' })
    nearBottomRef.current = true
    setShowJump(false)
  }

  const handleScroll = () => {
    const el = scrollerRef.current
    if (!el) return
    const near = el.scrollHeight - el.scrollTop - el.clientHeight < 120
    nearBottomRef.current = near
    if (near) setShowJump(false)
  }

  useEffect(() => {
    if (nearBottomRef.current) scrollToBottom()
    else setShowJump(true)
  }, [messages])

  const attachImages = (files: FileList) => {
    Array.from(files).forEach(file => {
      const reader = new FileReader()
      reader.onload = () => {
        const dataUri = reader.result as string  // full "data:image/png;base64,..." URI
        if (dataUri) setPendingImages(prev => [...prev, dataUri])
      }
      reader.readAsDataURL(file)
    })
  }

  const handleSend = () => {
    const q = input.trim()
    if (!q || streaming) return
    onSend(q, pendingImages)
    setInput('')
    setPendingImages([])
    if (textareaRef.current) textareaRef.current.style.height = 'auto'
    // Sending always re-engages follow mode — jump to your own message.
    requestAnimationFrame(() => scrollToBottom())
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

  const handleEdit = (text: string) => {
    setInput(text)
    setTimeout(() => {
      textareaRef.current?.focus()
      const ta = textareaRef.current
      if (ta) {
        ta.style.height = 'auto'
        ta.style.height = Math.min(ta.scrollHeight, 140) + 'px'
      }
    }, 0)
  }

  const handleRestart = (msgId: string) => {
    const idx = messages.findIndex(m => m.id === msgId)
    const prev = idx > 0 ? messages[idx - 1] : null
    if (prev?.role === 'user') onSend(prev.text)
  }

  const [copiedId, setCopiedId] = useState<string | null>(null)
  const handleCopy = (msgId: string, text: string) => {
    navigator.clipboard.writeText(text).then(() => {
      setCopiedId(msgId)
      setTimeout(() => setCopiedId(id => id === msgId ? null : id), 1500)
    })
  }

  const activeAvatar = Object.values(avatars).find(a => a.state === 'active') ?? null

  return (
    <div className="flex flex-col h-full overflow-hidden" style={{ background: 'var(--paper)' }}>

      {/* Header — dark kajal with Playfair italic */}
      <div
        className="flex items-center gap-3 px-5 py-3 flex-shrink-0 relative overflow-hidden"
        style={{ background: 'var(--kajal)', minHeight: 56 }}
      >
        <MahatiLogo size={32} />
        <div className="flex flex-col gap-0">
          <span
            className="label-hero text-[22px] leading-none"
            style={{ color: 'var(--paper)', letterSpacing: '-0.01em' }}
          >
            NARAD.OS
          </span>
          <span className="font-deva text-[11px] leading-tight" style={{ color: 'rgba(252,250,242,0.55)', fontFamily: 'var(--font-deva)' }}>
            नारद  अवतारा
          </span>
        </div>
        {onClear && messages.length > 0 && (
          <button
            onClick={onClear}
            title="Clear conversation"
            className="ml-auto z-10 flex items-center gap-1 px-2 py-1 rounded text-[11px] transition-opacity opacity-50 hover:opacity-100"
            style={{ color: 'rgba(252,250,242,0.7)', background: 'rgba(252,250,242,0.08)', border: '1px solid rgba(252,250,242,0.15)' }}
          >
            <RotateCcw size={12} />
            clear
          </button>
        )}
        {/* Zigzag motif at bottom edge of header */}
        <div className="absolute bottom-0 left-0 w-full overflow-hidden" style={{ height: 16, opacity: 0.12 }}>
          <ZigzagBank color="var(--paper)" className="w-full" />
        </div>
      </div>

      {/* Messages */}
      <div className="relative flex-1 min-h-0">
      <div
        ref={scrollerRef}
        onScroll={handleScroll}
        className="h-full overflow-y-auto px-4 py-5 flex flex-col gap-2.5"
        style={{ background: 'var(--paper)' }}
      >
        {messages.length === 0 && (
          <div className="flex flex-col items-center justify-center gap-3 mt-[22%]">
            <div style={{ opacity: 0.85 }}>
              <MahatiLogo size={56} />
            </div>
            <p className="text-[34px] leading-none" style={{ fontFamily: 'var(--font-deva)', color: 'var(--sindoor)', opacity: 0.8 }}>नमस्ते</p>
            <p className="label-hero text-[15px]" style={{ color: 'var(--ink-55)' }}>
              Ask anything — Narad plucks the right string.
            </p>
            <div className="flex flex-wrap justify-center gap-2 mt-2 max-w-[420px]">
              {SUGGESTIONS.map(s => (
                <button
                  key={s.label}
                  onClick={() => handleEdit(s.prompt)}
                  className="text-chip px-3 py-1.5 rounded-full cursor-pointer transition-all duration-150 hover:scale-[1.03] active:scale-95"
                  style={{
                    fontSize: 11,
                    color: 'var(--ink-70)',
                    background: 'var(--surface)',
                    border: '1px solid var(--ink-12)',
                    boxShadow: '0 2px 8px -2px var(--ink-08)',
                  }}
                >
                  {s.label}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map(msg => {
          const primaryAvatar = msg.avatarsInvolved?.[0]
          const avatarClass = primaryAvatar
            ? `avatar-glass-${primaryAvatar.toLowerCase()}`
            : ''

          return (
            <div
              key={msg.id}
              className={cn(
                'group/bubble flex flex-col gap-0.5 w-full',
                msg.role === 'user' ? 'items-end' : 'items-start'
              )}
            >
              {/* Bubble */}
              <div
                className={cn(
                  'max-w-[82%] px-3.5 py-2.5 text-body-sm',
                  msg.role === 'user'
                    ? 'rounded-[16px_16px_4px_16px]'
                    : cn('folk-card folk-shadow rounded-[4px_16px_16px_16px]', avatarClass)
                )}
                style={
                  msg.role === 'user'
                    ? {
                        background: 'var(--kajal)',
                        color: 'var(--paper)',
                        borderRadius: '16px 16px 4px 16px',
                      }
                    : { color: 'var(--kajal)' }
                }
              >
                {/* Avatar tags */}
                {msg.role === 'assistant' && msg.avatarsInvolved && msg.avatarsInvolved.length > 0 && (
                  <div className="flex flex-wrap gap-1.5 mb-2">
                    {msg.avatarsInvolved.map(a => (
                      <span
                        key={a}
                        className="text-chip px-2 py-px rounded organic-border inline-flex items-baseline gap-1"
                        style={{
                          color: AVATAR_COLOURS[a],
                          borderColor: `rgba(${AVATAR_RGB[a]}, 0.30)`,
                          background: `rgba(${AVATAR_RGB[a]}, 0.08)`,
                        }}
                      >
                        {isAvatarName(a) && (
                          <span style={{ fontFamily: 'var(--font-deva)', fontSize: 10 }}>
                            {DEVA[a]?.charAt(0)}
                          </span>
                        )}
                        {a}
                      </span>
                    ))}
                  </div>
                )}

                {msg.role === 'assistant'
                  ? <MarkdownMessage text={msg.text} />
                  : <UserMessageText text={msg.text} />
                }
              </div>

              {/* Action buttons + token ticker */}
              <div
                className={cn(
                  'flex flex-col gap-0.5',
                  msg.role === 'user' ? 'items-end pr-0.5' : 'items-start pl-0.5'
                )}
              >
                <div className="msg-actions flex gap-1 opacity-0 group-hover/bubble:opacity-100 transition-opacity duration-150">
                  {msg.role === 'user' && (
                    <button
                      className={ACTION_BTN}
                      onClick={() => handleEdit(msg.text)}
                      title="Edit and resend"
                    >
                      <Pencil size={10} />
                      edit
                    </button>
                  )}
                  {msg.role === 'assistant' && (
                    <button
                      className={ACTION_BTN}
                      onClick={() => handleCopy(msg.id, msg.text)}
                      title="Copy to clipboard"
                    >
                      {copiedId === msg.id
                        ? <><Check size={10} />copied</>
                        : <><Copy size={10} />copy</>
                      }
                    </button>
                  )}
                  {msg.role === 'assistant' && !streaming && (
                    <button
                      className={ACTION_BTN}
                      onClick={() => handleRestart(msg.id)}
                      title="Restart from this prompt"
                    >
                      <RotateCcw size={10} />
                      restart
                    </button>
                  )}
                  {msg.role === 'assistant' && !streaming && (() => {
                    const voiceAvatar = msg.avatarsInvolved?.find(
                      a => VOICE_AVATARS.includes(a as TTSAvatar)
                    ) as TTSAvatar | undefined
                    if (!voiceAvatar) return null

                    const enKey = `${msg.id}:en`
                    const hiKey = `${msg.id}:hi`
                    const isEnPlaying = tts.playingId === enKey
                    const isHiPlaying = tts.playingId === hiKey
                    const isEnLoading = tts.state === 'loading' && isEnPlaying
                    const isHiLoading = tts.state === 'loading' && isHiPlaying

                    return (
                      <>
                        <button
                          className={cn(ACTION_BTN, isEnPlaying && 'opacity-100')}
                          onClick={() => tts.speak(msg.text, voiceAvatar, msg.id, 'en')}
                          title={isEnPlaying ? 'Stop' : `Speak as ${voiceAvatar}`}
                          style={isEnPlaying ? { color: 'var(--marigold)', borderColor: 'rgba(194,65,12,0.35)' } : {}}
                        >
                          {isEnLoading
                            ? <><Loader size={10} style={{ animation: 'spin 1s linear infinite' }} />loading</>
                            : isEnPlaying
                            ? <><VolumeX size={10} />stop</>
                            : <><Volume2 size={10} />speak</>
                          }
                        </button>
                        <button
                          className={cn(ACTION_BTN, isHiPlaying && 'opacity-100')}
                          onClick={() => tts.speak(msg.text, voiceAvatar, msg.id, 'hi')}
                          title={isHiPlaying ? 'Stop Hindi' : `Speak in Hindi`}
                          style={isHiPlaying ? { color: 'var(--marigold)', borderColor: 'rgba(194,65,12,0.35)' } : {}}
                        >
                          {isHiLoading
                            ? <><Loader size={10} style={{ animation: 'spin 1s linear infinite' }} />loading</>
                            : isHiPlaying
                            ? <><VolumeX size={10} />stop</>
                            : <span style={{ fontFamily: 'var(--font-deva)', fontSize: 11 }}>हिं</span>
                          }
                        </button>
                      </>
                    )
                  })()}
                </div>
                {msg.role === 'assistant' && (
                  <TokenTicker
                    usage={msg.usage}
                    tokenEstimate={msg.tokenEstimate}
                    totalDurationMs={msg.totalDurationMs}
                    clientTokPerSec={msg.clientTokPerSec}
                    avatarsInvolved={msg.avatarsInvolved}
                    avatarLatencies={msg.avatarLatencies}
                  />
                )}
              </div>
            </div>
          )
        })}

        {/* Streaming indicator — breathes in the active avatar's colour */}
        {streaming && (() => {
          const activeName = activeAvatar?.name
          const streamColour = activeName && isAvatarName(activeName)
            ? AVATAR_COLOURS[activeName]
            : 'var(--sindoor)'
          const streamRgb = activeName && isAvatarName(activeName)
            ? AVATAR_RGB[activeName]
            : 'var(--rgb-sindoor)'
          return (
            <div className="flex flex-col gap-2 max-w-[82%]">

              {/* Active avatar label + task */}
              {activeAvatar && (
                <div className="flex items-center gap-2 px-1">
                  {activeName && isAvatarName(activeName) && (
                    <span style={{ fontFamily: 'var(--font-deva)', fontSize: 12, color: streamColour }}>
                      {DEVA[activeName]?.charAt(0)}
                    </span>
                  )}
                  <span
                    className="font-mono text-[11px] font-medium"
                    style={{ color: streamColour }}
                  >
                    {activeAvatar.name}
                  </span>
                  {activeAvatar.task && (
                    <span className="font-mono text-[11px] opacity-35 truncate flex-1" style={{ color: 'var(--ink)' }}>
                      {activeAvatar.task}
                    </span>
                  )}
                </div>
              )}

              {/* Progress bar */}
              <div
                className="h-[2px] rounded-full overflow-hidden mx-1"
                style={{ background: 'var(--ink-08)' }}
              >
                <div
                  className="h-full w-[35%] rounded-full"
                  style={{
                    background: streamColour,
                    animation: 'progress-sweep 1.6s ease-in-out infinite',
                  }}
                />
              </div>

              {/* Breathing dots + stop button */}
              <div className="flex items-center gap-2.5">
                <div className="folk-card flex items-center gap-1.5 px-4 py-3.5 rounded w-fit">
                  {[0, 200, 400].map(delay => (
                    <span
                      key={delay}
                      className="inline-block w-[7px] h-[7px] rounded-full"
                      style={{
                        background: `rgba(${streamRgb}, 0.9)`,
                        animation: `breath 1.2s ease-in-out ${delay}ms infinite`,
                      }}
                    />
                  ))}
                </div>
                <button
                  className={cn(ACTION_BTN, 'border-sindoor/25 text-sindoor/50 hover:text-sindoor/80 hover:border-sindoor/40')}
                  onClick={stop}
                  title="Stop generation"
                >
                  <Square size={10} />
                  stop
                </button>
              </div>

            </div>
          )
        })()}

        {error && (
          <div
            className="px-3.5 py-2.5 rounded organic-border mx-1"
            style={{
              background: 'rgba(194,65,12,0.05)',
              borderColor: 'rgba(194,65,12,0.30)',
            }}
          >
            <span className="font-mono text-[12px]" style={{ color: 'var(--sindoor)' }}>⚠ {error}</span>
          </div>
        )}

      </div>

      {/* "↓ new" pill — appears when new content arrives while scrolled up */}
      {showJump && (
        <button
          onClick={() => scrollToBottom(true)}
          className="absolute bottom-3 left-1/2 -translate-x-1/2 z-10 flex items-center gap-1.5 px-3 py-1.5 rounded-full font-mono text-[11px] cursor-pointer transition-transform hover:scale-105 active:scale-95"
          style={{
            background: 'var(--kajal)',
            color: 'var(--paper)',
            border: '1px solid rgba(252,250,242,0.18)',
            boxShadow: '0 4px 14px rgba(0,0,0,0.25)',
          }}
        >
          ↓ new
        </button>
      )}
      </div>

      {/* Input area */}
      <div
        className="flex flex-col gap-2 px-4 py-3 flex-shrink-0"
        style={{
          background: 'var(--speckle)',
          borderTop: '1px solid color-mix(in srgb, var(--kajal) 10%, transparent)',
        }}
      >
        {activeArtifact && (
          <div
            className="flex items-center justify-between gap-3 rounded px-3 py-2"
            style={{
              background: 'rgba(255,255,255,0.58)',
              border: '1px solid color-mix(in srgb, var(--kajal) 10%, transparent)',
            }}
          >
            <div className="min-w-0">
              <div className="font-mono text-[10px] uppercase tracking-[0.12em]" style={{ color: 'rgba(45,42,38,0.48)' }}>
                Active Artifact
              </div>
              <div className="text-[12px] font-semibold truncate" style={{ color: 'var(--kajal)' }}>
                {activeArtifact.artifactType === 'flashcards' ? 'Flashcards' : 'Concept Map'} · {activeArtifact.topic}
              </div>
              <div className="text-[11px] truncate" style={{ color: 'rgba(45,42,38,0.58)' }}>
                Explicit edit prompts only: “add one more card…” or “add a node for…”
              </div>
            </div>
            {onCloseArtifact && (
              <button
                onClick={onCloseArtifact}
                className="w-8 h-8 rounded flex items-center justify-center flex-shrink-0"
                style={{ border: '1px solid color-mix(in srgb, var(--kajal) 12%, transparent)', color: 'rgba(45,42,38,0.62)' }}
                title="Close artifact panel"
              >
                <X size={14} />
              </button>
            )}
          </div>
        )}

        {/* Thumbnail strip */}
        {pendingImages.length > 0 && (
          <div className="flex flex-wrap gap-2">
            {pendingImages.map((b64, i) => (
              <div key={i} className="relative w-14 h-14 flex-shrink-0 rounded overflow-hidden"
                style={{ border: '1px solid color-mix(in srgb, var(--kajal) 15%, transparent)' }}>
                <img
                  src={b64}
                  alt=""
                  className="w-full h-full object-cover"
                />
                <button
                  className="absolute top-0.5 right-0.5 w-4 h-4 rounded-full flex items-center justify-center"
                  style={{ background: 'rgba(45,42,38,0.7)' }}
                  onClick={() => setPendingImages(prev => prev.filter((_, idx) => idx !== i))}
                >
                  <X size={8} style={{ color: 'var(--paper)' }} />
                </button>
              </div>
            ))}
          </div>
        )}

        <div className="flex items-end gap-2.5">
        {/* Hidden file input */}
        <input
          ref={fileRef}
          type="file"
          accept="image/*"
          multiple
          className="hidden"
          onChange={e => { if (e.target.files) attachImages(e.target.files); e.target.value = '' }}
        />
        {/* Paperclip button */}
        <button
          onClick={() => fileRef.current?.click()}
          disabled={streaming}
          className={cn(
            'w-10 h-10 rounded flex-shrink-0 flex items-center justify-center',
            'transition-all duration-150 border outline-none cursor-pointer',
            streaming ? 'opacity-30 cursor-not-allowed' : 'hover:scale-105 active:scale-95'
          )}
          style={{
            background: 'var(--paper)',
            borderColor: 'color-mix(in srgb, var(--kajal) 12%, transparent)',
            color: 'var(--kajal)',
            opacity: streaming ? 0.3 : 0.55,
            borderRadius: '4px',
          }}
          title="Attach image"
        >
          <Paperclip size={15} />
        </button>
        <textarea
          ref={textareaRef}
          value={input}
          onChange={autoResize}
          onKeyDown={handleKey}
          placeholder="Ask Narad…"
          disabled={streaming}
          rows={1}
          className={cn(
            'flex-1 resize-none px-3.5 py-2.5',
            'font-body text-[13px] leading-relaxed placeholder:opacity-35',
            'outline-none',
            'transition-all duration-150',
            'min-h-[42px] max-h-[140px]',
            streaming && 'opacity-50'
          )}
          style={{
            background: 'var(--paper)',
            color: 'var(--kajal)',
            border: '1px solid color-mix(in srgb, var(--kajal) 12%, transparent)',
            borderRadius: '4px',
            boxShadow: 'none',
          }}
          onFocus={e => {
            e.target.style.borderColor = 'rgba(194,65,12,0.50)'
            e.target.style.boxShadow = '0 0 0 2px rgba(194,65,12,0.12)'
          }}
          onBlur={e => {
            e.target.style.borderColor = 'color-mix(in srgb, var(--kajal) 12%, transparent)'
            e.target.style.boxShadow = 'none'
          }}
        />
        <button
          onClick={handleSend}
          disabled={streaming || !input.trim()}
          className={cn(
            'w-10 h-10 rounded flex-shrink-0 flex items-center justify-center',
            'font-bold text-[18px] transition-all duration-150',
            'border-0 outline-none cursor-pointer',
            (streaming || !input.trim())
              ? 'opacity-30 cursor-not-allowed'
              : 'hover:scale-105 active:scale-95'
          )}
          style={{ background: 'var(--marigold)', color: 'var(--paper)', borderRadius: '4px' }}
        >
          ↑
        </button>
        </div>
      </div>
    </div>
  )
}
