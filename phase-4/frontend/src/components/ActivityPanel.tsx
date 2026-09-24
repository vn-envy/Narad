/**
 * ActivityPanel — the profile's inbox: what needs you, what is running, and
 * what is done. Items come from Vahana (GET /inbox) and in-progress paths
 * (GET /workflow-runs). Opening the screen marks its items read.
 *
 * Tapping an item deep-links: an approval opens its card in Chat
 * ("/?approval=<id>"), a path opens Paths. A carer's shared copy only shows
 * who shared it; it links nowhere, because only the subject can act on it.
 */
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import {
  ArrowRight,
  Bell,
  CalendarClock,
  CircleCheck,
  HeartPulse,
  Inbox,
  LoaderCircle,
  Mail,
  MessageCircleQuestion,
  Moon,
  Pill,
  RefreshCw,
  ShieldCheck,
  TriangleAlert,
  Users,
} from 'lucide-react'
import { apiFetch, apiUrl, type WorkflowRun } from '@/lib/api'
import { fetchInbox, markInboxRead, type InboxItem } from '@/lib/notifications'
import { PUSH_EVENT } from '@/lib/pwa'
import { relativeTime } from '@/lib/format-time'
import { useIsMobile } from '@/hooks/useIsMobile'
import { PhoneNotificationsCard } from './NotificationSettings'

interface Props {
  userId: string
  /** An item to highlight, from a notification tap ("/?activity=<id>"). */
  focusEventId?: string | null
  onOpenUrl: (url: string) => void
  onOpenRun: (runId: string) => void
  onUnreadChange?: (count: number) => void
}

type Target = { label: string; open: () => void } | null

const NEEDS_YOU_WINDOW_MS = 24 * 60 * 60_000
const REFRESH_MS = 30_000

const cardStyle = {
  border: '1px solid rgba(45,42,38,0.1)',
  background: 'rgba(255,255,255,0.58)',
  borderRadius: 12,
} as const

const KIND_ICONS: Record<string, typeof Bell> = {
  approval_request: ShieldCheck,
  approval_result: ShieldCheck,
  question: MessageCircleQuestion,
  medicine_reminder: Pill,
  health_alert: HeartPulse,
  task_done: CircleCheck,
  reminder: CalendarClock,
  triage: Mail,
  andon: TriangleAlert,
  swapna: Moon,
}

const KIND_LABELS: Record<string, string> = {
  approval_request: 'Approval',
  approval_result: 'Approval',
  question: 'Question',
  medicine_reminder: 'Medicine',
  health_alert: 'Health',
  task_done: 'Done',
  reminder: 'Reminder',
  triage: 'Mail',
  andon: 'Needs attention',
  swapna: 'Digest',
}

function isRecent(item: InboxItem): boolean {
  const age = Date.now() - new Date(item.ts).getTime()
  const expires = Date.parse(String(item.data?.expires_at ?? ''))
  if (Number.isFinite(expires) && expires < Date.now()) return false
  return Number.isFinite(age) && age < NEEDS_YOU_WINDOW_MS
}

/** Needs you: open approval requests and questions from the last day. Everything else is done. */
export function groupInbox(items: InboxItem[]): { needsYou: InboxItem[]; done: InboxItem[] } {
  const decided = new Set(
    items
      .filter(item => item.kind === 'approval_result' && !item.shared_from && item.data?.proposal_id)
      .map(item => String(item.data?.proposal_id)),
  )
  const needsYou = items.filter(item => {
    if (item.shared_from || !isRecent(item)) return false
    if (item.kind === 'question') return true
    return item.kind === 'approval_request' && !decided.has(String(item.data?.proposal_id ?? ''))
  })
  const waiting = new Set(needsYou.map(item => item.id))
  return { needsYou, done: items.filter(item => !waiting.has(item.id)) }
}

function readableTime(ts: string): string {
  const date = new Date(ts)
  if (Number.isNaN(date.valueOf())) return ''
  const sameDay = date.toDateString() === new Date().toDateString()
  return new Intl.DateTimeFormat(undefined, sameDay
    ? { hour: 'numeric', minute: '2-digit' }
    : { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }).format(date)
}

function SectionLabel({ children, count }: { children: ReactNode; count?: number }) {
  return (
    <div style={{ display: 'flex', alignItems: 'baseline', gap: 6, margin: '18px 0 8px', fontFamily: 'var(--font-mono)', fontSize: 9.5, fontWeight: 750, textTransform: 'uppercase', letterSpacing: '0.12em', color: 'rgba(45,42,38,0.46)' }}>
      {children}{typeof count === 'number' && <span style={{ color: 'rgba(45,42,38,0.32)' }}>· {count}</span>}
    </div>
  )
}

function Empty({ children }: { children: ReactNode }) {
  return <div style={{ fontSize: 11.5, lineHeight: 1.5, color: 'rgba(45,42,38,0.46)', padding: '2px 2px 4px' }}>{children}</div>
}

export function ActivityPanel({ userId, focusEventId, onOpenUrl, onOpenRun, onUnreadChange }: Props) {
  const [items, setItems] = useState<InboxItem[]>([])
  const [runs, setRuns] = useState<WorkflowRun[]>([])
  const [newIds, setNewIds] = useState<Set<string>>(new Set())
  const [expanded, setExpanded] = useState<string | null>(focusEventId ?? null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const isMobile = useIsMobile()
  const focusRef = useRef<HTMLDivElement | null>(null)
  const unreadChanged = useRef(onUnreadChange)
  unreadChanged.current = onUnreadChange

  const load = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true)
    try {
      const [inbox, runPayload] = await Promise.all([
        fetchInbox(100),
        apiFetch(apiUrl('/workflow-runs', { user_id: userId, limit: 50 }))
          .then(response => response.ok ? response.json() as Promise<{ runs: WorkflowRun[] }> : { runs: [] })
          .catch(() => ({ runs: [] as WorkflowRun[] })),
      ])
      setItems(inbox.items)
      setRuns(runPayload.runs)
      setError(null)
      // Opening the screen reads what is on it; the "new" dots stay for this visit.
      const unread = inbox.items.filter(item => !item.read).map(item => item.id)
      if (unread.length > 0) {
        setNewIds(current => new Set([...current, ...unread]))
        await markInboxRead(unread)
      }
      unreadChanged.current?.(0)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Activity could not be loaded.')
    } finally {
      if (!quiet) setLoading(false)
    }
  }, [userId])

  useEffect(() => { void load() }, [load])

  useEffect(() => {
    const refresh = () => { void load(true) }
    const timer = window.setInterval(refresh, REFRESH_MS)
    window.addEventListener(PUSH_EVENT, refresh)
    window.addEventListener('focus', refresh)
    return () => {
      window.clearInterval(timer)
      window.removeEventListener(PUSH_EVENT, refresh)
      window.removeEventListener('focus', refresh)
    }
  }, [load])

  useEffect(() => {
    if (!focusEventId) return
    setExpanded(focusEventId)
    focusRef.current?.scrollIntoView({ block: 'center', behavior: 'smooth' })
  }, [focusEventId, items.length])

  const { needsYou, done } = useMemo(() => groupInbox(items), [items])
  const waitingRuns = runs.filter(run => run.status === 'waiting_confirmation' || run.state?.confirmation?.status === 'pending')
  const activeRuns = runs.filter(run => run.status === 'active' && !waitingRuns.includes(run))

  const targetFor = (item: InboxItem): Target => {
    if (item.shared_from) return null
    const url = typeof item.data?.url === 'string' ? item.data.url : ''
    if (url.startsWith('/') && !url.startsWith('//')) {
      return { label: item.kind === 'approval_request' ? 'Review' : 'Open', open: () => onOpenUrl(url) }
    }
    const runId = typeof item.data?.workflow_run_id === 'string' ? item.data.workflow_run_id : ''
    if (runId) return { label: 'Open path', open: () => onOpenRun(runId) }
    return null
  }

  const renderItem = (item: InboxItem) => {
    const Icon = item.shared_from ? Users : KIND_ICONS[item.kind] ?? Bell
    const target = targetFor(item)
    const open = expanded === item.id
    const fresh = newIds.has(item.id)
    const focused = item.id === focusEventId
    return (
      <div
        key={item.id}
        ref={focused ? focusRef : undefined}
        style={{
          ...cardStyle,
          padding: '11px 12px',
          display: 'grid',
          gridTemplateColumns: '32px minmax(0,1fr)',
          gap: 10,
          boxShadow: focused ? 'inset 3px 0 var(--sindoor)' : 'none',
          background: fresh ? 'rgba(252,211,77,0.10)' : cardStyle.background,
        }}
      >
        <span style={{ width: 32, height: 32, display: 'grid', placeItems: 'center', borderRadius: 9, color: item.kind === 'health_alert' || item.kind === 'andon' ? 'var(--sindoor)' : 'var(--kajal)', background: 'rgba(45,42,38,0.06)' }}>
          <Icon size={15} />
        </span>
        <div style={{ minWidth: 0 }}>
          <button
            type="button"
            onClick={() => (target ? target.open() : setExpanded(open ? null : item.id))}
            aria-expanded={target ? undefined : open}
            style={{ display: 'block', width: '100%', padding: 0, border: 0, background: 'transparent', textAlign: 'left', cursor: 'pointer', minHeight: 32 }}
          >
            <span style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
              <span style={{ flex: 1, minWidth: 0, fontSize: 12.5, fontWeight: 700, color: 'var(--kajal)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: open ? 'normal' : 'nowrap' }}>
                {fresh && <span aria-label="New" style={{ display: 'inline-block', width: 7, height: 7, marginRight: 6, borderRadius: 99, background: 'var(--sindoor)', verticalAlign: 'middle' }} />}
                {item.title || KIND_LABELS[item.kind] || 'Update'}
              </span>
              <span title={new Date(item.ts).toLocaleString()} style={{ flex: '0 0 auto', fontSize: 10, color: 'rgba(45,42,38,0.46)' }}>
                {readableTime(item.ts)}
              </span>
            </span>
            {item.body && (
              <span style={{
                display: open ? 'block' : '-webkit-box',
                marginTop: 3,
                fontSize: 11.5,
                lineHeight: 1.45,
                color: 'rgba(45,42,38,0.62)',
                whiteSpace: 'pre-wrap',
                overflow: 'hidden',
                WebkitLineClamp: open ? undefined : 2,
                WebkitBoxOrient: 'vertical',
              }}>
                {item.body}
              </span>
            )}
          </button>
          <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 6, marginTop: 7 }}>
            <span style={{ fontSize: 9.5, color: 'rgba(45,42,38,0.42)' }}>{relativeTime(item.ts)}</span>
            {KIND_LABELS[item.kind] && (
              <span style={{ padding: '1px 7px', borderRadius: 99, background: 'rgba(45,42,38,0.06)', fontSize: 9.5, color: 'rgba(45,42,38,0.58)' }}>{KIND_LABELS[item.kind]}</span>
            )}
            {item.shared_from && (
              <span style={{ padding: '1px 7px', borderRadius: 99, background: 'rgba(15,118,110,0.1)', fontSize: 9.5, fontWeight: 650, color: 'var(--mor)' }}>
                Shared by {item.shared_from_name || item.shared_from}{item.kind === 'approval_request' ? ' · only they can decide' : ''}
              </span>
            )}
            {target && (
              <button type="button" onClick={target.open} style={{ marginLeft: 'auto', minHeight: 32, padding: '0 11px', borderRadius: 8, border: 0, background: item.kind === 'approval_request' ? 'var(--tulsi)' : 'var(--kajal)', color: '#fff', fontSize: 11, fontWeight: 700, display: 'flex', alignItems: 'center', gap: 5, cursor: 'pointer' }}>
                {target.label} <ArrowRight size={12} />
              </button>
            )}
          </div>
        </div>
      </div>
    )
  }

  const renderRun = (run: WorkflowRun, waiting: boolean) => (
    <button
      type="button"
      key={run.run_id}
      onClick={() => onOpenRun(run.run_id)}
      style={{ ...cardStyle, width: '100%', padding: '11px 12px', display: 'grid', gridTemplateColumns: 'minmax(0,1fr) auto', alignItems: 'center', gap: 10, textAlign: 'left', cursor: 'pointer', borderLeft: `3px solid ${run.definition?.accent || '#b45309'}` }}
    >
      <span style={{ minWidth: 0 }}>
        <span style={{ display: 'block', fontSize: 12.5, fontWeight: 700, color: 'var(--kajal)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{run.title}</span>
        <span style={{ display: 'block', marginTop: 2, fontSize: 10.5, color: 'rgba(45,42,38,0.52)' }}>
          {waiting ? 'Waiting for your approval' : `${run.current_stage?.title ?? 'In progress'} · ${run.progress_percent}%`}
        </span>
      </span>
      <ArrowRight size={14} style={{ color: 'rgba(45,42,38,0.45)' }} />
    </button>
  )

  return (
    <div className="panel-scroll" style={{ height: '100%', overflow: 'auto', background: 'linear-gradient(135deg, rgba(180,83,9,0.035), transparent 42%), var(--paper)' }}>
      <div style={{ maxWidth: 680, margin: '0 auto', padding: isMobile ? '12px 12px 32px' : '18px 20px 36px' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
          <div style={{ fontSize: 11, color: 'rgba(45,42,38,0.52)' }}>Reminders, approvals and finished work, newest first.</div>
          <button type="button" onClick={() => void load()} title="Refresh" aria-label="Refresh activity" style={{ width: 34, height: 34, flex: '0 0 auto', borderRadius: 8, border: '1px solid rgba(45,42,38,0.1)', background: 'transparent', display: 'grid', placeItems: 'center', cursor: 'pointer' }}>
            <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>

        <div style={{ marginTop: 12 }}>
          <PhoneNotificationsCard userId={userId} compact />
        </div>

        {error && (
          <div role="alert" style={{ ...cardStyle, marginTop: 12, padding: '9px 11px', borderColor: 'rgba(194,65,12,0.22)', color: 'var(--sindoor)', fontSize: 11.5 }}>{error}</div>
        )}

        {loading && items.length === 0 && runs.length === 0 ? (
          <div style={{ padding: 40, display: 'grid', placeItems: 'center', color: 'rgba(45,42,38,0.45)' }}><LoaderCircle size={22} className="animate-spin" /></div>
        ) : (
          <>
            <SectionLabel count={needsYou.length + waitingRuns.length}>Needs you</SectionLabel>
            <div style={{ display: 'grid', gap: 8 }}>
              {needsYou.length + waitingRuns.length === 0 && <Empty>Nothing is waiting on you.</Empty>}
              {needsYou.map(renderItem)}
              {waitingRuns.map(run => renderRun(run, true))}
            </div>

            <SectionLabel count={activeRuns.length}>Running</SectionLabel>
            <div style={{ display: 'grid', gap: 8 }}>
              {activeRuns.length === 0 && <Empty>No paths in progress.</Empty>}
              {activeRuns.map(run => renderRun(run, false))}
            </div>

            <SectionLabel count={done.length}>Done</SectionLabel>
            <div style={{ display: 'grid', gap: 8 }}>
              {done.length === 0 && (
                <div style={{ ...cardStyle, padding: '18px 14px', display: 'flex', gap: 10, alignItems: 'center', color: 'rgba(45,42,38,0.5)', fontSize: 11.5, lineHeight: 1.5 }}>
                  <Inbox size={18} style={{ flex: '0 0 auto' }} />
                  Nothing yet. Reminders, results and finished work will show up here.
                </div>
              )}
              {done.map(renderItem)}
            </div>
          </>
        )}
      </div>
    </div>
  )
}
