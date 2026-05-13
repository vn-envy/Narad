import { useEffect, useState } from 'react'
import {
  Sheet,
  SheetClose,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from '@/components/ui/sheet'
import { X } from 'lucide-react'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Badge } from '@/components/ui/badge'
import { Separator } from '@/components/ui/separator'
import { MadhubaniBorder } from './MadhubaniBorder'

const API = 'http://localhost:8000'

interface KarmaEvent {
  id: string
  ts: string
  action: 'promoted' | 'accepted' | 'reverted' | 'expired'
  sutra_id: string
  avatar: string
  detail: string
}

interface KarmaSummary {
  total_events: number
  by_action: Record<string, number>
  recent: KarmaEvent[]
}

const ACTION_META: Record<string, { bg: string; color: string; label: string; meaning: string }> = {
  promoted: {
    bg: '#065f46', color: '#fcfaf2',
    label: 'Promoted',
    meaning: 'Tapas scored this session ≥ 0.75. It entered the 24h cooldown as a new Sutra.',
  },
  accepted: {
    bg: '#2d2a26', color: '#fcfaf2',
    label: 'Accepted',
    meaning: 'You skipped the cooldown. This pattern is now being injected into matching avatar prompts.',
  },
  reverted: {
    bg: '#c2410c', color: '#fcfaf2',
    label: 'Reverted',
    meaning: 'You rejected this Sutra. It will never influence responses.',
  },
  expired: {
    bg: '#78716c', color: '#fcfaf2',
    label: 'Expired',
    meaning: 'This Sutra exceeded its TTL (90 days) and was removed from active context.',
  },
}

const AVATAR_COLOURS: Record<string, string> = {
  Matsya:      '#065f46',
  Varaha:      '#c2410c',
  Narasimha:   '#c2410c',
  Rama:        '#2d2a26',
  Krishna:     '#065f46',
  Buddha:      '#92610a',
  Parashurama: '#57534e',
}

function relativeTime(ts: string): string {
  const diff = Date.now() - new Date(ts).getTime()
  const mins = Math.floor(diff / 60_000)
  if (mins < 1)  return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24)  return `${hrs}h ago`
  return `${Math.floor(hrs / 24)}d ago`
}


// ── Karma legend ──────────────────────────────────────────────────────────────

function KarmaLegend({ byAction }: { byAction: Record<string, number> }) {
  return (
    <div
      className="px-4 py-3 flex-shrink-0"
      style={{ background: 'var(--speckle)' }}
    >
      <p className="font-body text-[11px] leading-[1.55] m-0" style={{ color: 'var(--kajal)', opacity: 0.80 }}>
        <strong>Karma</strong> is the append-only audit trail of every change to your Sutra bank.
        Every time Tapas promotes, you accept, revert, or a Sutra expires — it is recorded here.
      </p>

      <Separator className="my-2 opacity-20" />

      {/* Event type glossary */}
      <div className="flex flex-col gap-[5px]">
        {Object.entries(ACTION_META).map(([action, meta]) => (
          <div key={action} className="flex items-start gap-2">
            <span
              className="text-chip px-[7px] py-px rounded whitespace-nowrap flex-shrink-0 mt-px"
              style={{ background: meta.bg, color: meta.color }}
            >
              {meta.label}
            </span>
            <span className="font-body text-[10px] opacity-65 leading-[1.4]" style={{ color: 'var(--kajal)' }}>{meta.meaning}</span>
          </div>
        ))}
      </div>

      {Object.keys(byAction).length > 0 && (
        <>
          <Separator className="my-2 opacity-20" />
          <div className="flex gap-1.5 flex-wrap">
            {Object.entries(byAction).map(([action, count]) => {
              const meta = ACTION_META[action] ?? { bg: '#78716c', color: '#fcfaf2', label: action }
              return (
                <div
                  key={action}
                  className="flex flex-col items-center px-3.5 py-1.5 rounded organic-border min-w-[60px]"
                  style={{ background: meta.bg }}
                >
                  <span className="font-mono text-[20px] font-bold leading-none" style={{ color: meta.color }}>
                    {count}
                  </span>
                  <span className="text-chip mt-0.5 opacity-85" style={{ color: meta.color }}>
                    {meta.label}
                  </span>
                </div>
              )
            })}
          </div>
        </>
      )}
    </div>
  )
}


// ── Karma Sheet ───────────────────────────────────────────────────────────────

export function KarmaSheet() {
  const [open, setOpen] = useState(false)
  const [summary, setSummary] = useState<KarmaSummary | null>(null)

  useEffect(() => {
    if (!open) return
    fetch(`${API}/karma`)
      .then(r => r.json())
      .then(setSummary)
      .catch(() => {})
  }, [open])

  const events      = summary?.recent ?? []
  const byAction    = summary?.by_action ?? {}
  const totalEvents = summary?.total_events ?? 0

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger
        className="flex items-center gap-1.5 px-2.5 py-1 rounded cursor-pointer relative organic-border hover:bg-kajal/30 transition-colors"
        style={{ background: 'rgba(252,250,242,0.10)', borderColor: 'rgba(252,250,242,0.20)' }}
      >
        <span className="font-deva text-[13px]" style={{ color: 'var(--marigold)', fontFamily: 'var(--font-deva)' }}>कर्म</span>
        <span className="text-chip" style={{ color: 'rgba(252,250,242,0.70)' }}>KARMA</span>
        {totalEvents > 0 && (
          <span className="absolute -top-1.5 -right-1.5 min-w-[15px] h-[15px] rounded-full font-mono text-[8px] font-bold flex items-center justify-center px-0.5"
            style={{ background: 'var(--marigold)', color: 'var(--paper)' }}>
            {totalEvents}
          </span>
        )}
      </SheetTrigger>

      <SheetContent
        side="right"
        style={{ width: 400, maxWidth: 400, padding: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden', background: 'var(--paper)' }}
        className="border-l-2 border-kajal"
      >
        {/* Header */}
        <SheetHeader
          className="px-5 py-3.5 flex-shrink-0 relative overflow-hidden"
          style={{ background: 'var(--kajal)' }}
        >
          <div className="flex items-baseline gap-2">
            <SheetTitle className="flex items-baseline gap-2 flex-1">
              <span className="font-deva text-[20px]" style={{ color: 'var(--marigold)', fontFamily: 'var(--font-deva)' }}>कर्म</span>
              <span className="label-hero text-[16px] leading-none" style={{ color: 'var(--paper)' }}>KARMA LOG</span>
              {totalEvents > 0 && (
                <span className="font-mono text-[10px] ml-1" style={{ color: 'rgba(252,250,242,0.45)' }}>
                  {totalEvents} events
                </span>
              )}
            </SheetTitle>
            {/* Explicit close button */}
            <SheetClose
              className="flex items-center justify-center w-7 h-7 rounded hover:bg-white/10 transition-colors flex-shrink-0"
              style={{ color: 'rgba(252,250,242,0.55)' }}
            >
              <X size={14} />
            </SheetClose>
          </div>
        </SheetHeader>

        <MadhubaniBorder position="bottom" />

        {/* Timeline label */}
        <div className="px-4 py-1.5 flex-shrink-0" style={{ background: 'var(--paper)' }}>
          <span className="label-section" style={{ color: 'var(--kajal)' }}>Recent Events (newest first)</span>
        </div>

        {/* Scrollable timeline — FIRST, takes the majority of space */}
        <div style={{ flex: 1, minHeight: 0, overflow: 'hidden', background: 'var(--paper)' }}>
          <ScrollArea style={{ height: '100%' }}>
            <div className="px-4 pb-4 flex flex-col gap-0 pt-2">
              {events.length === 0 && (
                <p className="font-body text-[11px] opacity-40 text-center py-6" style={{ color: 'var(--kajal)' }}>
                  No karma events yet.
                </p>
              )}
              {events.map((e, i) => {
                const meta        = ACTION_META[e.action] ?? { bg: '#78716c', color: '#fcfaf2', label: e.action, meaning: '' }
                const avatarColor = AVATAR_COLOURS[e.avatar] ?? 'var(--kajal)'
                return (
                  <div key={e.id} className="flex gap-2.5 pb-2.5">
                    <div className="flex flex-col items-center flex-shrink-0 w-3.5">
                      <div className="w-3 h-3 rounded-full flex-shrink-0 mt-[3px]" style={{ background: meta.bg }} />
                      {i < events.length - 1 && (
                        <div className="w-px flex-1 min-h-[18px] mt-[3px]" style={{ background: 'color-mix(in srgb, var(--kajal) 10%, transparent)' }} />
                      )}
                    </div>
                    <div className="flex-1 pb-0.5">
                      <div className="flex items-center gap-1.5 mb-0.5 flex-wrap">
                        <Badge variant="avatar" className="text-chip px-1.5 rounded" style={{ background: meta.bg, color: meta.color }}>
                          {meta.label}
                        </Badge>
                        <Badge variant="avatar" className="text-chip px-1.5 rounded" style={{ background: avatarColor, color: '#fcfaf2' }}>
                          {e.avatar}
                        </Badge>
                        <span className="font-mono text-[9px] ml-auto opacity-40" style={{ color: 'var(--kajal)' }}>
                          {relativeTime(e.ts)}
                        </span>
                      </div>
                      {e.detail && (
                        <p className="font-body text-[11px] m-0 leading-[1.4]" style={{ color: 'var(--kajal)' }}>
                          {e.detail.slice(0, 120)}{e.detail.length > 120 ? '…' : ''}
                        </p>
                      )}
                      <p className="font-mono text-[8px] opacity-30 mt-0.5 mb-0" style={{ color: 'var(--kajal)' }}>
                        {e.sutra_id.slice(0, 8)}…
                      </p>
                    </div>
                  </div>
                )
              })}
            </div>
          </ScrollArea>
        </div>

        {/* Legend — SECOND, fixed at bottom */}
        <Separator className="opacity-20" />
        <KarmaLegend byAction={byAction} />
      </SheetContent>
    </Sheet>
  )
}
