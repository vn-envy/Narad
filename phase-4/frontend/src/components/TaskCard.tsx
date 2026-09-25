/**
 * Kriya task card and task screen: an errand running on the Mac, watched and
 * stoppable from the phone.
 *
 * TaskCard sits in the chat (the `task_started` stream event): the goal, the
 * current step in plain words, a small live frame, the approval card when a
 * step waits for an OK, and Stop. TaskScreen is the full view: a 1-2 fps live
 * frame, the step list, a big Stop button and, while the task waits for help
 * (a sign-in or a captcha), takeover: tap the frame to click there, type into
 * the page, scroll, go back, then Continue. Frames are fetched with the
 * session, shown from memory and never cached. TaskSheet opens the screen for
 * /?task=<id> links from notifications and Activity.
 *
 * Phone tasks (Artemis on an Android phone) show the phone's latest screen in
 * a portrait frame and have no takeover: the phone is used directly. When one
 * needs a banking or UPI app, the card asks to allow that app for this task
 * before its approval can do anything. Desktop tasks show the Mac's window.
 */
import { useCallback, useEffect, useRef, useState, type CSSProperties, type MouseEvent } from 'react'
import {
  ArrowDown,
  ArrowLeft,
  ArrowUp,
  Check,
  CircleCheck,
  CornerDownLeft,
  Hand,
  Loader,
  Maximize2,
  Minimize2,
  Play,
  RotateCcw,
  Send,
  ShieldAlert,
  ShieldCheck,
  Square,
  X,
  ZoomIn,
} from 'lucide-react'
import {
  TASK_ID_RE,
  TASK_STATUS_LABELS,
  allowTaskApp,
  blockedApps,
  canTakeOver,
  continueTask,
  fetchTask,
  fetchTaskFrame,
  isTaskActive,
  isTaskLive,
  openTaskScreen,
  stopTask,
  takeoverTask,
  type KriyaTask,
  type TakeoverInput,
  type TaskEvent,
} from '@/lib/tasks'
import { ApprovalCard } from './ApprovalCard'
import { AvatarTag, Beam, DotText, DotsRow, Fold } from './pulli'

const CARD_POLL_MS = 2_000
const SCREEN_POLL_MS = 1_000
const FRAME_MS = 600 // about 1.5 frames a second
const CARD_FRAME_MS = 2_500
const FRAME_ASPECT = '1280 / 800'
const PHONE_ASPECT = '9 / 19.5'

function frameAspect(task: KriyaTask): string {
  return task.surface === 'phone' ? PHONE_ASPECT : FRAME_ASPECT
}

function frameLabel(task: KriyaTask): string {
  if (task.surface === 'phone') return `The screen of ${task.device || 'the phone'}`
  if (task.surface === 'desktop') return "The Mac's window the task is working in"
  return 'The page the task is working on'
}

function accentFor(task: KriyaTask): string {
  if (task.status === 'waiting_approval' || task.status === 'waiting_help') return 'var(--sindoor)'
  if (task.status === 'done' || task.status === 'running' || task.status === 'queued') return 'var(--tulsi)'
  if (task.status === 'failed') return 'var(--kesari)'
  return 'var(--loha)'
}

function hostOf(url?: string): string {
  if (!url) return ''
  try {
    return new URL(url).hostname.replace(/^www\./, '')
  } catch {
    return ''
  }
}

/** Poll a task while it is active; returns the freshest copy. */
function useTask(initial: KriyaTask, intervalMs: number): [KriyaTask, (next: KriyaTask) => void] {
  const [task, setTask] = useState(initial)
  const active = isTaskActive(task)
  useEffect(() => {
    setTask(current => (current.id === initial.id && current.updated_at && current.updated_at >= (initial.updated_at ?? '') ? current : initial))
  }, [initial])
  useEffect(() => {
    if (!active) return
    let cancelled = false
    let timer = 0
    const controller = new AbortController()
    const tick = async () => {
      if (cancelled) return
      if (document.visibilityState === 'visible') {
        try {
          const next = await fetchTask(task.id, controller.signal)
          if (!cancelled) setTask(next)
          if (!isTaskActive(next)) return
        } catch {
          // offline for a moment: keep polling
        }
      }
      timer = window.setTimeout(tick, intervalMs)
    }
    void tick()
    return () => {
      cancelled = true
      controller.abort()
      window.clearTimeout(timer)
    }
  }, [task.id, active, intervalMs])
  return [task, setTask]
}

/** The live frame as an object URL, refreshed every `everyMs` while `on`. */
function useLiveFrame(taskId: string, on: boolean, everyMs: number): string | null {
  const [url, setUrl] = useState<string | null>(null)
  useEffect(() => {
    if (!on) return
    let cancelled = false
    let timer = 0
    let current: string | null = null
    const controller = new AbortController()
    const tick = async () => {
      if (cancelled) return
      if (document.visibilityState === 'visible') {
        try {
          const blob = await fetchTaskFrame(taskId, controller.signal)
          if (cancelled) return
          if (blob) {
            const next = URL.createObjectURL(blob)
            setUrl(next)
            if (current) URL.revokeObjectURL(current)
            current = next
          }
        } catch {
          // a dropped frame: the next one will do
        }
      }
      timer = window.setTimeout(tick, everyMs)
    }
    void tick()
    return () => {
      cancelled = true
      controller.abort()
      window.clearTimeout(timer)
      if (current) URL.revokeObjectURL(current)
      setUrl(null)
    }
  }, [taskId, on, everyMs])
  return url
}

/** Who is on it and where (Matsya runs every errand), and how it stands, in dots. */
function StatusChip({ task }: { task: KriyaTask }) {
  const where = task.surface === 'phone' ? 'phone' : task.surface === 'desktop' ? 'on the Mac' : 'errand'
  return (
    <span className="inline-flex items-center gap-3 min-w-0">
      <AvatarTag name="Matsya" detail={where} live={task.status === 'running'} />
      <span className="n-status" style={{ color: accentFor(task) }}>
        {TASK_STATUS_LABELS[task.status] ?? task.status}
      </span>
    </span>
  )
}

/** The errand's steps as dots, with the count the machine keeps. */
function StepDots({ task }: { task: KriyaTask }) {
  if (!isTaskActive(task) || !task.max_steps) return null
  const step = Math.max(0, task.step)
  return (
    <div className="flex items-center gap-3 min-w-0">
      <div className="min-w-0 overflow-hidden flex-1">
        <DotsRow total={task.max_steps} done={Math.max(1, step)} colour="var(--avatar-matsya)" gap={9.5} r={2.8} live={task.status === 'running'} label={`Step ${step} of ${task.max_steps}`} />
      </div>
      <DotText size={13.5}>{`STEP ${String(step).padStart(2, '0')}/${task.max_steps}`}</DotText>
    </div>
  )
}

function Detail({ task }: { task: KriyaTask }) {
  const working = task.status === 'running' || task.status === 'queued'
  const text = isTaskActive(task) ? task.detail : task.result?.summary || task.detail
  if (!text) return null
  return (
    <p className="flex items-start gap-2 text-[14.5px] leading-snug break-words" style={{ color: 'var(--ink-70)' }} aria-live="polite">
      {working && <Loader size={15} className="animate-spin shrink-0 mt-[2px]" aria-hidden="true" />}
      <span className="min-w-0">{text}</span>
    </p>
  )
}

function Result({ task }: { task: KriyaTask }) {
  if (isTaskActive(task) || !task.result?.answer) return null
  return (
    <div className="rounded-lg px-3 py-2.5 text-[14.5px] leading-snug break-words" style={{ background: 'var(--surface-2)', border: 'var(--folk-border)', color: 'var(--kajal)' }}>
      {task.result.answer}
    </div>
  )
}

function FrameBox({
  src,
  interactive,
  onTap,
  label,
  zoomable,
  aspect = FRAME_ASPECT,
}: {
  src: string | null
  interactive?: boolean
  onTap?: (x: number, y: number) => void
  label: string
  zoomable?: boolean
  aspect?: string
}) {
  const [dot, setDot] = useState<{ x: number; y: number } | null>(null)
  // The page is a desktop-width view: zoomed, it is 2.5x wide and scrolls, so
  // a field is big enough to tap on a phone.
  const [zoomed, setZoomed] = useState(false)
  const tap = (event: MouseEvent<HTMLDivElement>) => {
    if (!interactive || !onTap) return
    const rect = event.currentTarget.getBoundingClientRect()
    const x = (event.clientX - rect.left) / rect.width
    const y = (event.clientY - rect.top) / rect.height
    if (x < 0 || x > 1 || y < 0 || y > 1) return
    setDot({ x, y })
    window.setTimeout(() => setDot(null), 700)
    onTap(x, y)
  }
  const outer: CSSProperties = {
    position: 'relative',
    background: 'var(--surface-2)',
    border: interactive ? '2px solid var(--sindoor)' : 'var(--folk-border)',
    ...(zoomed ? { height: 'min(62vh, 520px)', overflow: 'auto' } : { aspectRatio: aspect, overflow: 'hidden' }),
  }
  const inner: CSSProperties = {
    position: 'relative',
    width: zoomed ? '250%' : '100%',
    aspectRatio: aspect,
    cursor: interactive ? 'crosshair' : 'default',
    touchAction: zoomed ? 'pan-x pan-y' : 'manipulation',
  }
  // A phone's portrait screen stays phone-sized instead of filling the width.
  const portrait = aspect !== FRAME_ASPECT
  return (
    <div className="relative w-full" style={{ flexShrink: 0, ...(portrait ? { maxWidth: 280, margin: '0 auto' } : {}) }}>
      <div className="w-full rounded" style={outer}>
        <div style={inner} onClick={tap} role={interactive ? 'button' : undefined} aria-label={label}>
          {src ? (
            <img src={src} alt={label} className="w-full h-full object-contain select-none" draggable={false} />
          ) : (
            <div role="status" className="w-full h-full flex items-center justify-center gap-2 text-[13px]" style={{ color: 'var(--ink-55)' }}>
              <Loader size={15} className="animate-spin" aria-hidden="true" /> Connecting to the page…
            </div>
          )}
          {dot && (
            <span
              aria-hidden
              style={{
                position: 'absolute', left: `${dot.x * 100}%`, top: `${dot.y * 100}%`, width: 28, height: 28,
                marginLeft: -14, marginTop: -14, borderRadius: 999, border: '2px solid var(--sindoor)',
                background: 'rgba(var(--rgb-sindoor), 0.18)', pointerEvents: 'none',
              }}
            />
          )}
        </div>
      </div>
      {zoomable && src && (
        <button
          type="button"
          onClick={() => setZoomed(value => !value)}
          aria-label={zoomed ? 'Fit the page' : 'Zoom in on the page'}
          className="inline-flex items-center justify-center rounded-full"
          style={{
            position: 'absolute', right: 8, bottom: 8, width: 48, height: 48, border: 0,
            background: 'rgba(var(--rgb-kajal), 0.72)', color: '#fcfaf2',
          }}
        >
          {zoomed ? <Minimize2 size={17} /> : <ZoomIn size={17} />}
        </button>
      )}
    </div>
  )
}

function StopButton({ task, onChange, large }: { task: KriyaTask; onChange: (next: KriyaTask) => void; large?: boolean }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  if (!isTaskActive(task)) return null
  const stop = async () => {
    setBusy(true)
    setError(null)
    try {
      onChange(await stopTask(task.id))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not reach Narad.')
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className={large ? 'w-full' : 'flex-none'}>
      {/* On the card Stop is outlined, so Watch is the one filled button; on
          the task screen it is the screen's main control. */}
      <button
        type="button"
        onClick={() => void stop()}
        disabled={busy || task.cancel_requested}
        className={large ? 'n-btn n-btn-block' : 'n-link'}
        style={large ? { minHeight: 54, fontSize: 16 } : undefined}
      >
        {busy || task.cancel_requested ? <Loader size={17} className="animate-spin" aria-hidden="true" /> : <Square size={15} fill="currentColor" aria-hidden="true" />}
        {task.cancel_requested ? 'Stopping…' : 'Stop'}
      </button>
      {error && <p className="mt-1.5 text-[13.5px]" role="alert" style={{ color: 'var(--kesari)' }}>{error}</p>}
    </div>
  )
}

/** A phone task that needs a banking or UPI app: allow it for this task, then approve. */
function AppAllowPanel({ task, onChange }: { task: KriyaTask; onChange: (next: KriyaTask) => void }) {
  const apps = blockedApps(task)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  if (task.status !== 'waiting_approval' || !apps.length) return null
  const allow = async (pkg: string) => {
    setBusy(pkg)
    setError(null)
    try {
      onChange(await allowTaskApp(task.id, pkg))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not reach Narad.')
    } finally {
      setBusy(null)
    }
  }
  return (
    <div className="flex flex-col gap-2 rounded px-3 py-3" style={{ background: 'rgba(var(--rgb-sindoor), 0.06)', border: '1px solid rgba(var(--rgb-sindoor), 0.22)' }}>
      <p className="flex items-start gap-2 text-[13px] leading-snug" style={{ color: 'var(--kajal)' }}>
        <ShieldAlert size={15} className="shrink-0 mt-px" style={{ color: 'var(--kesari)' }} />
        <span>
          {apps.map(app => app.name).join(', ')} {apps.length > 1 ? 'are banking or UPI apps' : 'is a banking or UPI app'}. Narad
          will not open {apps.length > 1 ? 'them' : 'it'} for this task unless you allow {apps.length > 1 ? 'them' : 'it'} here first,
          then approve below.
        </span>
      </p>
      {apps.map(app => (
        <button
          key={app.package}
          type="button"
          onClick={() => void allow(app.package)}
          disabled={busy !== null}
          className="w-full rounded-[10px] text-[13px] font-semibold inline-flex items-center justify-center gap-2"
          style={{ minHeight: 48, border: '1px solid var(--ink-12)', background: 'var(--surface)', color: 'var(--kajal)' }}
        >
          {busy === app.package ? <Loader size={15} className="animate-spin" /> : <ShieldCheck size={15} />}
          Allow {app.name} for this task
        </button>
      ))}
      {error && <p className="text-[12px]" role="alert" style={{ color: 'var(--kesari)' }}>{error}</p>}
    </div>
  )
}

/** In the chat: one running errand. */
export function TaskCard({ task: incoming }: { task: KriyaTask }) {
  const [task, setTask] = useTask(incoming, CARD_POLL_MS)
  const showFrame = task.status === 'running' || task.status === 'waiting_help'
  const frame = useLiveFrame(task.id, showFrame, CARD_FRAME_MS)
  const active = isTaskActive(task)
  return (
    <section
      className="n-card flex flex-col gap-2.5"
      style={{ ['--card-accent' as string]: accentFor(task) }}
      aria-label={`Task: ${task.goal}`}
    >
      {active && <Beam colour={task.status === 'running' ? 'var(--avatar-matsya)' : 'var(--sindoor)'} live={task.status !== 'queued'} />}
      <div className="n-card-head">
        <StatusChip task={task} />
      </div>
      <p className="n-card-title" style={{ marginTop: 0 }}>{task.goal}</p>
      <StepDots task={task} />
      <Detail task={task} />
      {showFrame && (
        <button type="button" onClick={() => openTaskScreen(task.id)} className="block w-full text-left" aria-label="Watch it live">
          <FrameBox src={frame} label={frameLabel(task)} aspect={frameAspect(task)} />
        </button>
      )}
      <AppAllowPanel task={task} onChange={setTask} />
      {task.status === 'waiting_approval' && task.approval && <ApprovalCard key={task.approval.id} proposal={task.approval} />}
      <Result task={task} />
      <div className="n-card-actions items-center" style={{ marginTop: 2 }}>
        <button
          type="button"
          onClick={() => openTaskScreen(task.id)}
          className={task.status === 'waiting_help' ? 'n-btn n-btn-accent flex-1' : active ? 'n-btn n-btn-primary flex-1' : 'n-btn flex-1'}
        >
          {task.status === 'waiting_help' ? <Hand size={16} aria-hidden="true" /> : <Maximize2 size={15} aria-hidden="true" />}
          {task.status === 'waiting_help' ? 'Open live view' : active ? 'Watch live' : 'Steps'}
        </button>
        <StopButton task={task} onChange={setTask} />
      </div>
    </section>
  )
}

const EVENT_ICONS: Record<string, typeof Check> = {
  step: CircleCheck,
  retry: RotateCcw,
  approval_requested: ShieldCheck,
  approved: ShieldCheck,
  help_needed: Hand,
  takeover: Hand,
  resumed: Play,
  done: Check,
  failed: X,
  cancelled: Square,
}

function StepList({ events }: { events: TaskEvent[] }) {
  const shown = events.filter(event => event.kind !== 'created').slice(-40)
  if (!shown.length) return null
  return (
    <ol className="flex flex-col gap-1.5" aria-label="Steps">
      {shown.map(event => {
        const Icon = EVENT_ICONS[event.kind] ?? CircleCheck
        const failed = event.kind === 'failed' || /→ (did not work|not done|refused)/.test(event.summary)
        return (
          <li key={event.id} className="flex items-start gap-2.5 text-[14px] leading-snug">
            <Icon size={15} aria-hidden="true" className="shrink-0 mt-[2px]" style={{ color: failed ? 'var(--kesari)' : 'var(--ink-55)' }} />
            <span className="min-w-0 break-words" style={{ color: 'var(--ink-70)' }}>
              {event.step ? <span className="font-mono text-[11.5px] mr-1.5" style={{ color: 'var(--ink-55)' }}>{event.step}</span> : null}
              {event.summary}
            </span>
          </li>
        )
      })}
    </ol>
  )
}

function HelpPanel({ task, onChange }: { task: KriyaTask; onChange: (next: KriyaTask) => void }) {
  const [text, setText] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const expired = task.help?.kind === 'approval_expired'
  // Only a page can be helped from here; a phone or the Mac is used directly.
  const typing = !expired && canTakeOver(task)

  const send = async (input: TakeoverInput, label: string) => {
    setBusy(label)
    setError(null)
    try {
      await takeoverTask(task.id, input)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'The page did not respond.')
    } finally {
      setBusy(null)
    }
  }
  const proceed = async () => {
    setBusy('continue')
    setError(null)
    try {
      onChange(await continueTask(task.id))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not reach Narad.')
    } finally {
      setBusy(null)
    }
  }
  const small = 'n-btn n-btn-sm flex-1 px-2'
  const smallStyle: CSSProperties = {}
  return (
    <div className="flex flex-col gap-2.5 rounded px-3 py-3" style={{ background: 'rgba(var(--rgb-sindoor), 0.06)', border: '1px solid rgba(var(--rgb-sindoor), 0.22)' }}>
      <p className="text-[15px] leading-snug font-semibold" style={{ color: 'var(--kajal)' }}>{task.help?.reason || task.detail}</p>
      {typing && (
        <>
          <p className="text-[14px] leading-snug" style={{ color: 'var(--ink-70)' }}>
            Tap a field on the page above, then type here. Narad never saves what you type.
          </p>
          <form
            className="flex gap-2"
            onSubmit={event => {
              event.preventDefault()
              if (!text) return
              const value = text
              setText('')
              void send({ kind: 'type', text: value }, 'type')
            }}
          >
            <input
              value={text}
              onChange={event => setText(event.target.value)}
              autoComplete="off"
              autoCapitalize="none"
              autoCorrect="off"
              spellCheck={false}
              placeholder="Type into the page"
              aria-label="Type into the page"
              className="n-field flex-1 min-w-0 outline-none"
            />
            <button type="submit" disabled={!text || busy !== null} className="n-btn n-btn-primary" style={{ minWidth: 48, padding: 0 }} aria-label="Send the text">
              {busy === 'type' ? <Loader size={15} className="animate-spin" /> : <Send size={15} />}
            </button>
          </form>
          <div className="flex gap-2">
            <button type="button" className={small} style={smallStyle} disabled={busy !== null} onClick={() => void send({ kind: 'key', key: 'Enter' }, 'enter')}>
              <CornerDownLeft size={14} /> Enter
            </button>
            <button type="button" className={small} style={smallStyle} disabled={busy !== null} onClick={() => void send({ kind: 'scroll', direction: 'up' }, 'up')} aria-label="Scroll up">
              <ArrowUp size={14} />
            </button>
            <button type="button" className={small} style={smallStyle} disabled={busy !== null} onClick={() => void send({ kind: 'scroll', direction: 'down' }, 'down')} aria-label="Scroll down">
              <ArrowDown size={14} />
            </button>
            <button type="button" className={small} style={smallStyle} disabled={busy !== null} onClick={() => void send({ kind: 'back' }, 'back')}>
              <ArrowLeft size={14} /> Back
            </button>
          </div>
        </>
      )}
      <button
        type="button"
        onClick={() => void proceed()}
        disabled={busy !== null}
        className="n-btn n-btn-go n-btn-block"
        style={{ minHeight: 52, fontSize: 15 }}
      >
        {busy === 'continue' ? <Loader size={16} className="animate-spin" /> : <Play size={15} fill="currentColor" />}
        {expired ? 'Ask me again' : "I'm done, continue"}
      </button>
      {error && <p className="text-[13.5px]" role="alert" style={{ color: 'var(--kesari)' }}>{error}</p>}
    </div>
  )
}

/** The full task view: live frame, takeover while it waits for help, steps, Stop. */
export function TaskScreen({ task: incoming, onClose }: { task: KriyaTask; onClose: () => void }) {
  const [task, setTask] = useTask(incoming, SCREEN_POLL_MS)
  const live = isTaskLive(task)
  const frame = useLiveFrame(task.id, live, FRAME_MS)
  const helping = task.status === 'waiting_help'
  const [tapError, setTapError] = useState<string | null>(null)
  const tap = useCallback(
    (x: number, y: number) => {
      setTapError(null)
      takeoverTask(task.id, { kind: 'click', x, y }).catch(err =>
        setTapError(err instanceof Error ? err.message : 'The page did not respond.'),
      )
    },
    [task.id],
  )
  const host = task.device || (canTakeOver(task) ? hostOf(task.last_url || task.help?.url) : '')
  return (
    <div role="dialog" aria-modal="true" aria-label="Task" style={{ position: 'fixed', inset: 0, zIndex: 60, background: 'var(--paper)', display: 'flex', flexDirection: 'column' }}>
      <div className="chrome-frost flex items-center gap-2 px-3" style={{ minHeight: 60, paddingTop: 'env(safe-area-inset-top)' }}>
        <button type="button" onClick={onClose} aria-label="Close" className="inline-flex items-center justify-center rounded-full shrink-0" style={{ width: 48, height: 48, color: 'var(--ink-70)' }}>
          <X size={20} />
        </button>
        <StatusChip task={task} />
        {host && <span className="ml-auto text-[12.5px] truncate pr-1" style={{ color: 'var(--ink-55)', maxWidth: '40%' }}>{host}</span>}
      </div>
      <div className="flex-1 overflow-y-auto px-4 py-4 flex flex-col gap-3" style={{ maxWidth: 720, width: '100%', margin: '0 auto' }}>
        <h1 className="font-display break-words" style={{ fontSize: 26, color: 'var(--kajal)' }}>{task.goal}</h1>
        <StepDots task={task} />
        {!helping && <Detail task={task} />}
        {live && (
          <FrameBox
            src={frame}
            interactive={helping && canTakeOver(task)}
            onTap={tap}
            zoomable={task.surface !== 'phone'}
            aspect={frameAspect(task)}
            label={helping && canTakeOver(task) ? 'The page: tap where you want to click' : frameLabel(task)}
          />
        )}
        {tapError && <p className="text-[13.5px]" role="alert" style={{ color: 'var(--kesari)' }}>{tapError}</p>}
        {helping && <HelpPanel task={task} onChange={setTask} />}
        <AppAllowPanel task={task} onChange={setTask} />
        {task.status === 'waiting_approval' && task.approval && <ApprovalCard key={task.approval.id} proposal={task.approval} />}
        <Result task={task} />
        {task.events && task.events.length > 0 && (
          <Fold summary="Steps so far" count={task.events.filter(event => event.kind !== 'created').length}>
            <div className="px-3 pt-1"><StepList events={task.events} /></div>
          </Fold>
        )}
      </div>
      {isTaskActive(task) && (
        <div className="px-4 pt-2" style={{ paddingBottom: 'calc(12px + env(safe-area-inset-bottom))', maxWidth: 720, width: '100%', margin: '0 auto' }}>
          <StopButton task={task} onChange={setTask} large />
        </div>
      )}
    </div>
  )
}

/**
 * Opens the task screen over whatever is showing: for /?task=<id> (a
 * notification or an Activity item), a cancellable `narad:deeplink` event
 * whose link carries `?task=`, or a `narad:open-task` window event.
 */
export function TaskSheet() {
  const [openId, setOpenId] = useState<string | null>(null)
  const [task, setTask] = useState<KriyaTask | null>(null)
  const [error, setError] = useState<string | null>(null)
  const openRef = useRef(setOpenId)
  openRef.current = setOpenId

  useEffect(() => {
    const show = (id: string | null | undefined) => {
      if (!id || !TASK_ID_RE.test(id)) return
      openRef.current(id)
    }
    const params = new URLSearchParams(window.location.search)
    const fromUrl = params.get('task')
    if (fromUrl) {
      params.delete('task')
      const query = params.toString()
      window.history.replaceState(window.history.state, '', `${window.location.pathname}${query ? `?${query}` : ''}${window.location.hash}`)
      show(fromUrl)
    }
    const onWindow = (event: Event) => show((event as CustomEvent<{ id?: string }>).detail?.id)
    const onDeepLink = (event: Event) => {
      const detail = (event as CustomEvent<Record<string, unknown>>).detail ?? {}
      let id: string | null = null
      try {
        id = new URL(String(detail.url ?? detail.href ?? detail.path ?? ''), window.location.origin).searchParams.get('task')
      } catch {
        id = null
      }
      if (id && TASK_ID_RE.test(id)) {
        event.preventDefault()
        show(id)
      }
    }
    window.addEventListener('narad:open-task', onWindow)
    window.addEventListener('narad:deeplink', onDeepLink)
    return () => {
      window.removeEventListener('narad:open-task', onWindow)
      window.removeEventListener('narad:deeplink', onDeepLink)
    }
  }, [])

  useEffect(() => {
    if (!openId) return
    setTask(null)
    setError(null)
    const controller = new AbortController()
    fetchTask(openId, controller.signal)
      .then(setTask)
      .catch(err => { if (!controller.signal.aborted) setError(err instanceof Error ? err.message : 'Could not load it.') })
    return () => controller.abort()
  }, [openId])

  if (!openId) return null
  const close = () => setOpenId(null)
  if (!task) {
    return (
      <div role="dialog" aria-modal="true" aria-label="Task" onClick={close} style={{ position: 'fixed', inset: 0, zIndex: 60, background: 'var(--paper)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <p role={error ? 'alert' : 'status'} className="text-[14.5px] flex items-center gap-2 px-6 text-center" style={{ color: error ? 'var(--kesari)' : 'var(--ink-55)' }}>
          {error ? error : <><Loader size={16} className="animate-spin" aria-hidden="true" /> Loading the task…</>}
        </p>
      </div>
    )
  }
  return <TaskScreen key={task.id} task={task} onClose={close} />
}
