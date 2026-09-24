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
import type { TTSAvatar } from '../hooks/useTTS'
import { MahatiLogo } from './MahatiLogo'
import { ZigzagBank } from './Motifs'
import { GuruMessage } from './GuruCards'
import { ApprovalCard, type ApprovalChange } from './ApprovalCard'
import { MessageFooter } from './MessageFooter'
import { cn } from '@/lib/utils'
import {
  Archive,
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
  Volume2,
  VolumeX,
  X,
} from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { AVATAR_COLOURS, AVATAR_RGB, DEVA, isAvatarName } from '@/lib/avatara-constants'
import { apiFetch, apiPath, apiUrl, type WorkflowRun } from '@/lib/api'
import type { FamilyProfile } from '@/lib/api'
import { useIsMobile } from '@/hooks/useIsMobile'
import { ProfileBadge } from './ProfileBadge'
import { toast } from 'sonner'

const SUGGESTIONS: Array<{ label: string; prompt: string }> = [
  { label: 'Plan my week',        prompt: 'Plan my week from my calendar and open tasks.' },
  { label: 'Research a topic',    prompt: 'Research the latest on ' },
  { label: 'Teach me something',  prompt: '/teach me ' },
  { label: 'Automate something',  prompt: 'Write a script that ' },
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
})

function AvatarChips({ avatars }: { avatars: AvatarName[] }) {
  return (
    <div className="flex flex-wrap gap-1.5 mb-2">
      {avatars.map(a => (
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
  )
}

/** The answer being written, styled exactly like the assistant bubble it becomes. */
function LiveAnswerBubble({ live }: { live: LiveAnswer }) {
  const primary = live.avatars[0]
  return (
    <div className="flex flex-col gap-0.5 w-full items-start" aria-busy="true">
      <div
        className={cn(
          'max-w-[82%] px-3.5 py-2.5 text-body-sm folk-card folk-shadow rounded-[4px_16px_16px_16px]',
          primary ? `avatar-glass-${primary.toLowerCase()}` : '',
        )}
        style={{ color: 'var(--kajal)' }}
      >
        {live.avatars.length > 0 && <AvatarChips avatars={live.avatars} />}
        <MarkdownMessage text={live.text.replace(LIVE_PHASE_RE, '')} live />
      </div>
    </div>
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
  const scrollerRef = useRef<HTMLDivElement>(null)
  const nearBottomRef = useRef(true)
  const [showJump, setShowJump] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const folderRef = useRef<HTMLInputElement>(null)
  const attachmentMenuRef = useRef<HTMLDivElement>(null)
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

  const liveText = streaming ? liveAnswer?.text ?? '' : ''

  useEffect(() => {
    if (nearBottomRef.current) scrollToBottom()
    else setShowJump(true)
  }, [messages, liveText])

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
    document.addEventListener('mousedown', closeMenu)
    return () => document.removeEventListener('mousedown', closeMenu)
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
    onSend(q, attachments)
    setInput('')
    setPendingBatches([])
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

  const activeAvatar = Object.values(avatars).find(a => a.state === 'active') ?? null
  const pendingAttachmentCount = pendingBatches.reduce((sum, batch) => sum + batch.file_count, 0)
  const liveUrls = Array.from(new Set((input.match(LIVE_URL_RE) ?? []).map(url => url.replace(/[.,;:!?]+$/, ''))))

  return (
    <div className="flex flex-col h-full overflow-hidden" style={{ background: 'var(--paper)' }}>

      {/* Header — dark kajal with Playfair italic */}
      <div
        className="flex items-center gap-2 sm:gap-3 px-3 sm:px-5 py-3 flex-shrink-0 relative overflow-hidden"
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
        <div className="ml-auto z-10 flex items-center gap-1.5">
        <ProfileBadge profile={profile} onSwitch={onSwitchProfile} compact={isMobile} />
        {onOpenVoice && (
          <button
            onClick={onOpenVoice}
            title="Voice mode — talk to Narad"
            className="flex items-center gap-1 px-2 py-1 rounded text-[11px] transition-opacity opacity-50 hover:opacity-100"
            style={{ color: 'rgba(252,250,242,0.7)', background: 'rgba(252,250,242,0.08)', border: '1px solid rgba(252,250,242,0.15)' }}
          >
            <Mic size={12} />
            {!isMobile && 'voice'}
          </button>
        )}
        {onClear && messages.length > 0 && (
          <button
            onClick={onClear}
            title="Clear conversation"
            className="flex items-center gap-1 px-2 py-1 rounded text-[11px] transition-opacity opacity-50 hover:opacity-100"
            style={{ color: 'rgba(252,250,242,0.7)', background: 'rgba(252,250,242,0.08)', border: '1px solid rgba(252,250,242,0.15)' }}
          >
            <RotateCcw size={12} />
            {!isMobile && 'clear'}
          </button>
        )}
        </div>
        {/* Zigzag motif at bottom edge of header */}
        <div className="absolute bottom-0 left-0 w-full overflow-hidden" style={{ height: 16, opacity: 0.12 }}>
          <ZigzagBank color="var(--paper)" className="w-full" />
        </div>
      </div>

      {/* The teaching workflow stays in the primary chat instead of a separate surface. */}
      {guidedSession && (
        <div
          className="flex items-center gap-2 px-4 py-1.5 flex-shrink-0"
          style={{
            background: 'rgba(29,78,216,0.07)',
            borderBottom: '1px solid rgba(29,78,216,0.18)',
          }}
        >
          <GraduationCap size={12} style={{ color: '#1d4ed8' }} />
          <span className="font-mono text-[10.5px] uppercase tracking-wider" style={{ color: '#1d4ed8' }}>
            Teach workflow
          </span>
          <span className="text-[11.5px] truncate flex-1" style={{ color: 'var(--kajal)', fontFamily: 'var(--font-body)', opacity: 0.75 }}>
            {guidedSession.topic}
          </span>
          <button
            onClick={() => onGuidedExit?.()}
            className="text-[10.5px] font-mono px-2 py-0.5 rounded opacity-60 hover:opacity-100 transition-opacity"
            style={{ color: '#1d4ed8', border: '1px solid rgba(29,78,216,0.3)' }}
            title="Exit the teaching workflow (or type /exit)"
          >
            exit
          </button>
        </div>
      )}

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
          // Anumati: a side effect waiting for this person's OK.
          if (msg.role === 'approval' && msg.approval) {
            return (
              <div key={msg.id} className="w-full max-w-[92%] self-start">
                <ApprovalCard proposal={msg.approval} onChange={onApprovalChange} />
              </div>
            )
          }

          // G7: guided-mode cards render as their own wide block, not a bubble.
          if (msg.role === 'assistant' && msg.guru) {
            return (
              <div key={msg.id} className="w-full max-w-[92%] self-start">
                <GuruMessage
                  payload={msg.guru}
                  busy={streaming}
                  speaking={tts.playingId === `${msg.id}:en` && tts.state !== 'idle'}
                  onAnswer={(answer, choiceIndex) => onGuidedAnswer?.(msg.id, answer, choiceIndex)}
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
                {msg.role === 'assistant' && <MessageFooter message={msg} userId={userId} />}
              </div>
            </div>
          )
        })}

        {/* The answer as it is written; the final reply replaces it in place. */}
        {liveText && liveAnswer && <LiveAnswerBubble live={liveAnswer} />}

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

              {/* Breathing dots (until text streams in) + stop button */}
              <div className="flex items-center gap-2.5">
                {!liveText && (
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
                )}
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
        {activeWorkflow && (
          <div
            className="flex items-center justify-between gap-3 rounded px-3 py-2"
            style={{
              background: `linear-gradient(90deg, ${activeWorkflow.definition.accent}10, rgba(255,255,255,0.58))`,
              border: `1px solid ${activeWorkflow.definition.accent}28`,
            }}
          >
            <button type="button" onClick={onOpenWorkflow} className="min-w-0 text-left" style={{ background: 'transparent', border: 0, padding: 0, cursor: 'pointer' }}>
              <div className="font-mono text-[9px] uppercase tracking-[0.13em]" style={{ color: activeWorkflow.definition.accent }}>
                Active path · {activeWorkflow.definition.title}
              </div>
              <div className="text-[12px] font-semibold truncate" style={{ color: 'var(--kajal)' }}>
                {activeWorkflow.current_stage?.title || (activeWorkflow.status === 'completed' ? 'Path complete' : activeWorkflow.title)}
              </div>
            </button>
            <div className="flex items-center gap-2 flex-shrink-0">
              <button type="button" onClick={onOpenWorkflow} className="font-mono text-[9px] uppercase tracking-[0.08em]" style={{ border: 0, background: 'transparent', color: activeWorkflow.definition.accent, cursor: 'pointer' }}>
                {activeWorkflow.progress_percent}% · view
              </button>
              {onLeaveWorkflow && (
                <button onClick={onLeaveWorkflow} className="w-7 h-7 rounded flex items-center justify-center" style={{ border: '1px solid color-mix(in srgb, var(--kajal) 10%, transparent)', color: 'rgba(45,42,38,0.52)', cursor: 'pointer' }} title="Leave path context">
                  <X size={12} />
                </button>
              )}
            </div>
          </div>
        )}

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

        {(pendingBatches.length > 0 || liveUrls.length > 0) && (
          <div className="flex flex-wrap gap-2">
            {pendingBatches.map(batch => {
              const first = batch.attachments[0]
              const singleImage = batch.file_count === 1 && first?.kind === 'image'
              return (
                <div
                  key={batch.batch_id}
                  className="group/attachment relative flex items-center gap-2.5 rounded px-2.5 py-2 max-w-[260px]"
                  style={{
                    background: 'rgba(255,255,255,0.62)',
                    border: '1px solid color-mix(in srgb, var(--kajal) 12%, transparent)',
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
                      className="w-9 h-9 rounded flex items-center justify-center flex-shrink-0"
                      style={{ background: 'rgba(45,42,38,0.06)', color: 'rgba(45,42,38,0.62)' }}
                    >
                      {batch.source === 'folder'
                        ? <FolderOpen size={16} />
                        : first ? <AttachmentIcon kind={first.kind} size={16} /> : <Files size={16} />}
                    </span>
                  )}
                  <div className="min-w-0 pr-4">
                    <div className="text-[11px] font-medium truncate" style={{ color: 'var(--kajal)' }}>
                      {batch.label}
                    </div>
                    <div className="font-mono text-[9px]" style={{ color: 'rgba(45,42,38,0.48)' }}>
                      {batch.file_count === 1 ? formatBytes(batch.size_bytes) : `${batch.file_count} files · ${formatBytes(batch.size_bytes)}`}
                    </div>
                  </div>
                  <button
                    type="button"
                    className="absolute top-1 right-1 w-5 h-5 rounded flex items-center justify-center opacity-55 hover:opacity-100"
                    style={{ color: 'var(--kajal)' }}
                    onClick={() => removeBatch(batch.batch_id)}
                    title="Remove attachment"
                  >
                    <X size={10} />
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
                  className="inline-flex items-center gap-2 rounded px-2.5 py-2 max-w-[220px]"
                  style={{
                    background: 'rgba(15,118,110,0.06)',
                    border: '1px solid rgba(15,118,110,0.18)',
                    color: '#0f766e',
                  }}
                  title={`${url} · Matsya will retrieve the live page`}
                >
                  <Link2 size={13} className="flex-shrink-0" />
                  <span className="font-mono text-[10px] truncate">{label}</span>
                  <span className="font-mono text-[8px] uppercase tracking-wide opacity-60">live</span>
                </div>
              )
            })}
          </div>
        )}

        <div className="flex items-end gap-2.5">
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
              className="absolute bottom-12 left-0 w-44 rounded p-1.5 z-30"
              style={{
                background: 'var(--paper)',
                border: '1px solid color-mix(in srgb, var(--kajal) 14%, transparent)',
                boxShadow: '0 12px 32px rgba(45,42,38,0.14)',
              }}
            >
              <button
                type="button"
                onClick={() => fileRef.current?.click()}
                className="w-full flex items-center gap-2.5 rounded px-2.5 py-2 text-left hover:bg-kajal/5"
              >
                <Files size={14} style={{ color: 'var(--sindoor)' }} />
                <span className="text-[11px]" style={{ color: 'var(--kajal)' }}>Files or documents</span>
              </button>
              <button
                type="button"
                onClick={() => folderRef.current?.click()}
                className="w-full flex items-center gap-2.5 rounded px-2.5 py-2 text-left hover:bg-kajal/5"
              >
                <FolderOpen size={14} style={{ color: '#0f766e' }} />
                <span className="text-[11px]" style={{ color: 'var(--kajal)' }}>Folder</span>
              </button>
            </div>
          )}
          <button
            type="button"
            onClick={() => setAttachmentMenuOpen(open => !open)}
            disabled={streaming || uploading}
            className={cn(
              'w-10 h-10 rounded flex-shrink-0 flex items-center justify-center',
              'transition-[transform,opacity,border-color] duration-150 border outline-none cursor-pointer',
              (streaming || uploading) ? 'opacity-30 cursor-not-allowed' : 'hover:scale-105 active:scale-95'
            )}
            style={{
              background: 'var(--paper)',
              borderColor: pendingAttachmentCount > 0 ? 'rgba(194,65,12,0.34)' : 'color-mix(in srgb, var(--kajal) 12%, transparent)',
              color: 'var(--kajal)',
              opacity: (streaming || uploading) ? 0.3 : 0.62,
              borderRadius: '4px',
            }}
            title="Attach files or a folder"
          >
            {uploading ? <Loader size={15} className="animate-spin" /> : <Paperclip size={15} />}
          </button>
        </div>
        <textarea
          ref={textareaRef}
          value={input}
          onChange={autoResize}
          onKeyDown={handleKey}
          placeholder="Ask Narad or paste a live URL…"
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
          disabled={streaming || uploading || (!input.trim() && pendingAttachmentCount === 0)}
          className={cn(
            'w-10 h-10 rounded flex-shrink-0 flex items-center justify-center',
            'font-bold text-[18px] transition-all duration-150',
            'border-0 outline-none cursor-pointer',
            (streaming || uploading || (!input.trim() && pendingAttachmentCount === 0))
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
