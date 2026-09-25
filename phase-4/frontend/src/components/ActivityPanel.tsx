/**
 * ActivityPanel — the profile's inbox: what needs you, what is running, and
 * what is done. Items come from Vahana (GET /inbox), errands from Kriya
 * (GET /tasks) and in-progress paths (GET /workflow-runs). Opening the screen
 * marks its items read.
 *
 * Tapping an item deep-links: an approval opens its card ("/?approval=<id>"),
 * a task its live screen ("/?task=<id>"), a review its check screen, a path
 * opens Paths. A carer's shared copy only shows who shared it; it links
 * nowhere, because only the subject can act on it.
 */
import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import {
  ArrowRight,
  Bell,
  CalendarClock,
  ChevronRight,
  CircleCheck,
  Globe,
  Hand,
  HeartPulse,
  LoaderCircle,
  Mail,
  MessageCircleQuestion,
  Moon,
  Pill,
  Route,
  ShieldCheck,
  TriangleAlert,
  Users,
} from 'lucide-react'
import { apiFetch, apiUrl, type WorkflowRun } from '@/lib/api'
import { fetchInbox, groupInbox, markInboxRead, type InboxItem } from '@/lib/notifications'
import { fetchActiveTasks, openTaskScreen, type KriyaTask } from '@/lib/tasks'
import { PUSH_EVENT } from '@/lib/pwa'
import { relativeTime } from '@/lib/format-time'
import { textLang } from '@/lib/trust'
import { PhoneNotificationsCard } from './NotificationSettings'
import { Bindu, Fold, FocusCard, KolamFull, ScreenTitle } from './pulli'

interface Props {
  userId: string
  /** An item to highlight, from a notification tap ("/?activity=<id>"). */
  focusEventId?: string | null
  onOpenUrl: (url: string) => void
  onOpenRun: (runId: string) => void
  onUnreadChange?: (count: number) => void
}

type Target = { label: string; open: () => void } | null

const REFRESH_MS = 30_000
// While an errand runs, its state here keeps up with the card in the chat.
const TASK_REFRESH_MS = 8_000

// Inside a fold, things are quiet rows: no box, a highlight when pressed.
const cardStyle: CSSProperties = {
  border: 0,
  background: 'transparent',
  borderRadius: 14,
}

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

/** How an errand stands, in the words the task card uses. */
const TASK_STATES: Record<string, { label: string; tone: string; icon: typeof Globe }> = {
  queued: { label: 'Waiting to start', tone: 'var(--ink-55)', icon: Globe },
  running: { label: 'Working', tone: 'var(--tulsi)', icon: Globe },
  waiting_approval: { label: 'Waiting for your OK', tone: 'var(--sindoor)', icon: ShieldCheck },
  waiting_help: { label: 'Needs your help', tone: 'var(--sindoor)', icon: Hand },
}

function readableTime(ts: string): string {
  const date = new Date(ts)
  if (Number.isNaN(date.valueOf())) return ''
  const sameDay = date.toDateString() === new Date().toDateString()
  return new Intl.DateTimeFormat(undefined, sameDay
    ? { hour: 'numeric', minute: '2-digit' }
    : { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }).format(date)
}

function IconTile({ icon: Icon, tone }: { icon: typeof Bell; tone: string }) {
  return (
    <span aria-hidden="true" style={{ width: 36, height: 36, flex: '0 0 auto', display: 'grid', placeItems: 'center', borderRadius: 999, color: tone, background: 'var(--surface-raised)' }}>
      <Icon size={17} />
    </span>
  )
}

export function ActivityPanel({ userId, focusEventId, onOpenUrl, onOpenRun, onUnreadChange }: Props) {
  const [items, setItems] = useState<InboxItem[]>([])
  const [runs, setRuns] = useState<WorkflowRun[]>([])
  const [tasks, setTasks] = useState<KriyaTask[]>([])
  const [newIds, setNewIds] = useState<Set<string>>(new Set())
  const [expanded, setExpanded] = useState<string | null>(focusEventId ?? null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const focusRef = useRef<HTMLDivElement | null>(null)
  const unreadChanged = useRef(onUnreadChange)
  unreadChanged.current = onUnreadChange

  const loadTasks = useCallback(async () => {
    try {
      setTasks(await fetchActiveTasks())
    } catch {
      // A Mac without Kriya, or a moment offline: no errands to show.
    }
  }, [])

  const load = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true)
    try {
      const [inbox, runPayload] = await Promise.all([
        fetchInbox(100),
        apiFetch(apiUrl('/workflow-runs', { user_id: userId, limit: 50 }))
          .then(response => response.ok ? response.json() as Promise<{ runs: WorkflowRun[] }> : { runs: [] })
          .catch(() => ({ runs: [] as WorkflowRun[] })),
        loadTasks(),
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
  }, [userId, loadTasks])

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

  const hasTasks = tasks.length > 0
  useEffect(() => {
    if (!hasTasks) return
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') void loadTasks()
    }, TASK_REFRESH_MS)
    return () => window.clearInterval(timer)
  }, [hasTasks, loadTasks])

  useEffect(() => {
    if (!focusEventId) return
    setExpanded(focusEventId)
    const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    focusRef.current?.scrollIntoView({ block: 'center', behavior: reduced ? 'auto' : 'smooth' })
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
    const hindi = textLang(`${item.title} ${item.body}`) === 'hi'
    return (
      <div
        key={item.id}
        ref={focused ? focusRef : undefined}
        lang={hindi ? 'hi' : undefined}
        style={{
          ...cardStyle,
          padding: '12px 12px 10px',
          display: 'grid',
          gridTemplateColumns: '36px minmax(0,1fr)',
          gap: 12,
          background: focused ? 'var(--surface-raised)' : cardStyle.background,
        }}
      >
        <IconTile icon={Icon} tone={item.kind === 'health_alert' || item.kind === 'andon' ? 'var(--sindoor)' : 'var(--kajal)'} />
        <div style={{ minWidth: 0 }}>
          <button
            type="button"
            onClick={() => (target ? target.open() : setExpanded(open ? null : item.id))}
            aria-expanded={target ? undefined : open}
            style={{ display: 'block', width: '100%', padding: 0, border: 0, background: 'transparent', textAlign: 'left', cursor: 'pointer', minHeight: 44, color: 'inherit' }}
          >
            <span style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
              <span style={{ flex: 1, minWidth: 0, fontSize: 15, lineHeight: 1.35, fontWeight: 700, color: 'var(--kajal)', overflow: 'hidden', textOverflow: 'ellipsis', display: '-webkit-box', WebkitLineClamp: open ? undefined : 2, WebkitBoxOrient: 'vertical' }}>
                {fresh && <span role="img" aria-label="New" style={{ display: 'inline-block', width: 8, height: 8, marginRight: 7, borderRadius: 99, background: 'var(--sindoor)', verticalAlign: 'middle' }} />}
                {item.title || KIND_LABELS[item.kind] || 'Update'}
              </span>
              <span title={new Date(item.ts).toLocaleString()} style={{ flex: '0 0 auto', fontSize: 12.5, color: 'var(--ink-55)' }}>
                {readableTime(item.ts)}
              </span>
            </span>
            {item.body && (
              <span style={{
                display: open ? 'block' : '-webkit-box',
                marginTop: 4,
                fontSize: 14,
                lineHeight: 1.5,
                color: 'var(--ink-70)',
                whiteSpace: 'pre-wrap',
                overflow: 'hidden',
                WebkitLineClamp: open ? undefined : 2,
                WebkitBoxOrient: 'vertical',
              }}>
                {item.body}
              </span>
            )}
          </button>
          <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 6, marginTop: 6 }}>
            <span style={{ fontSize: 12.5, color: 'var(--ink-55)' }}>{relativeTime(item.ts)}</span>
            {KIND_LABELS[item.kind] && (
              <span style={{ padding: '2px 8px', borderRadius: 99, background: 'var(--ink-05)', fontSize: 12, color: 'var(--ink-70)' }}>{KIND_LABELS[item.kind]}</span>
            )}
            {item.shared_from && (
              <span style={{ padding: '2px 8px', borderRadius: 99, background: 'rgba(var(--rgb-mor),0.1)', fontSize: 12, fontWeight: 650, color: 'var(--mor)' }}>
                Shared by {item.shared_from_name || item.shared_from}{item.kind === 'approval_request' ? ' · only they can decide' : ''}
              </span>
            )}
            {target && (
              <button
                type="button"
                onClick={target.open}
                className={item.kind === 'approval_request' ? 'n-btn n-btn-go n-btn-sm' : 'n-btn n-btn-sm'}
                style={{ marginLeft: 'auto' }}
              >
                {target.label} <ArrowRight size={15} aria-hidden="true" />
              </button>
            )}
          </div>
        </div>
      </div>
    )
  }

  const renderTask = (task: KriyaTask) => {
    const state = TASK_STATES[task.status] ?? TASK_STATES.running
    const waiting = task.status === 'waiting_approval' || task.status === 'waiting_help'
    return (
      <button
        type="button"
        key={task.id}
        onClick={() => openTaskScreen(task.id)}
        aria-label={`${task.goal}. ${state.label}. Open the task`}
        className="pl-row"
        style={{ ...cardStyle, width: '100%', minHeight: 64, padding: '10px 12px', display: 'grid', gridTemplateColumns: '36px minmax(0,1fr) auto', alignItems: 'center', gap: 12, textAlign: 'left', cursor: 'pointer', color: 'inherit' }}
      >
        <IconTile icon={state.icon} tone={state.tone} />
        <span style={{ minWidth: 0 }}>
          <span style={{ display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden', fontSize: 15, lineHeight: 1.35, fontWeight: 700, color: 'var(--kajal)' }}>{task.goal}</span>
          <span style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 3, fontSize: 13.5, color: 'var(--ink-70)', minWidth: 0 }}>
            {task.status === 'running' && <LoaderCircle size={13} className="animate-spin" aria-hidden="true" style={{ flex: '0 0 auto', color: state.tone }} />}
            <span style={{ flex: '0 0 auto', fontWeight: 650, color: state.tone }}>{state.label}</span>
            {!waiting && task.detail && <span style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>· {task.detail}</span>}
          </span>
        </span>
        <ChevronRight size={18} aria-hidden="true" style={{ color: 'var(--ink-40)' }} />
      </button>
    )
  }

  const renderRun = (run: WorkflowRun, waiting: boolean) => (
    <button
      type="button"
      key={run.run_id}
      onClick={() => onOpenRun(run.run_id)}
      className="pl-row"
      style={{ ...cardStyle, width: '100%', minHeight: 64, padding: '10px 12px', display: 'grid', gridTemplateColumns: '36px minmax(0,1fr) auto', alignItems: 'center', gap: 12, textAlign: 'left', cursor: 'pointer', color: 'inherit' }}
    >
      <IconTile icon={Route} tone={run.definition?.accent || 'var(--kajal)'} />
      <span style={{ minWidth: 0 }}>
        <span style={{ display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden', fontSize: 15, lineHeight: 1.35, fontWeight: 700, color: 'var(--kajal)' }}>{run.title}</span>
        <span style={{ display: 'block', marginTop: 3, fontSize: 13.5, color: waiting ? 'var(--sindoor)' : 'var(--ink-70)', fontWeight: waiting ? 650 : 400 }}>
          {waiting ? 'Waiting for your OK' : `Path · ${run.current_stage?.title ?? 'In progress'} · ${run.progress_percent}%`}
        </span>
      </span>
      <ChevronRight size={18} aria-hidden="true" style={{ color: 'var(--ink-40)' }} />
    </button>
  )

  const runningCount = tasks.length + activeRuns.length
  const waitingCount = needsYou.length + waitingRuns.length
  // One thing in focus: the first that needs this person. The rest folds.
  const focusItem = needsYou[0] ?? null
  const focusRun = focusItem ? null : waitingRuns[0] ?? null
  const otherNeeds = needsYou.slice(focusItem ? 1 : 0)
  const otherRuns = waitingRuns.filter(run => run !== focusRun)
  const moreCount = otherNeeds.length + otherRuns.length
  const focusInDone = !!focusEventId && done.some(item => item.id === focusEventId)
  const nothingAtAll = waitingCount === 0 && runningCount === 0 && done.length === 0

  const renderFocus = () => {
    if (focusItem) {
      const target = targetFor(focusItem)
      const kicker = focusItem.kind === 'question' ? 'A question for you' : 'Needs your OK'
      return (
        <FocusCard
          kicker={`${kicker}${waitingCount > 1 ? ` · 1 of ${waitingCount}` : ''}`.toUpperCase()}
          title={focusItem.title || KIND_LABELS[focusItem.kind] || 'Needs you'}
          body={focusItem.body ? <span style={{ display: '-webkit-box', WebkitLineClamp: 3, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>{focusItem.body}</span> : undefined}
          live={Boolean(target)}
          action={target ? (
            <button type="button" className="n-btn n-btn-accent" style={{ flex: 1, minHeight: 52 }} onClick={target.open}>
              {target.label === 'Review' ? 'Review it' : target.label}
            </button>
          ) : undefined}
        >
          {focusItem.shared_from && (
            <p style={{ marginTop: 10, fontSize: 13.5, color: 'var(--ink-55)' }}>Shared by {focusItem.shared_from_name || focusItem.shared_from}. Only they can decide.</p>
          )}
        </FocusCard>
      )
    }
    if (focusRun) {
      return (
        <FocusCard
          kicker="PATH · WAITING FOR YOUR OK"
          colour={focusRun.definition?.accent || 'var(--sindoor)'}
          title={focusRun.title}
          body={focusRun.current_stage?.title}
          action={<button type="button" className="n-btn n-btn-accent" style={{ flex: 1, minHeight: 52 }} onClick={() => onOpenRun(focusRun.run_id)}>Open the path</button>}
        />
      )
    }
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: 14, padding: '14px 4px 6px' }}>
        <Bindu mood="calm" size={52} decorative />
        <p style={{ fontSize: 16, lineHeight: 1.45, color: 'var(--ink-70)' }}>Nothing needs you right now.</p>
      </div>
    )
  }

  return (
    <div className="panel-scroll" style={{ height: '100%', overflow: 'auto', background: 'var(--paper)' }}>
      <div style={{ maxWidth: 680, margin: '0 auto', padding: '4px 16px 36px' }}>
        <ScreenTitle title="Activity" status={waitingCount > 0 ? `${waitingCount} NEED YOU` : undefined} statusColour="var(--sindoor)" />

        {error && (
          <div role="alert" style={{ marginTop: 12, padding: '10px 14px', borderRadius: 14, background: 'var(--surface-raised)', color: 'var(--sindoor)', fontSize: 14 }}>{error}</div>
        )}

        {loading && items.length === 0 && runs.length === 0 && tasks.length === 0 ? (
          <div role="status" aria-label="Loading Activity" style={{ padding: 40, display: 'grid', placeItems: 'center' }}><Bindu mood="thinking" size={56} decorative /></div>
        ) : nothingAtAll ? (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 14, padding: '36px 12px 20px', textAlign: 'center' }}>
            <KolamFull size={220} />
            <h2 className="font-display" style={{ fontSize: 28 }}>All clear.</h2>
            <p style={{ fontSize: 16, lineHeight: 1.5, color: 'var(--ink-70)', maxWidth: 290 }}>Nothing needs you. When something does, a dot lights here first.</p>
          </div>
        ) : (
          <>
            <div className="pl-in" style={{ marginTop: 16 }}>{renderFocus()}</div>
            <div style={{ marginTop: 12, display: 'grid', gap: 2 }}>
              {moreCount > 0 && (
                <Fold summary={moreCount === 1 ? 'One more needs you' : `${moreCount} more need you`} count={moreCount}>
                  {otherNeeds.map(renderItem)}
                  {otherRuns.map(run => renderRun(run, true))}
                </Fold>
              )}
              {runningCount > 0 && (
                <Fold summary="Running" count={runningCount} defaultOpen={waitingCount === 0}>
                  {tasks.map(renderTask)}
                  {activeRuns.map(run => renderRun(run, false))}
                </Fold>
              )}
              {done.length > 0 && (
                <Fold summary="Done" count={done.length} defaultOpen={focusInDone}>
                  {done.map(renderItem)}
                </Fold>
              )}
            </div>
          </>
        )}

        <div style={{ marginTop: 2 }}>
          <Fold summary="Notifications on this phone">
            <div style={{ padding: '4px 4px 0' }}>
              <PhoneNotificationsCard userId={userId} compact />
            </div>
          </Fold>
        </div>
      </div>
    </div>
  )
}
