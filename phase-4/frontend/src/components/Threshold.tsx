/**
 * Threshold — the chat before anything is said, drawn like the kolam at a
 * doorway at dawn: the date in dots, a greeting, the day's kolam, and one
 * thing in focus. If something needs this person (an approval, a question,
 * an errand waiting on them), that is the card, with its one button. If
 * nothing does, Bindu rests and the composer below is the one action.
 * Everything else folds: other waiting things, running errands, and ideas
 * for a first ask.
 */
import { useEffect, useState } from 'react'
import { ChevronRight, Pill, ShieldCheck, MessageCircleQuestion, Bell } from 'lucide-react'
import type { AvatarName } from '../hooks/useAvatara'
import type { FamilyProfile } from '@/lib/api'
import { fetchInbox, groupInbox, type InboxItem } from '@/lib/notifications'
import { fetchActiveTasks, openTaskScreen, type KriyaTask } from '@/lib/tasks'
import { OPEN_URL_EVENT, PUSH_EVENT } from '@/lib/pwa'
import { Bindu, DotText, Fold, FocusCard, KolamDay, KolamGlyph } from './pulli'

const STARTERS: Array<{ name: AvatarName; role: string; prompt: string }> = [
  { name: 'Matsya', role: 'Research a topic', prompt: 'Research the latest on ' },
  { name: 'Rama', role: 'Plan my week', prompt: 'Plan my week from my calendar and open tasks.' },
  { name: 'Krishna', role: 'Teach me something', prompt: '/teach me ' },
  { name: 'Parashurama', role: 'Automate something', prompt: 'Write a script that ' },
]

const KIND_KICKER: Record<string, string> = {
  approval_request: 'Needs your OK',
  question: 'A question for you',
}

function openUrl(url: string) {
  window.dispatchEvent(new CustomEvent(OPEN_URL_EVENT, { detail: { url } }))
}

function itemUrl(item: InboxItem): string {
  const url = typeof item.data?.url === 'string' ? item.data.url : ''
  return url.startsWith('/') && !url.startsWith('//') ? url : `/?activity=${encodeURIComponent(item.id)}`
}

function stampFor(now: Date): string {
  const day = new Intl.DateTimeFormat('en-GB', { weekday: 'short', day: 'numeric', month: 'short' }).format(now)
  const time = new Intl.DateTimeFormat('en-GB', { hour: '2-digit', minute: '2-digit', hour12: false }).format(now)
  return `${day} · ${time}`.toUpperCase()
}

function greetingFor(now: Date): string {
  const hour = now.getHours()
  if (hour >= 17 || hour < 4) return 'शुभ संध्या'
  return 'नमस्ते'
}

function isToday(ts: string, now: Date): boolean {
  const date = new Date(ts)
  return !Number.isNaN(date.valueOf()) && date.toDateString() === now.toDateString()
}

function ItemIcon({ kind }: { kind: string }) {
  const Icon = kind === 'medicine_reminder' ? Pill : kind === 'approval_request' ? ShieldCheck : kind === 'question' ? MessageCircleQuestion : Bell
  return (
    <span aria-hidden="true" style={{ width: 34, height: 34, flex: 'none', display: 'grid', placeItems: 'center', borderRadius: 999, background: 'var(--surface-raised)', color: 'var(--sindoor)' }}>
      <Icon size={17} />
    </span>
  )
}

function QuietRow({ lead, title, meta, onOpen }: { lead: React.ReactNode; title: string; meta: string; onOpen: () => void }) {
  return (
    <button
      type="button"
      className="pl-row"
      onClick={onOpen}
      style={{ width: '100%', display: 'flex', alignItems: 'center', gap: 12, minHeight: 56, padding: '6px 12px', border: 0, borderRadius: 14, background: 'transparent', color: 'inherit', textAlign: 'left', cursor: 'pointer' }}
    >
      {lead}
      <span style={{ flex: 1, minWidth: 0 }}>
        <span style={{ display: 'block', fontSize: 15.5, color: 'var(--kajal)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{title}</span>
        <span style={{ display: 'block', fontSize: 13, color: 'var(--ink-55)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{meta}</span>
      </span>
      <ChevronRight size={18} aria-hidden="true" style={{ color: 'var(--ink-40)', flex: 'none' }} />
    </button>
  )
}

export function Threshold({ profile, onPrompt }: { profile: FamilyProfile; onPrompt: (text: string) => void }) {
  const [now, setNow] = useState(() => new Date())
  const [items, setItems] = useState<InboxItem[] | null>(null)
  const [tasks, setTasks] = useState<KriyaTask[]>([])

  useEffect(() => {
    let alive = true
    const load = () => {
      setNow(new Date())
      fetchInbox(50).then(payload => { if (alive) setItems(payload.items) }).catch(() => { if (alive) setItems([]) })
      fetchActiveTasks().then(list => { if (alive) setTasks(list) }).catch(() => undefined)
    }
    load()
    const timer = window.setInterval(load, 60_000)
    window.addEventListener(PUSH_EVENT, load)
    window.addEventListener('focus', load)
    return () => {
      alive = false
      window.clearInterval(timer)
      window.removeEventListener(PUSH_EVENT, load)
      window.removeEventListener('focus', load)
    }
  }, [])

  const { needsYou, done } = groupInbox(items ?? [])
  const waitingTasks = tasks.filter(task => task.status === 'waiting_approval' || task.status === 'waiting_help')
  const runningTasks = tasks.filter(task => !waitingTasks.includes(task))
  const doneToday = done.filter(item => !item.shared_from && isToday(item.ts, now)).length
  const total = doneToday + needsYou.length + tasks.length
  const waitingCount = needsYou.length + waitingTasks.length
  const firstName = (profile.display_name || '').split(' ')[0]

  const focusItem = needsYou[0] ?? null
  const focusTask = focusItem ? null : waitingTasks[0] ?? null
  const rest: React.ReactNode[] = []
  needsYou.slice(focusItem ? 1 : 0).forEach(item => rest.push(
    <QuietRow key={item.id} lead={<ItemIcon kind={item.kind} />} title={item.title || 'Needs you'} meta={item.body || 'Open in Activity'} onOpen={() => openUrl(itemUrl(item))} />,
  ))
  waitingTasks.filter(task => task !== focusTask).concat(runningTasks).forEach(task => rest.push(
    <QuietRow
      key={task.id}
      lead={<KolamGlyph name="Matsya" size={30} live={task.status === 'running'} />}
      title={task.goal}
      meta={task.status === 'running' ? `Matsya · step ${String(task.step ?? 0).padStart(2, '0')}/${task.max_steps ?? 30}` : 'Waiting for you'}
      onOpen={() => openTaskScreen(task.id)}
    />,
  ))

  const sub = items === null
    ? ' '
    : waitingCount === 0
      ? (tasks.length > 0 ? 'Nothing needs you. Matsya is out on an errand.' : 'Nothing needs you right now.')
      : waitingCount === 1 ? 'One thing needs you first.' : `${waitingCount} things need you. This one first.`

  return (
    <div style={{ position: 'relative', display: 'flex', flexDirection: 'column', minHeight: '100%', margin: '-16px -12px 0', padding: '0 4px' }}>
      <div className="pulli-ground" aria-hidden="true" style={{ position: 'absolute', inset: 0, pointerEvents: 'none' }} />
      <div className="pl-in" style={{ position: 'relative', display: 'flex', gap: 12, alignItems: 'flex-start', padding: '22px 16px 0' }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <DotText size={14}>{stampFor(now)}{waitingCount > 0 ? ` · ${waitingCount} NEED YOU` : ''}</DotText>
          <h1 lang="hi" className="font-display" style={{ marginTop: 8, fontSize: 34, color: 'var(--kajal)', overflowWrap: 'anywhere' }}>
            {greetingFor(now)}{firstName ? `, ${firstName}` : ''}
          </h1>
          <p style={{ marginTop: 8, fontSize: 16.5, lineHeight: 1.45, color: 'var(--ink-70)' }}>{sub}</p>
        </div>
        <button
          type="button"
          onClick={() => openUrl('/?activity=')}
          aria-label={total > 0 ? `Today: ${doneToday} of ${total} done. Open Activity` : 'Open Activity'}
          style={{ flex: 'none', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4, marginTop: 4, border: 0, background: 'transparent', cursor: 'pointer', padding: 0 }}
        >
          <KolamDay done={doneToday} total={total} size={84} />
          <DotText size={13} colour="var(--sindoor)">{total > 0 ? (doneToday >= total ? 'ALL DONE' : `${doneToday} OF ${total}`) : 'QUIET DAY'}</DotText>
        </button>
      </div>

      <div className="pl-in pl-in2" style={{ position: 'relative', padding: '22px 12px 0' }}>
        {focusItem ? (
          <FocusCard
            kicker={(KIND_KICKER[focusItem.kind] ?? 'Needs you').toUpperCase()}
            title={focusItem.title || 'Needs you'}
            body={focusItem.body ? <span style={{ display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>{focusItem.body}</span> : undefined}
            action={
              <button type="button" className="n-btn n-btn-accent" style={{ flex: 1, minHeight: 52 }} onClick={() => openUrl(itemUrl(focusItem))}>
                {focusItem.kind === 'approval_request' ? 'Review it' : 'Open it'}
              </button>
            }
          />
        ) : focusTask ? (
          <FocusCard
            kicker={focusTask.status === 'waiting_help' ? 'NEEDS YOUR HELP' : 'NEEDS YOUR OK'}
            lead={<Bindu mood="ask" size={36} decorative />}
            title={focusTask.goal}
            body={focusTask.detail || 'Matsya paused and is waiting for you.'}
            action={<button type="button" className="n-btn n-btn-accent" style={{ flex: 1, minHeight: 52 }} onClick={() => openTaskScreen(focusTask.id)}>Open the errand</button>}
          />
        ) : items !== null ? (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 12, padding: '18px 16px 4px', textAlign: 'center' }}>
            <Bindu mood="calm" size={84} />
            <p style={{ fontSize: 15.5, lineHeight: 1.45, color: 'var(--ink-70)', maxWidth: 280 }}>Ask anything below: in English or हिंदी, typed or spoken.</p>
          </div>
        ) : null}
      </div>

      <div className="pl-in pl-in3" style={{ position: 'relative', padding: '14px 12px 16px' }}>
        {rest.length > 0 && (
          <Fold summary={rest.length === 1 ? 'Also: one more thing' : `Also: ${rest.length} more things`} count={rest.length}>
            {rest}
          </Fold>
        )}
        <Fold summary="Ideas for a first ask">
          {STARTERS.map(starter => (
            <QuietRow
              key={starter.name}
              lead={<KolamGlyph name={starter.name} size={30} />}
              title={starter.role}
              meta={starter.name}
              onOpen={() => onPrompt(starter.prompt)}
            />
          ))}
        </Fold>
      </div>
    </div>
  )
}
