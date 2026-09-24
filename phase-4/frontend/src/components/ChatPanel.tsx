import { memo, useState, useRef, useEffect } from 'react'
import type {
  ActiveArtifactSession,
  ChatAttachment,
  ChatAttachmentBatch,
  Message,
  AvatarName,
  AvatarStatus,
  TokenUsage,
  GuidedSessionMeta,
  LiveAnswer,
} from '../hooks/useAvatara'
import { useTTS, VOICE_AVATARS } from '../hooks/useTTS'
import { unlockAudio } from '@/lib/speech-queue'
import type { TTSAvatar } from '../hooks/useTTS'
import { Veena, type VeenaMood } from './Veena'
import { AvatarGlyph } from './AvatarGlyph'
import { GuruMessage } from './GuruCards'
import { ApprovalCard, type ApprovalChange } from './ApprovalCard'
import { MessageFooter } from './MessageFooter'
import { DocumentReviewHost } from './DocumentReview'
import { TaskCard } from './TaskCard'
import { PathSuggestionCard } from './PathSuggestionCard'
import { cn } from '@/lib/utils'
import {
  Archive,
  ArrowDown,
  ArrowUp,
  Check,
  Code2,
  Copy,
  File as FileIcon,
  FileSpreadsheet,
  FileText,
  Files,
  FolderOpen,
  GraduationCap,
  Image,
  Link2,
  Loader,
  Mic,
  Paperclip,
  Pencil,
  RotateCcw,
  Square,
  TriangleAlert,
  Volume2,
  VolumeX,
  X,
} from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { AVATAR_COLOURS, AVATAR_RGB, isAvatarName } from '@/lib/avatara-constants'
import { apiFetch, apiPath, apiUrl, type WorkflowRun } from '@/lib/api'
import type { FamilyProfile } from '@/lib/api'
import { useIsMobile } from '@/hooks/useIsMobile'
import { PROFILE_COLORS, ProfileBadge } from './ProfileBadge'
import { textLang } from '@/lib/trust'
import { toast } from 'sonner'

/** The empty chat introduces the four avatāras; a tap starts a prompt in their line of work. */
const AVATAR_INTROS: Array<{ name: AvatarName; role: string; prompt: string }> = [
  { name: 'Matsya',      role: 'Research a topic',   prompt: 'Research the latest on ' },
  { name: 'Rama',        role: 'Plan my week',       prompt: 'Plan my week from my calendar and open tasks.' },
  { name: 'Krishna',     role: 'Teach me something', prompt: '/teach me ' },
  { name: 'Parashurama', role: 'Automate something', prompt: 'Write a script that ' },
]

const MEDIA_RE = /https?:\/\/\S+\/media\/[^\s"')]+\.(mp4|wav|mp3)/gi
const LIVE_URL_RE = /https?:\/\/[^\s<>\]\[()"']+/gi
const MAX_UPLOAD_FILES = 256
const IGNORED_FOLDER_PARTS = new Set(['.git', 'node_modules', '.next', 'dist', 'build', '__pycache__'])

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / (1024 * 1024)).toFixed(1)} MB`
}

function AttachmentIcon({ kind, size = 14 }: { kind: ChatAttachment['kind']; size?: number }) {
  if (kind === 'image') return <Image size={size} />
  if (kind === 'archive') return <Archive size={size} />
  if (kind === 'code') return <Code2 size={size} />
  if (kind === 'data') return <FileSpreadsheet size={size} />
  if (kind === 'document' || kind === 'text') return <FileText size={size} />
  return <FileIcon size={size} />
}

function MessageAttachments({ attachments }: { attachments?: ChatAttachment[] }) {
  if (!attachments?.length) return null
  const shown = attachments.slice(0, 6)
  return (
    <div className="flex flex-wrap gap-1.5 mt-2 pt-2" style={{ borderTop: '1px solid rgba(250,247,240,0.16)' }}>
      {shown.map(item => (
        <span
          key={item.attachment_id}
          className="inline-flex items-center gap-1.5 max-w-[210px] rounded px-2 py-1 font-mono text-[10px]"
          style={{ background: 'rgba(250,247,240,0.10)', color: 'rgba(250,247,240,0.82)' }}
          title={item.relative_path}
        >
          <AttachmentIcon kind={item.kind} size={11} />
          <span className="truncate">{item.name}</span>
        </span>
      ))}
      {attachments.length > shown.length && (
        <span className="font-mono text-[10px] px-1.5 py-1" style={{ color: 'rgba(250,247,240,0.62)' }}>
          +{attachments.length - shown.length} more
        </span>
      )}
    </div>
  )
}

function MediaEmbed({ url }: { url: string }) {
  const lc = url.toLowerCase()
  if (lc.endsWith('.mp4')) {
    return (
      <video
        src={url}
        controls
        className="rounded w-full mt-2"
        style={{ maxHeight: '240px', background: 'rgba(var(--rgb-ink),0.08)' }}
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
// A streaming skill answer ends with its raw marker; the final reply carries
// the "[Continuing: …]" form instead, so hide the raw one while it streams.
const LIVE_PHASE_RE = /\n?[ \t]*CURRENT_PHASE:[^\n]*$/i

// Memoised: a streaming answer re-renders the panel many times a second, and
// only the text that changed should be parsed as markdown again.
const MarkdownMessage = memo(function MarkdownMessage({ text, live = false }: { text: string; live?: boolean }) {
  const phases: string[] = []
  const cleaned = text.replace(CONTINUING_RE, (_m, phase: string) => {
    phases.push(phase.trim())
    return ''
  }).trim()
  // A half-streamed media URL would load a broken embed; embeds wait for the final reply.
  const mediaUrls = live ? [] : Array.from(new Set(cleaned.match(MEDIA_RE) ?? []))
  return (
    <>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          p: ({ children }) => (
            <p className="leading-relaxed mb-2 last:mb-0 break-words">
              {children}
            </p>
          ),
          h1: ({ children }) => (
            <h1 className="text-[18px] font-semibold mt-4 mb-2 first:mt-0 pb-1.5" style={{ borderBottom: '1px solid var(--ink-12)' }}>
              {children}
            </h1>
          ),
          h2: ({ children }) => (
            <h2 className="text-[16.5px] font-semibold mt-3 mb-1.5 first:mt-0">{children}</h2>
          ),
          h3: ({ children }) => (
            <h3 className="text-[15.5px] font-semibold mt-2.5 mb-1 first:mt-0">{children}</h3>
          ),
          h4: ({ children }) => (
            <h4 className="text-[14px] font-semibold mt-2 mb-0.5 first:mt-0 uppercase tracking-wide opacity-70">{children}</h4>
          ),
          ul: ({ children }) => (
            <ul className="pl-5 mb-2 space-y-0.5" style={{ listStyleType: 'disc' }}>{children}</ul>
          ),
          ol: ({ children }) => (
            <ol className="pl-5 mb-2 space-y-0.5" style={{ listStyleType: 'decimal' }}>{children}</ol>
          ),
          li: ({ children }) => (
            <li className="leading-relaxed">{children}</li>
          ),
          pre: ({ children }) => (
            <div className="overflow-x-auto rounded mb-2" style={{ background: 'var(--ink-05)', border: '1px solid var(--line)' }}>
              <pre className="p-3 overflow-x-auto">{children}</pre>
            </div>
          ),
          code: ({ children, className }: { children?: React.ReactNode; className?: string }) => {
            const str = String(children ?? '')
            const isBlock = str.includes('\n') || !!className?.startsWith('language-')
            if (!isBlock) {
              return (
                <code className="font-code text-[0.88em] px-1 py-0.5 rounded" style={{ background: 'var(--ink-08)', color: 'var(--kajal)' }}>
                  {children}
                </code>
              )
            }
            return (
              <code className={cn('font-code text-[13px] block leading-relaxed', className)} style={{ color: 'var(--kajal)' }}>
                {children}
              </code>
            )
          },
          table: ({ children }) => (
            <div className="overflow-x-auto mb-3 rounded" style={{ border: '1px solid var(--ink-12)' }}>
              <table className="w-full text-[14px] border-collapse">{children}</table>
            </div>
          ),
          thead: ({ children }) => (
            <thead style={{ background: 'var(--ink-05)' }}>{children}</thead>
          ),
          tbody: ({ children }) => <tbody>{children}</tbody>,
          tr: ({ children }) => (
            <tr style={{ borderBottom: '1px solid var(--ink-08)' }}>{children}</tr>
          ),
          th: ({ children }) => (
            <th className="text-[12.5px] font-semibold text-left px-3 py-2 whitespace-nowrap" style={{ color: 'var(--ink-70)', borderRight: '1px solid var(--ink-08)', overflowWrap: 'normal' }}>
              {children}
            </th>
          ),
          td: ({ children }) => (
            <td className="px-3 py-2 align-top" style={{ borderRight: '1px solid var(--ink-05)', overflowWrap: 'normal', wordBreak: 'normal', hyphens: 'auto' }}>
              {children}
            </td>
          ),
          blockquote: ({ children }) => (
            <blockquote className="pl-3 py-0.5 mb-2 italic" style={{ borderLeft: '3px solid var(--ink-20)', color: 'var(--ink-70)' }}>
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
          hr: () => <hr className="my-3" style={{ borderColor: 'var(--ink-12)' }} />,
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
              className="font-mono text-[11.5px] px-2 py-0.5 rounded-full"
              style={{
                background: 'var(--ink-05)',
                border: '1px solid var(--ink-12)',
                color: 'var(--ink-55)',
              }}
            >
              lesson continues · {phase}
            </span>
          ))}
        </div>
      )}
    </>
  )
})

function AvatarChips({ avatars }: { avatars: AvatarName[] }) {
  return (
    <div className="flex flex-wrap gap-1.5 mb-2">
      {avatars.map(a => (
        <span
          key={a}
          className="pl-1.5 pr-2 py-0.5 rounded-full inline-flex items-center gap-1 font-mono text-[11px] tracking-[0.04em]"
          style={{
            color: isAvatarName(a) ? `var(--avatar-${a.toLowerCase()})` : 'var(--ink-70)',
            background: `rgba(${AVATAR_RGB[a]}, 0.08)`,
          }}
        >
          {isAvatarName(a) && <AvatarGlyph name={a} size={14} />}
          {a}
        </span>
      ))}
    </div>
  )
}

/** The answer being written, styled exactly like the assistant bubble it becomes. */
function LiveAnswerBubble({ live }: { live: LiveAnswer }) {
  const primary = live.avatars[0]
  return (
    <div className="flex flex-col gap-0.5 w-full items-start" aria-busy="true">
      <div
        className={cn(
          'chat-bubble chat-bubble-assistant text-chat folk-card folk-shadow rounded-[4px_16px_16px_16px]',
          primary ? `avatar-glass-${primary.toLowerCase()}` : '',
        )}
        style={{ color: 'var(--kajal)' }}
        lang={textLang(live.text) === 'hi' ? 'hi' : undefined}
      >
        {live.avatars.length > 0 && <AvatarChips avatars={live.avatars} />}
        <MarkdownMessage text={live.text.replace(LIVE_PHASE_RE, '')} live />
      </div>
    </div>
  )
}

function UserMessageText({ text }: { text: string }) {
  return (
    <p className="leading-relaxed whitespace-pre-wrap break-words">
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
        <div className="flex items-center gap-1.5 font-mono text-[10.5px]"
          style={{ color: 'var(--ink-40)' }}>
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
            const colour = AVATAR_COLOURS[avatarName] ?? 'rgba(var(--rgb-ink),0.6)'
            return (
              <span
                key={name}
                className="text-[10px] font-mono px-1.5 py-px rounded-sm leading-tight"
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

// Quiet by default; 44 px tall on phones so a thumb finds them.
const ACTION_BTN = cn(
  'flex items-center gap-1.5 px-2.5 min-h-[44px] sm:min-h-[30px] rounded-md',
  'text-[13px] sm:text-[11.5px] leading-none',
  'border border-transparent hover:border-kajal/20',
  'text-kajal/60 hover:text-kajal/85',
  'transition-colors duration-150 cursor-pointer bg-transparent',
  'hover:bg-kajal/5',
)

interface Props {
  userId: string
  profile: FamilyProfile
  onSwitchProfile: () => void
  messages: Message[]
  avatars: Record<AvatarName, AvatarStatus>
  streaming: boolean
  /** Streamed text of the answer being written (null until text arrives). */
  liveAnswer?: LiveAnswer | null
  error: string | null
  onSend: (query: string, attachments?: ChatAttachment[]) => void
  stop: () => void
  onClear?: () => void
  onOpenVoice?: () => void
  /** Phone: the avatar in the header opens You (not a sign-out). */
  onOpenProfile?: () => void
  activeArtifact?: ActiveArtifactSession | null
  onCloseArtifact?: () => void
  activeWorkflow?: WorkflowRun | null
  onOpenWorkflow?: () => void
  onLeaveWorkflow?: () => void
  guidedSession?: GuidedSessionMeta | null
  onGuidedAnswer?: (messageId: string, answer?: string, choiceIndex?: number) => void
  onGuidedSkip?: () => void
  onGuidedExit?: () => void
  /** An approval card was decided or edited: keep the chat's copy current. */
  onApprovalChange?: ApprovalChange
}

/** Within this many pixels of the end, the chat follows new text. */
const FOLLOW_SLACK_PX = 48

const HEADER_BUTTON = 'n-icon-btn transition-colors hover:bg-black/5 dark:hover:bg-white/10'

export function ChatPanel({
  userId,
  profile,
  onSwitchProfile,
  messages,
  avatars,
  streaming,
  liveAnswer = null,
  error,
  onSend,
  stop,
  onClear,
  onOpenVoice,
  onOpenProfile,
  activeArtifact,
  onCloseArtifact,
  activeWorkflow,
  onOpenWorkflow,
  onLeaveWorkflow,
  guidedSession,
  onGuidedAnswer,
  onGuidedSkip,
  onGuidedExit,
  onApprovalChange,
}: Props) {
  const isMobile = useIsMobile()
  const [input, setInput] = useState('')
  const [pendingBatches, setPendingBatches] = useState<ChatAttachmentBatch[]>([])
  const [uploading, setUploading] = useState(false)
  const [attachmentMenuOpen, setAttachmentMenuOpen] = useState(false)
  // Phone: actions show under the latest answer, and under any bubble tapped.
  const [revealedId, setRevealedId] = useState<string | null>(null)
  const scrollerRef = useRef<HTMLDivElement>(null)
  const followRef = useRef(true)
  const lastScrollTopRef = useRef(0)
  const [showJump, setShowJump] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const folderRef = useRef<HTMLInputElement>(null)
  const attachmentMenuRef = useRef<HTMLDivElement>(null)
  const tts = useTTS()

  // Follow the answer while the reader is at the end. Scrolling up, even a
  // little, stops following at once (no yanking back on the next chunk) and
  // offers "Latest" instead; reaching the end again resumes following.
  const scrollToBottom = (smooth = false) => {
    const el = scrollerRef.current
    if (!el) return
    const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    el.scrollTo({ top: el.scrollHeight, behavior: smooth && !reduced ? 'smooth' : 'auto' })
    followRef.current = true
    setShowJump(false)
  }

  const handleScroll = () => {
    const el = scrollerRef.current
    if (!el) return
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight
    if (distance <= FOLLOW_SLACK_PX) {
      followRef.current = true
      setShowJump(false)
    } else if (el.scrollTop < lastScrollTopRef.current - 2) {
      // Only a person scrolls up; the app itself only ever scrolls down.
      followRef.current = false
    }
    lastScrollTopRef.current = el.scrollTop
  }

  const liveText = streaming ? liveAnswer?.text ?? '' : ''

  // Veena's face in the header: thinking while Narad chooses, working (with
  // that avatar's string humming) while one works, a wince on an error.
  const workingAvatar = Object.values(avatars).find(a => a.state === 'active')?.name ?? null
  const veenaMood: VeenaMood = error ? 'oops' : workingAvatar ? 'working' : streaming ? 'thinking' : 'calm'

  useEffect(() => {
    if (followRef.current) scrollToBottom()
    else setShowJump(true)
  }, [messages, liveText, streaming])

  // The keyboard opening (or a card growing) shrinks the list: stay at the end.
  useEffect(() => {
    const el = scrollerRef.current
    if (!el || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(() => {
      if (followRef.current) el.scrollTop = el.scrollHeight
    })
    observer.observe(el)
    return () => observer.disconnect()
  }, [])

  // G7: guru mode defaults to voice — auto-speak each new atom's narration in
  // Krishna's voice. History restored on page load is seeded as already-spoken
  // so a refresh never replays old lessons.
  const spokenRef = useRef<Set<string> | null>(null)
  useEffect(() => {
    if (spokenRef.current === null) {
      spokenRef.current = new Set(messages.filter(m => m.guru).map(m => m.id))
      return
    }
    const last = messages[messages.length - 1]
    if (!last?.guru || spokenRef.current.has(last.id)) return
    spokenRef.current.add(last.id)
    if (last.guru.kind === 'step' && last.guru.step.narration) {
      tts.speak(last.guru.step.narration, 'Krishna', last.id, 'en')
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messages])

  useEffect(() => {
    if (!attachmentMenuOpen) return
    const closeMenu = (event: MouseEvent) => {
      if (!attachmentMenuRef.current?.contains(event.target as Node)) {
        setAttachmentMenuOpen(false)
      }
    }
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') setAttachmentMenuOpen(false) }
    document.addEventListener('mousedown', closeMenu)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', closeMenu)
      document.removeEventListener('keydown', onKey)
    }
  }, [attachmentMenuOpen])

  const uploadSelection = async (fileList: FileList, source: 'files' | 'folder') => {
    const selected = Array.from(fileList).filter(file => {
      if (source !== 'folder') return true
      const relativePath = file.webkitRelativePath || file.name
      const parts = relativePath.split('/')
      return !parts.some(part => IGNORED_FOLDER_PARTS.has(part)) && !relativePath.endsWith('/.DS_Store')
    })
    if (selected.length === 0) {
      toast.error('No usable files found in that selection.')
      return
    }
    if (selected.length > MAX_UPLOAD_FILES) {
      toast.error(`That selection has ${selected.length} files. Choose a folder with ${MAX_UPLOAD_FILES} files or fewer.`)
      return
    }

    setUploading(true)
    setAttachmentMenuOpen(false)
    try {
      const form = new FormData()
      selected.forEach(file => form.append('files', file, file.name))
      form.append('user_id', userId)
      form.append('source', source)
      form.append('relative_paths', JSON.stringify(
        selected.map(file => file.webkitRelativePath || file.name)
      ))
      const response = await apiFetch('/chat/attachments', { method: 'POST', body: form })
      const payload = await response.json().catch(() => ({})) as ChatAttachmentBatch & { detail?: string }
      if (!response.ok) throw new Error(payload.detail || `Upload failed (HTTP ${response.status})`)
      setPendingBatches(current => [...current, payload])
    } catch (error) {
      toast.error('Could not attach that selection.', {
        description: error instanceof Error ? error.message : 'Upload failed.',
      })
    } finally {
      setUploading(false)
    }
  }

  const removeBatch = (batchId: string) => {
    setPendingBatches(current => current.filter(batch => batch.batch_id !== batchId))
    apiFetch(apiUrl(`/chat/attachment-batches/${batchId}`, { user_id: userId }), {
      method: 'DELETE',
    }).catch(() => {})
  }

  const handleSend = () => {
    const attachments = pendingBatches.flatMap(batch => batch.attachments)
    const q = input.trim() || (attachments.length > 0 ? 'Review the attached inputs and summarize what matters.' : '')
    if (!q || streaming || uploading) return
    // A lesson speaks its next step by itself, after the reply arrives; the
    // phone allows that only if audio was unlocked inside this tap.
    if (guidedSession) unlockAudio()
    onSend(q, attachments)
    setInput('')
    setPendingBatches([])
    setRevealedId(null)
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
    if (prev?.role === 'user') onSend(prev.text, prev.attachments)
  }

  const [copiedId, setCopiedId] = useState<string | null>(null)
  const handleCopy = (msgId: string, text: string) => {
    navigator.clipboard.writeText(text).then(() => {
      setCopiedId(msgId)
      setTimeout(() => setCopiedId(id => id === msgId ? null : id), 1500)
    })
  }

  /** A tap on a bubble (not on a link or button inside it) shows its actions. */
  const revealActions = (event: React.MouseEvent, id: string) => {
    if (!isMobile || (event.target as HTMLElement).closest('a, button, input, textarea, summary')) return
    setRevealedId(current => (current === id ? null : id))
  }

  const activeAvatar = Object.values(avatars).find(a => a.state === 'active') ?? null
  const pendingAttachmentCount = pendingBatches.reduce((sum, batch) => sum + batch.file_count, 0)
  const liveUrls = Array.from(new Set((input.match(LIVE_URL_RE) ?? []).map(url => url.replace(/[.,;:!?]+$/, ''))))
  const lastAssistantId = [...messages].reverse().find(m => m.role === 'assistant' && !m.guru)?.id ?? null
  const canSend = !streaming && !uploading && (Boolean(input.trim()) || pendingAttachmentCount > 0)

  return (
    <div className="flex flex-col h-full overflow-hidden" style={{ background: 'var(--paper)' }}>

      {/* Header — frosted, beaded lower edge; Veena's face shows what Narad is doing */}
      <header
        className="chrome-frost flex items-center gap-2 sm:gap-3 pl-3 pr-1.5 sm:px-5 flex-shrink-0 relative overflow-hidden"
        style={{ minHeight: 'calc(56px + env(safe-area-inset-top))', paddingTop: 'env(safe-area-inset-top)' }}
      >
        <Veena variant="face" size={isMobile ? 34 : 38} mood={veenaMood} active={workingAvatar} />
        <div className="flex flex-col gap-0 min-w-0">
          <span
            className="label-hero leading-none"
            style={{ color: 'var(--on-chrome)', letterSpacing: '-0.01em', fontSize: isMobile ? 20 : 22 }}
          >
            NARAD.OS
          </span>
          <span aria-hidden="true" className="text-[12px] leading-tight mt-0.5" style={{ color: 'var(--on-chrome-muted)', fontFamily: 'var(--font-deva)' }}>
            नारद  अवतारा
          </span>
        </div>
        <div className="ml-auto z-10 flex items-center gap-0.5 sm:gap-1.5" style={{ color: 'rgba(var(--rgb-on-chrome),0.82)' }}>
          {onOpenVoice && (
            <button type="button" onClick={onOpenVoice} className={HEADER_BUTTON} aria-label="Talk to Narad (voice mode)" title="Voice mode">
              <Mic size={19} />
            </button>
          )}
          {onClear && messages.length > 0 && (
            <button type="button" onClick={onClear} className={HEADER_BUTTON} aria-label="Start a new chat" title="New chat">
              <RotateCcw size={18} />
            </button>
          )}
          {isMobile && onOpenProfile ? (
            <button
              type="button"
              onClick={onOpenProfile}
              className={HEADER_BUTTON}
              aria-label={`You: ${profile.display_name}`}
            >
              <span
                aria-hidden="true"
                className="grid place-items-center rounded-full font-mono text-[12px] font-extrabold"
                style={{ width: 30, height: 30, color: '#fffaf0', background: PROFILE_COLORS[profile.color] || PROFILE_COLORS.sindoor, boxShadow: 'inset 0 0 0 1px rgba(255,255,255,0.2)' }}
              >
                {profile.initial}
              </span>
            </button>
          ) : (
            <ProfileBadge profile={profile} onSwitch={onSwitchProfile} compact={isMobile} />
          )}
        </div>
      </header>

      {/* The teaching workflow stays in the primary chat instead of a separate surface. */}
      {guidedSession && (
        <div
          className="flex items-center gap-2 pl-4 pr-1 flex-shrink-0"
          style={{
            minHeight: 48,
            background: 'rgba(var(--rgb-matsya),0.08)',
            borderBottom: '1px solid rgba(var(--rgb-matsya),0.2)',
          }}
        >
          <GraduationCap size={15} aria-hidden="true" style={{ color: 'var(--avatar-matsya)' }} />
          <span className="font-mono text-[11.5px] uppercase tracking-wider" style={{ color: 'var(--avatar-matsya)' }}>
            Teach
          </span>
          <span className="text-[14px] truncate flex-1" style={{ color: 'var(--kajal)', opacity: 0.8 }}>
            {guidedSession.topic}
          </span>
          <button
            type="button"
            onClick={() => onGuidedExit?.()}
            className="n-btn n-btn-sm"
            style={{ minHeight: 40, color: 'var(--avatar-matsya)', borderColor: 'rgba(var(--rgb-matsya),0.3)', background: 'transparent' }}
            title="Exit the teaching workflow (or type /exit)"
          >
            Exit
          </button>
        </div>
      )}

      {/* Messages */}
      <div className="relative flex-1 min-h-0">
      <div
        ref={scrollerRef}
        onScroll={handleScroll}
        className="h-full overflow-y-auto px-3 sm:px-4 pt-4 pb-6 flex flex-col gap-3"
        style={{ background: 'var(--paper)', overscrollBehavior: 'contain' }}
        role="log"
        aria-live="polite"
        aria-relevant="additions"
        aria-busy={streaming}
        aria-label="Conversation"
      >
        {messages.length === 0 && (
          <div className="flex flex-col items-center justify-center gap-3 my-auto py-6 px-2 text-center">
            <Veena mood="hello" size={isMobile ? 104 : 120} />
            <p className="text-[36px] leading-tight" lang="hi" style={{ fontFamily: 'var(--font-deva)', color: 'var(--sindoor)' }}>नमस्ते</p>
            <p className="label-hero text-[17px] text-balance max-w-[320px]" style={{ color: 'var(--ink-70)' }}>
              Ask anything — Narad plucks the right string.
            </p>
            <div className="grid grid-cols-2 gap-2.5 mt-3 w-full max-w-[440px] text-left">
              {AVATAR_INTROS.map(a => (
                <button
                  key={a.name}
                  type="button"
                  onClick={() => handleEdit(a.prompt)}
                  className="folk-card flex items-center gap-2 px-2.5 py-3 cursor-pointer active:scale-[0.97] transition-transform duration-150"
                  style={{ minHeight: 64 }}
                >
                  <AvatarGlyph name={a.name} size={28} style={{ flex: 'none' }} />
                  <span className="flex flex-col min-w-0">
                    <span className="label-hero text-[15px] leading-tight" style={{ color: `var(--avatar-${a.name.toLowerCase()})` }}>{a.name}</span>
                    <span className="text-[12.5px] leading-snug" style={{ color: 'var(--ink-55)' }}>{a.role}</span>
                  </span>
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map(msg => {
          // Anumati: a side effect waiting for this person's OK.
          if (msg.role === 'approval' && msg.approval) {
            return (
              <div key={msg.id} className="chat-card">
                <ApprovalCard proposal={msg.approval} onChange={onApprovalChange} />
              </div>
            )
          }

          // Workflow Paths: a guided path offered for this ask (Start / Not now).
          if (msg.role === 'path' && msg.pathSuggestion) {
            return (
              <div key={msg.id} className="w-full max-w-[92%] self-start">
                <PathSuggestionCard suggestion={msg.pathSuggestion} userId={userId} />
              </div>
            )
          }

          // Kriya: an errand running on the Mac, with its live view and Stop.
          if (msg.role === 'task' && msg.task) {
            return (
              <div key={msg.id} className="chat-card">
                <TaskCard task={msg.task} />
              </div>
            )
          }

          // G7: guided-mode cards render as their own wide block, not a bubble.
          if (msg.role === 'assistant' && msg.guru) {
            return (
              <div key={msg.id} className="chat-card">
                <GuruMessage
                  payload={msg.guru}
                  busy={streaming}
                  speaking={tts.playingId === `${msg.id}:en` && tts.state !== 'idle'}
                  onAnswer={(answer, choiceIndex) => {
                    unlockAudio()  // the next step is spoken without another tap
                    onGuidedAnswer?.(msg.id, answer, choiceIndex)
                  }}
                  onSkip={() => onGuidedSkip?.()}
                  onReplayVoice={() => {
                    const narration = msg.guru?.kind === 'step' ? msg.guru.step.narration : msg.text
                    if (narration) tts.speak(narration, 'Krishna', msg.id, 'en')
                  }}
                />
              </div>
            )
          }

          const primaryAvatar = msg.avatarsInvolved?.[0]
          const avatarClass = primaryAvatar
            ? `avatar-glass-${primaryAvatar.toLowerCase()}`
            : ''
          const hindi = textLang(msg.text) === 'hi'
          const showActions = !isMobile || revealedId === msg.id || (msg.id === lastAssistantId && !streaming)

          return (
            <div
              key={msg.id}
              className={cn(
                'chat-row group/bubble flex flex-col gap-0.5 w-full',
                msg.role === 'user' ? 'items-end' : 'items-start'
              )}
            >
              {/* Bubble */}
              <div
                className={cn(
                  'chat-bubble text-chat',
                  msg.role === 'user'
                    ? 'chat-bubble-user'
                    : cn('chat-bubble-assistant folk-card folk-shadow rounded-[4px_16px_16px_16px]', avatarClass)
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
                lang={hindi ? 'hi' : undefined}
                onClick={event => revealActions(event, msg.id)}
              >
                {/* Avatar tags */}
                {msg.role === 'assistant' && msg.avatarsInvolved && msg.avatarsInvolved.length > 0 && (
                  <AvatarChips avatars={msg.avatarsInvolved} />
                )}

                {msg.role === 'assistant'
                  ? <MarkdownMessage text={msg.text} />
                  : <UserMessageText text={msg.text} />
                }
                {msg.role === 'user' && <MessageAttachments attachments={msg.attachments} />}
              </div>

              {/* Action buttons + token ticker */}
              <div
                className={cn(
                  'flex flex-col gap-0.5 max-w-full',
                  msg.role === 'user' ? 'items-end' : 'items-start'
                )}
              >
                {showActions && (
                <div className="msg-actions flex flex-wrap gap-0.5 sm:gap-1 sm:opacity-0 sm:group-hover/bubble:opacity-100 sm:focus-within:opacity-100 transition-opacity duration-150">
                  {msg.role === 'user' && (
                    <button
                      type="button"
                      className={ACTION_BTN}
                      onClick={() => handleEdit(msg.text)}
                      aria-label="Edit and send again"
                      title="Edit and resend"
                    >
                      <Pencil size={14} aria-hidden="true" />
                      Edit
                    </button>
                  )}
                  {msg.role === 'assistant' && (
                    <button
                      type="button"
                      className={ACTION_BTN}
                      onClick={() => handleCopy(msg.id, msg.text)}
                      aria-label={copiedId === msg.id ? 'Copied' : 'Copy the answer'}
                      title="Copy to clipboard"
                    >
                      {copiedId === msg.id
                        ? <><Check size={14} aria-hidden="true" />Copied</>
                        : <><Copy size={14} aria-hidden="true" />Copy</>
                      }
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
                          type="button"
                          className={ACTION_BTN}
                          onClick={() => tts.speak(msg.text, voiceAvatar, msg.id, 'en')}
                          aria-label={isEnPlaying ? 'Stop speaking' : 'Read aloud'}
                          title={isEnPlaying ? 'Stop' : `Speak as ${voiceAvatar}`}
                          style={isEnPlaying ? { color: 'var(--sindoor)', borderColor: 'rgba(var(--rgb-sindoor),0.35)' } : {}}
                        >
                          {isEnLoading
                            ? <><Loader size={14} className="animate-spin" aria-hidden="true" />Loading</>
                            : isEnPlaying
                            ? <><VolumeX size={14} aria-hidden="true" />Stop</>
                            : <><Volume2 size={14} aria-hidden="true" />Listen</>
                          }
                        </button>
                        <button
                          type="button"
                          className={ACTION_BTN}
                          onClick={() => tts.speak(msg.text, voiceAvatar, msg.id, 'hi')}
                          aria-label={isHiPlaying ? 'Stop Hindi' : 'Listen in Hindi'}
                          title={isHiPlaying ? 'Stop Hindi' : 'Speak in Hindi'}
                          style={isHiPlaying ? { color: 'var(--sindoor)', borderColor: 'rgba(var(--rgb-sindoor),0.35)' } : {}}
                        >
                          {isHiLoading
                            ? <><Loader size={14} className="animate-spin" aria-hidden="true" />Loading</>
                            : isHiPlaying
                            ? <><VolumeX size={14} aria-hidden="true" />Stop</>
                            : <><Volume2 size={14} aria-hidden="true" /><span lang="hi">हिन्दी</span></>
                          }
                        </button>
                      </>
                    )
                  })()}
                  {msg.role === 'assistant' && !streaming && (
                    <button
                      type="button"
                      className={ACTION_BTN}
                      onClick={() => handleRestart(msg.id)}
                      aria-label="Ask this again"
                      title="Restart from this prompt"
                    >
                      <RotateCcw size={14} aria-hidden="true" />
                      Retry
                    </button>
                  )}
                </div>
                )}
                {msg.role === 'assistant' && !isMobile && (
                  <TokenTicker
                    usage={msg.usage}
                    tokenEstimate={msg.tokenEstimate}
                    totalDurationMs={msg.totalDurationMs}
                    clientTokPerSec={msg.clientTokPerSec}
                    avatarsInvolved={msg.avatarsInvolved}
                    avatarLatencies={msg.avatarLatencies}
                  />
                )}
                {msg.role === 'assistant' && <MessageFooter message={msg} userId={userId} />}
              </div>
            </div>
          )
        })}

        {/* Values read from a document, waiting to be checked against their crops. */}
        <DocumentReviewHost />

        {/* The answer as it is written; the final reply replaces it in place. */}
        {liveText && liveAnswer && <LiveAnswerBubble live={liveAnswer} />}

        {/* Streaming indicator — breathes in the active avatar's colour */}
        {streaming && (() => {
          const activeName = activeAvatar?.name
          const streamColour = activeName && isAvatarName(activeName)
            ? `var(--avatar-${activeName.toLowerCase()})`
            : 'var(--sindoor)'
          const streamRgb = activeName && isAvatarName(activeName)
            ? AVATAR_RGB[activeName]
            : 'var(--rgb-sindoor)'
          return (
            <div className="flex flex-col gap-2 w-full sm:max-w-[82%]" role="status">

              {/* Active avatar label + task */}
              <div className="flex items-center gap-2 px-1 min-w-0">
                <AvatarGlyph name={activeName && isAvatarName(activeName) ? activeName : 'narad'} size={20} live />
                <span className="text-[13px] font-semibold flex-shrink-0" style={{ color: streamColour }}>
                  {activeAvatar ? activeAvatar.name : 'Narad'}
                </span>
                <span className="text-[13px] truncate flex-1" style={{ color: 'var(--ink-55)' }}>
                  {activeAvatar?.task || (liveText ? 'is writing…' : 'is thinking…')}
                </span>
              </div>

              {/* Progress bar */}
              <div
                className="h-[2px] rounded-full overflow-hidden mx-1"
                style={{ background: 'var(--ink-08)' }}
                aria-hidden="true"
              >
                <div
                  className="h-full w-[35%] rounded-full"
                  style={{
                    background: streamColour,
                    animation: 'progress-sweep 1.6s ease-in-out infinite',
                  }}
                />
              </div>

              {/* Breathing dots until text streams in */}
              {!liveText && (
                <div className="folk-card flex items-center gap-1.5 px-4 py-3.5 rounded w-fit" aria-hidden="true">
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
              )}

            </div>
          )
        })()}

        {error && (
          <div
            role="alert"
            className="flex items-start gap-2 px-3.5 py-3 rounded-lg organic-border mx-1"
            style={{
              background: 'rgba(var(--rgb-sindoor),0.06)',
              borderColor: 'rgba(var(--rgb-sindoor),0.30)',
              color: 'var(--sindoor)',
            }}
          >
            <TriangleAlert size={16} className="flex-shrink-0 mt-0.5" aria-hidden="true" />
            <span className="text-[14px] leading-snug break-words">{error}</span>
          </div>
        )}

      </div>

      {/* "Latest" — appears when new content arrives while scrolled up */}
      {showJump && (
        <button
          type="button"
          onClick={() => scrollToBottom(true)}
          className="absolute bottom-3 left-1/2 -translate-x-1/2 z-10 flex items-center gap-1.5 px-4 rounded-full text-[14px] font-semibold cursor-pointer"
          style={{
            minHeight: 44,
            background: 'var(--chrome)',
            color: 'var(--on-chrome)',
            border: '1px solid rgba(var(--rgb-on-chrome),0.18)',
            boxShadow: '0 4px 14px rgba(0,0,0,0.25)',
          }}
        >
          <ArrowDown size={16} aria-hidden="true" />
          Latest
        </button>
      )}
      </div>

      {/* Input area */}
      <div
        className="flex flex-col gap-2 px-3 sm:px-4 pt-2.5 pb-2.5 flex-shrink-0"
        style={{
          background: 'var(--speckle)',
          borderTop: '1px solid var(--line)',
        }}
      >
        {activeWorkflow && (
          <div
            className="flex items-center justify-between gap-2 rounded-lg pl-3 pr-1"
            style={{
              minHeight: 52,
              background: `linear-gradient(90deg, ${activeWorkflow.definition.accent}14, var(--surface-raised))`,
              border: `1px solid ${activeWorkflow.definition.accent}30`,
            }}
          >
            <button type="button" onClick={onOpenWorkflow} className="min-w-0 flex-1 text-left py-1.5" style={{ background: 'transparent', border: 0, cursor: 'pointer', minHeight: 44 }}>
              <div className="font-mono text-[11px] uppercase tracking-[0.1em] truncate" style={{ color: activeWorkflow.definition.accent }}>
                Path · {activeWorkflow.definition.title} · {activeWorkflow.progress_percent}%
              </div>
              <div className="text-[14px] font-semibold truncate" style={{ color: 'var(--kajal)' }}>
                {activeWorkflow.current_stage?.title || (activeWorkflow.status === 'completed' ? 'Path complete' : activeWorkflow.title)}
              </div>
            </button>
            {onLeaveWorkflow && (
              <button type="button" onClick={onLeaveWorkflow} className="n-icon-btn" style={{ color: 'var(--ink-55)' }} aria-label="Leave this path's context">
                <X size={17} />
              </button>
            )}
          </div>
        )}

        {activeArtifact && (
          <div
            className="flex items-center justify-between gap-2 rounded-lg pl-3 pr-1 py-1.5"
            style={{
              background: 'var(--surface-raised)',
              border: '1px solid var(--line)',
            }}
          >
            <div className="min-w-0">
              <div className="font-mono text-[11px] uppercase tracking-[0.1em]" style={{ color: 'var(--ink-55)' }}>
                Active artifact
              </div>
              <div className="text-[14px] font-semibold truncate" style={{ color: 'var(--kajal)' }}>
                {activeArtifact.artifactType === 'flashcards' ? 'Flashcards' : 'Concept Map'} · {activeArtifact.topic}
              </div>
              <div className="text-[12.5px] truncate" style={{ color: 'var(--ink-55)' }}>
                Explicit edit prompts only: “add one more card…” or “add a node for…”
              </div>
            </div>
            {onCloseArtifact && (
              <button
                type="button"
                onClick={onCloseArtifact}
                className="n-icon-btn"
                style={{ color: 'var(--ink-55)' }}
                aria-label="Close the artifact panel"
              >
                <X size={17} />
              </button>
            )}
          </div>
        )}

        {(pendingBatches.length > 0 || liveUrls.length > 0) && (
          <div className="flex flex-wrap gap-2">
            {pendingBatches.map(batch => {
              const first = batch.attachments[0]
              const singleImage = batch.file_count === 1 && first?.kind === 'image'
              return (
                <div
                  key={batch.batch_id}
                  className="group/attachment relative flex items-center gap-2.5 rounded-lg pl-2.5 pr-1 py-1.5 max-w-full sm:max-w-[280px]"
                  style={{
                    background: 'var(--surface-raised)',
                    border: '1px solid var(--line)',
                  }}
                  title={batch.source === 'folder' ? `${batch.file_count} files from ${batch.label}` : batch.label}
                >
                  {singleImage ? (
                    <img
                      src={apiPath(first.content_url)}
                      alt=""
                      className="w-9 h-9 rounded object-cover flex-shrink-0"
                    />
                  ) : (
                    <span
                      aria-hidden="true"
                      className="w-9 h-9 rounded flex items-center justify-center flex-shrink-0"
                      style={{ background: 'var(--ink-05)', color: 'var(--ink-55)' }}
                    >
                      {batch.source === 'folder'
                        ? <FolderOpen size={16} />
                        : first ? <AttachmentIcon kind={first.kind} size={16} /> : <Files size={16} />}
                    </span>
                  )}
                  <div className="min-w-0 flex-1">
                    <div className="text-[13.5px] font-medium truncate" style={{ color: 'var(--kajal)' }}>
                      {batch.label}
                    </div>
                    <div className="text-[12px]" style={{ color: 'var(--ink-55)' }}>
                      {batch.file_count === 1 ? formatBytes(batch.size_bytes) : `${batch.file_count} files · ${formatBytes(batch.size_bytes)}`}
                    </div>
                  </div>
                  <button
                    type="button"
                    className="n-icon-btn"
                    style={{ color: 'var(--ink-55)' }}
                    onClick={() => removeBatch(batch.batch_id)}
                    aria-label={`Remove ${batch.label}`}
                  >
                    <X size={16} />
                  </button>
                </div>
              )
            })}
            {liveUrls.map(url => {
              let label = url
              try { label = new URL(url).hostname.replace(/^www\./, '') } catch { /* show the URL */ }
              return (
                <div
                  key={url}
                  className="inline-flex items-center gap-2 rounded-lg px-2.5 py-2 max-w-full sm:max-w-[240px]"
                  style={{
                    background: 'rgba(var(--rgb-mor),0.07)',
                    border: '1px solid rgba(var(--rgb-mor),0.2)',
                    color: 'var(--mor)',
                  }}
                  title={`${url} · Matsya will retrieve the live page`}
                >
                  <Link2 size={14} className="flex-shrink-0" aria-hidden="true" />
                  <span className="text-[13px] truncate">{label}</span>
                  <span className="font-mono text-[10.5px] uppercase tracking-wide opacity-70">live</span>
                </div>
              )
            })}
          </div>
        )}

        <div className="flex items-end gap-2">
        {/* Browser inputs stay separate because folder picking uses webkitdirectory. */}
        <input
          ref={fileRef}
          type="file"
          multiple
          className="hidden"
          onChange={e => { if (e.target.files) void uploadSelection(e.target.files, 'files'); e.target.value = '' }}
        />
        <input
          ref={folderRef}
          type="file"
          multiple
          className="hidden"
          {...({ webkitdirectory: '', directory: '' } as React.InputHTMLAttributes<HTMLInputElement>)}
          onChange={e => { if (e.target.files) void uploadSelection(e.target.files, 'folder'); e.target.value = '' }}
        />
        <div ref={attachmentMenuRef} className="relative flex-shrink-0">
          {attachmentMenuOpen && (
            <div
              role="menu"
              className="absolute bottom-[52px] left-0 w-52 rounded-lg p-1.5 z-30"
              style={{
                background: 'var(--paper)',
                border: '1px solid var(--ink-12)',
                boxShadow: '0 12px 32px rgba(0,0,0,0.16)',
              }}
            >
              <button
                type="button"
                role="menuitem"
                onClick={() => fileRef.current?.click()}
                className="w-full flex items-center gap-3 rounded-md px-3 text-left hover:bg-kajal/5"
                style={{ minHeight: 44 }}
              >
                <Files size={17} aria-hidden="true" style={{ color: 'var(--sindoor)' }} />
                <span className="text-[14px]" style={{ color: 'var(--kajal)' }}>Photos, files or documents</span>
              </button>
              <button
                type="button"
                role="menuitem"
                onClick={() => folderRef.current?.click()}
                className="w-full flex items-center gap-3 rounded-md px-3 text-left hover:bg-kajal/5"
                style={{ minHeight: 44 }}
              >
                <FolderOpen size={17} aria-hidden="true" style={{ color: 'var(--mor)' }} />
                <span className="text-[14px]" style={{ color: 'var(--kajal)' }}>A folder</span>
              </button>
            </div>
          )}
          <button
            type="button"
            onClick={() => setAttachmentMenuOpen(open => !open)}
            disabled={streaming || uploading}
            aria-label="Attach photos, files or a folder"
            aria-haspopup="menu"
            aria-expanded={attachmentMenuOpen}
            className="w-11 h-11 rounded-lg flex-shrink-0 flex items-center justify-center border transition-colors cursor-pointer disabled:cursor-not-allowed"
            style={{
              background: 'var(--field)',
              borderColor: pendingAttachmentCount > 0 ? 'rgba(var(--rgb-sindoor),0.4)' : 'var(--ink-12)',
              color: 'var(--ink-70)',
              opacity: (streaming || uploading) ? 0.4 : 1,
            }}
            title="Attach files or a folder"
          >
            {uploading ? <Loader size={18} className="animate-spin" /> : <Paperclip size={18} />}
          </button>
        </div>
        <textarea
          ref={textareaRef}
          value={input}
          onChange={autoResize}
          onKeyDown={handleKey}
          placeholder={streaming ? 'Narad is answering…' : 'Ask Narad anything'}
          aria-label="Message to Narad"
          disabled={streaming}
          rows={1}
          enterKeyHint="send"
          className={cn(
            'chat-composer flex-1 min-w-0 resize-none px-3.5 py-2.5 rounded-lg',
            'leading-snug placeholder:opacity-50',
            'outline-none transition-[border-color,box-shadow] duration-150',
            'min-h-[44px] max-h-[140px]',
            streaming && 'opacity-60'
          )}
          style={{
            background: 'var(--field)',
            color: 'var(--kajal)',
            border: '1px solid var(--ink-12)',
            boxShadow: 'none',
          }}
        />
        {streaming ? (
          <button
            type="button"
            onClick={stop}
            aria-label="Stop the answer"
            title="Stop"
            className="w-11 h-11 rounded-lg flex-shrink-0 flex items-center justify-center cursor-pointer border"
            style={{ background: 'var(--field)', color: 'var(--kesari)', borderColor: 'color-mix(in srgb, var(--kesari) 45%, transparent)' }}
          >
            <Square size={16} fill="currentColor" aria-hidden="true" />
          </button>
        ) : (
          <button
            type="button"
            onClick={handleSend}
            disabled={!canSend}
            aria-label="Send"
            className="w-11 h-11 rounded-lg flex-shrink-0 flex items-center justify-center border-0 cursor-pointer disabled:cursor-not-allowed transition-opacity"
            style={{ background: 'var(--sindoor)', color: '#fff', opacity: canSend ? 1 : 0.4 }}
          >
            <ArrowUp size={20} strokeWidth={2.4} aria-hidden="true" />
          </button>
        )}
        </div>
      </div>
    </div>
  )
}
