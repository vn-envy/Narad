/**
 * Pulli — the v7 drawing kit. One dot is both the kolam's pulli (the grid a
 * kolam is drawn round at every threshold) and the display's pixel, so the
 * interface is drawn from dots and one continuous line:
 *
 *   Bindu        the mascot: a 7×7 dot face in a kolam ring
 *   KolamGlyph   an avatar as nine dots and one line
 *   Beam         a card's edge; while live, one bright dot rides it
 *   LoopProgress a card's edge drawn as far as the work has gone
 *   DotsRow      steps as dots (done lit, current breathing)
 *   KolamDay     the day's kolam: one lobe per thing, drawn as the day goes
 *   KolamFull    a closed kolam, for "all clear"
 *   VoiceMatrix  the voice waveform as lit dots
 *   Fold         a quiet row that opens in place (the rest of a screen)
 *   FocusCard    the one thing on a screen, with its one filled button
 *   Sheet        a bottom sheet for anything that is not the screen's job
 *
 * Colours come from tokens (currentColor, --avatar-*, --sindoor, --glyph),
 * so every drawing follows day and night.
 */
import { useEffect, useId, type CSSProperties, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { ChevronRight, X } from 'lucide-react'

const AVATAR_KEYS = ['matsya', 'rama', 'krishna', 'parashurama', 'narad'] as const
export type AvatarKey = typeof AVATAR_KEYS[number]

export function avatarKey(name: string | null | undefined): AvatarKey | null {
  const key = (name ?? '').toLowerCase()
  return (AVATAR_KEYS as readonly string[]).includes(key) ? key as AvatarKey : null
}

export function avatarColour(name: string | null | undefined): string {
  const key = avatarKey(name)
  return key ? `var(--avatar-${key})` : 'var(--kajal)'
}

// ── Bindu ─────────────────────────────────────────────────────────────────

export type BinduMood = 'calm' | 'hello' | 'listening' | 'thinking' | 'working' | 'ask' | 'done' | 'oops' | 'sleepy' | 'hush' | 'care'

/** Lit dots per mood, as [row, column] on the 7×7 face. */
const FACES: Record<BinduMood, Array<[number, number]>> = {
  calm: [[2, 1], [2, 5], [4, 2], [4, 3], [4, 4]],
  hello: [[2, 1], [2, 5], [4, 1], [4, 5], [5, 2], [5, 3], [5, 4]],
  listening: [[1, 1], [2, 1], [1, 5], [2, 5], [5, 3]],
  thinking: [[1, 1], [1, 5], [4, 2], [4, 3], [5, 4]],
  working: [[2, 1], [2, 5], [4, 2], [4, 3], [4, 4]],
  ask: [[0, 1], [0, 5], [2, 1], [2, 5], [4, 3], [5, 3]],
  done: [[2, 0], [1, 1], [2, 2], [2, 4], [1, 5], [2, 6], [4, 1], [4, 5], [5, 2], [5, 3], [5, 4]],
  oops: [[1, 0], [1, 2], [2, 1], [3, 0], [3, 2], [1, 4], [1, 6], [2, 5], [3, 4], [3, 6], [4, 3], [5, 2], [5, 4]],
  sleepy: [[2, 0], [2, 1], [2, 2], [2, 4], [2, 5], [2, 6], [4, 3]],
  hush: [[1, 1], [1, 5], [2, 1], [2, 5], [4, 3]],
  care: [[2, 1], [2, 5], [4, 2], [4, 3], [4, 4], [0, 3]],
}

const MOOD_LABELS: Record<BinduMood, string> = {
  calm: 'Narad is here',
  hello: 'Narad says hello',
  listening: 'Narad is listening',
  thinking: 'Narad is thinking',
  working: 'An avatar is at work',
  ask: 'Narad has a question',
  done: 'Done',
  oops: 'Something went wrong',
  sleepy: 'The family Mac is asleep',
  hush: 'Private',
  care: 'With care',
}

interface BinduProps {
  mood?: BinduMood
  size?: number
  /** The avatar at work: the ring takes its colour and a dot rides it. */
  active?: string | null
  motion?: boolean
  className?: string
  style?: CSSProperties
  /** Decorative next to a visible label: hide it from screen readers. */
  decorative?: boolean
}

export function Bindu({ mood = 'calm', size = 96, active = null, motion = true, className, style, decorative = false }: BinduProps) {
  const lit = new Set((FACES[mood] ?? FACES.calm).map(([r, c]) => `${r}:${c}`))
  const small = size < 48
  const pitch = 9
  const rOn = small ? 4.6 : 3.3
  const rOff = small ? 0 : 1.4
  const ringW = small ? 2.4 : 1.5
  const x0 = 50 - 3 * pitch
  const y0 = 50 - 3 * pitch
  const activeKey = avatarKey(active)
  const ringColour = activeKey ? `var(--avatar-${activeKey})` : mood === 'hello' || mood === 'done' || mood === 'care' ? 'var(--sindoor)' : 'currentColor'
  const faceClass = motion ? ({ hello: 'pl-hop', oops: 'pl-shake', done: 'pl-pop' } as Partial<Record<BinduMood, string>>)[mood] : undefined

  const dots: ReactNode[] = []
  for (let row = 0; row < 7; row++) {
    for (let c = 0; c < 7; c++) {
      const cx = x0 + c * pitch
      const cy = y0 + row * pitch
      if (lit.has(`${row}:${c}`)) {
        const breathe = motion && mood === 'listening' && (row === 1 || row === 2)
        dots.push(<circle key={`${row}-${c}`} cx={cx} cy={cy} r={rOn} fill="currentColor" className={breathe ? 'pl-breathe' : undefined} />)
      } else if (rOff) {
        dots.push(<circle key={`${row}-${c}`} cx={cx} cy={cy} r={rOff} fill="var(--dot-off)" />)
      }
    }
  }

  let ring: ReactNode
  if (mood === 'thinking' && motion) {
    ring = (
      <>
        <circle cx="50" cy="50" r="46" className="pl-loop" stroke="var(--glyph)" strokeWidth={ringW} />
        <g className="pl-orbit">
          <circle cx="50" cy="4" r={small ? 5 : 4.2} fill="var(--sindoor)" />
          <circle cx="50" cy="4" r={small ? 3.4 : 2.8} fill="var(--sindoor)" opacity={0.55} transform="rotate(120 50 50)" />
          <circle cx="50" cy="4" r={small ? 2.4 : 2} fill="var(--sindoor)" opacity={0.3} transform="rotate(240 50 50)" />
        </g>
      </>
    )
  } else if (activeKey && motion) {
    ring = (
      <>
        <circle cx="50" cy="50" r="46" className="pl-loop" stroke={ringColour} strokeWidth={ringW} opacity={0.35} />
        <circle cx="50" cy="50" r="46" pathLength={1} className="pl-beam" stroke={ringColour} strokeWidth={small ? 4.5 : 3} />
      </>
    )
  } else if (mood === 'sleepy') {
    ring = <circle cx="50" cy="50" r="46" pathLength={1} className="pl-loop" stroke="var(--glyph)" strokeWidth={ringW} strokeDasharray=".02 .04" />
  } else {
    ring = <circle cx="50" cy="50" r="46" className="pl-loop" stroke={ringColour} strokeWidth={ringW} />
  }

  return (
    <svg
      viewBox="-6 -6 112 112"
      width={size}
      height={size}
      role={decorative ? undefined : 'img'}
      aria-hidden={decorative || undefined}
      aria-label={decorative ? undefined : MOOD_LABELS[mood]}
      className={className}
      style={{ display: 'block', flex: 'none', overflow: 'visible', color: 'var(--kajal)', ...style }}
    >
      {ring}
      <g className={faceClass}>{dots}</g>
      {mood === 'care' && <circle cx="50" cy={y0} r={rOn} fill="var(--sindoor)" />}
      {mood === 'sleepy' && !small && (
        <text x="80" y="20" fill="var(--ink-55)" className="font-dot" style={{ fontSize: 18 }}>z</text>
      )}
    </svg>
  )
}

// ── Kolam glyphs: an avatar as nine dots and one line ─────────────────────

const KOLAM: Record<AvatarKey, ReactNode> = {
  matsya: (
    <>
      <path d="M8 30C8 14 32 10 44 30C32 50 8 46 8 30Z" />
      <path d="M44 30C48 20 54 16 56 18C57 26 57 34 56 42C54 44 48 40 44 30Z" />
      <circle cx="20" cy="26" r="2.4" fill="currentColor" stroke="none" />
    </>
  ),
  rama: (
    <>
      <path d="M16 8C42 18 42 42 16 52" />
      <path d="M16 8V52" strokeDasharray="2 3" strokeWidth={1.4} />
      <path d="M6 30H54" />
      <path d="M46 22L54 30L46 38" />
    </>
  ),
  krishna: (
    <>
      <path d="M8 52L52 8" />
      <path d="M52 8C60 22 46 40 34 30C36 20 44 10 52 8Z" />
      <circle cx="40" cy="14" r="2" fill="currentColor" stroke="none" />
      <circle cx="32" cy="22" r="1.6" fill="currentColor" stroke="none" />
    </>
  ),
  parashurama: (
    <>
      <path d="M30 56V10" />
      <path d="M30 12C42 6 54 12 54 26C46 30 38 30 30 22Z" />
      <path d="M22 44H38" strokeWidth={1.4} />
    </>
  ),
  narad: (
    <>
      <circle cx="30" cy="30" r="22" />
      <circle cx="30" cy="30" r="5" fill="currentColor" stroke="none" />
    </>
  ),
}

export function KolamGlyph({ name, size = 34, live = false, dots = true, className, style }: {
  name: string
  size?: number
  /** The line draws itself back and forth while that avatar works. */
  live?: boolean
  /** The nine dots of the grid behind the line. */
  dots?: boolean
  className?: string
  style?: CSSProperties
}) {
  const key = avatarKey(name) ?? 'narad'
  return (
    <svg
      viewBox="0 0 60 60"
      width={size}
      height={size}
      aria-hidden="true"
      className={className}
      style={{ display: 'block', flex: 'none', overflow: 'visible', color: `var(--avatar-${key})`, ...style }}
    >
      {dots && [10, 30, 50].flatMap(x => [10, 30, 50].map(y => <circle key={`${x}-${y}`} cx={x} cy={y} r={1.5} fill="var(--glyph)" />))}
      <g className={live ? 'pl-klive' : undefined} fill="none" stroke="currentColor" strokeWidth={2.6} strokeLinecap="round" strokeLinejoin="round">
        {KOLAM[key]}
      </g>
    </svg>
  )
}

/** An avatar's name as the machine says it, with its glyph. */
export function AvatarTag({ name, detail, live = false }: { name: string; detail?: string; live?: boolean }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8, color: avatarColour(name), minWidth: 0 }}>
      <KolamGlyph name={name} size={22} dots={false} live={live} />
      <span className="font-dot" style={{ fontSize: 14.5, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
        {name.toUpperCase()}{detail ? ` · ${detail.toUpperCase()}` : ''}
      </span>
    </span>
  )
}

// ── Lines round cards ─────────────────────────────────────────────────────

/** A card's edge. Live: one bright dot rides it. Still: a thin closed line. */
export function Beam({ colour = 'var(--sindoor)', radius = 20, live = true, width = 2 }: {
  colour?: string
  radius?: number
  live?: boolean
  width?: number
}) {
  return (
    <svg className="pl-frame" aria-hidden="true">
      <rect
        x="1" y="1" width="calc(100% - 2px)" height="calc(100% - 2px)" rx={radius}
        pathLength={1}
        className={live ? 'pl-beam' : 'pl-loop'}
        stroke={colour}
        strokeWidth={width}
      />
    </svg>
  )
}

/** A card's edge as progress: the line drawn as far as the work has gone. */
export function LoopProgress({ colour, done, total, radius = 20 }: { colour: string; done: number; total: number; radius?: number }) {
  const fraction = total > 0 ? Math.max(0.02, Math.min(1, done / total)) : 0.02
  return (
    <svg className="pl-frame" aria-hidden="true">
      <rect x="1" y="1" width="calc(100% - 2px)" height="calc(100% - 2px)" rx={radius} pathLength={1} className="pl-loop" stroke="var(--line)" strokeWidth={1.5} />
      <rect
        x="1" y="1" width="calc(100% - 2px)" height="calc(100% - 2px)" rx={radius} pathLength={1}
        className="pl-loop pl-drawn" stroke={colour} strokeWidth={2.5} strokeDasharray={`${fraction.toFixed(3)} 1`}
      />
    </svg>
  )
}

/** Steps as dots: done ones lit, the current one breathing, the rest quiet. */
export function DotsRow({ total, done, colour, gap = 10, r = 3.2, live = true, label }: {
  total: number
  done: number
  colour: string
  gap?: number
  r?: number
  live?: boolean
  label?: string
}) {
  const count = Math.max(1, Math.min(total, 40))
  const current = Math.max(0, Math.min(done, count))
  const width = count * gap
  return (
    <svg
      viewBox={`0 0 ${width} ${2 * r + 2}`}
      width={width}
      height={2 * r + 2}
      role={label ? 'img' : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      style={{ display: 'block', maxWidth: '100%', flex: 'none' }}
    >
      {Array.from({ length: count }, (_, i) => {
        const cx = gap / 2 + i * gap
        if (i < current - 1) return <circle key={i} cx={cx} cy={r + 1} r={r} fill={colour} />
        if (i === current - 1) return <circle key={i} cx={cx} cy={r + 1} r={r + 0.8} fill={colour} className={live ? 'pl-breathe' : undefined} />
        return <circle key={i} cx={cx} cy={r + 1} r={Math.max(1, r - 1.2)} fill="var(--glyph)" />
      })}
    </svg>
  )
}

function hypotrochoid(R: number, r: number, d: number, turns: number, n: number): string {
  const pts: string[] = []
  for (let i = 0; i <= n; i++) {
    const th = (2 * Math.PI * i / n) * turns
    const x = (R - r) * Math.cos(th) + d * Math.cos((R - r) / r * th)
    const y = (R - r) * Math.sin(th) - d * Math.sin((R - r) / r * th)
    pts.push(`${(50 + x).toFixed(1)} ${(50 + y).toFixed(1)}`)
  }
  return `M${pts.join('L')}Z`
}

const DAY_PATH = hypotrochoid(40, 8, 17, 1, 480)
const FULL_PATH = hypotrochoid(40, 10, 22, 5, 800)

/** The day's kolam: five lobes, drawn as far as the day has gone. */
export function KolamDay({ done, total, size = 84 }: { done: number; total: number; size?: number }) {
  const fraction = total > 0 ? Math.max(0.02, Math.min(1, done / total)) : 1
  return (
    <svg viewBox="-4 -4 108 108" width={size} height={size} role="img" aria-label={total > 0 ? `${done} of ${total} done today` : 'Nothing to do today'} style={{ display: 'block', overflow: 'visible', flex: 'none' }}>
      {[0, 1, 2].flatMap(i => [0, 1, 2].map(j => <circle key={`${i}-${j}`} cx={50 + (i - 1) * 22} cy={50 + (j - 1) * 22} r={1.5} fill="var(--glyph)" />))}
      <path d={DAY_PATH} fill="none" stroke="var(--glyph)" strokeWidth={1.2} />
      <path d={DAY_PATH} pathLength={1} className="pl-drawn" fill="none" stroke="var(--sindoor)" strokeWidth={2.2} strokeLinecap="round" strokeDasharray={`${fraction.toFixed(3)} 1`} />
    </svg>
  )
}

/** A closed kolam round a 5×5 grid: nothing left to do. */
export function KolamFull({ size = 220, live = true }: { size?: number; live?: boolean }) {
  return (
    <svg viewBox="-10 -10 120 120" width={size} height={size} aria-hidden="true" style={{ display: 'block', overflow: 'visible' }}>
      {[0, 1, 2, 3, 4].flatMap(i => [0, 1, 2, 3, 4].map(j => <circle key={`${i}-${j}`} cx={50 + (i - 2) * 16} cy={50 + (j - 2) * 16} r={1.6} fill="var(--glyph)" />))}
      <path d={FULL_PATH} pathLength={1} className={live ? 'pl-draw' : undefined} fill="none" stroke="var(--sindoor)" strokeWidth={1.6} strokeLinejoin="round" />
    </svg>
  )
}

const VOICE_HEIGHTS = [2, 3, 5, 4, 7, 9, 6, 8, 5, 7, 9, 8, 6, 4, 7, 5, 3, 5, 4, 3, 2]

/** The voice as lit dots; `level` (0–1) scales the bars, `live` lets them move. */
export function VoiceMatrix({ cols = 21, rows = 9, pitch = 14, level = 1, live = true, colour = 'var(--sindoor)' }: {
  cols?: number
  rows?: number
  pitch?: number
  level?: number
  live?: boolean
  colour?: string
}) {
  const width = cols * pitch
  const height = rows * pitch
  const dots: ReactNode[] = []
  for (let c = 0; c < cols; c++) {
    const h = Math.max(1, Math.round(VOICE_HEIGHTS[c % VOICE_HEIGHTS.length] * Math.max(0.15, Math.min(1, level))))
    for (let r = 0; r < rows; r++) {
      const cx = pitch / 2 + c * pitch
      const cy = pitch / 2 + r * pitch
      const on = Math.abs(r - (rows - 1) / 2) <= (h - 1) / 2
      dots.push(on
        ? <circle key={`${c}-${r}`} cx={cx} cy={cy} r={pitch * 0.3} fill={colour} className={live ? 'pl-v' : undefined} style={live ? { animationDelay: `${(c % 7) * -0.13}s` } : undefined} />
        : <circle key={`${c}-${r}`} cx={cx} cy={cy} r={pitch * 0.11} fill="var(--glyph)" />)
    }
  }
  return (
    <svg viewBox={`0 0 ${width} ${height}`} width={width} height={height} aria-hidden="true" style={{ display: 'block', maxWidth: '100%' }}>
      {dots}
    </svg>
  )
}

// ── Screen structure: one focus, the rest folded ──────────────────────────

/** The machine's short words: times, counts, status. */
export function DotText({ children, size = 14, colour = 'var(--ink-55)', style }: { children: ReactNode; size?: number; colour?: string; style?: CSSProperties }) {
  return <span className="font-dot" style={{ fontSize: size, color: colour, whiteSpace: 'nowrap', ...style }}>{children}</span>
}

/** A quiet row that opens in place: what it is, and how many. */
export function Fold({ summary, count, children, defaultOpen = false, style }: {
  summary: ReactNode
  count?: number
  children: ReactNode
  defaultOpen?: boolean
  style?: CSSProperties
}) {
  return (
    <details className="pl-fold" open={defaultOpen || undefined} style={style}>
      <summary className="pl-row" style={{ display: 'flex', alignItems: 'center', gap: 10, minHeight: 52, padding: '0 12px', borderRadius: 16, color: 'var(--ink-70)' }}>
        <ChevronRight size={18} className="pl-chev" aria-hidden="true" style={{ flex: 'none', color: 'var(--ink-40)' }} />
        <span style={{ flex: 1, minWidth: 0, fontSize: 15 }}>{summary}</span>
        {typeof count === 'number' && <DotText size={14}>{count}</DotText>}
      </summary>
      <div className="pl-fold-body" style={{ padding: '2px 0 8px' }}>{children}</div>
    </details>
  )
}

/** The one thing on a screen: a lifted card, one title, one line, one filled button. */
export function FocusCard({ kicker, lead, title, body, action, colour = 'var(--sindoor)', live = true, children }: {
  kicker: ReactNode
  lead?: ReactNode
  title: ReactNode
  body?: ReactNode
  action?: ReactNode
  colour?: string
  live?: boolean
  children?: ReactNode
}) {
  return (
    <div style={{ position: 'relative', padding: 18, borderRadius: 24, background: 'var(--surface-raised)', boxShadow: 'var(--lift)' }}>
      {live && <Beam colour={colour} radius={24} />}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10 }}>
        <DotText size={14} colour={colour}>{kicker}</DotText>
        {lead}
      </div>
      <div className="font-display" style={{ marginTop: 12, fontSize: 21, lineHeight: 1.22, fontWeight: 650, fontStretch: '105%', color: 'var(--kajal)', overflowWrap: 'anywhere' }}>{title}</div>
      {body && <div style={{ marginTop: 6, fontSize: 15, lineHeight: 1.5, color: 'var(--ink-70)', overflowWrap: 'anywhere' }}>{body}</div>}
      {children}
      {action && <div style={{ display: 'flex', marginTop: 18 }}>{action}</div>}
    </div>
  )
}

/** A bottom sheet: for anything that is not this screen's one job. */
export function Sheet({ title, open, onClose, children }: { title: string; open: boolean; onClose: () => void; children: ReactNode }) {
  const titleId = useId()
  useEffect(() => {
    if (!open) return
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, onClose])
  if (!open) return null
  return createPortal(
    <div style={{ position: 'fixed', inset: 0, zIndex: 80, display: 'flex', flexDirection: 'column', justifyContent: 'flex-end' }}>
      <button type="button" aria-label="Close" className="pl-scrim" onClick={onClose} style={{ position: 'absolute', inset: 0, border: 0, background: 'rgba(0,0,0,0.36)', cursor: 'pointer' }} />
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="pl-sheet panel-scroll"
        style={{
          position: 'relative',
          width: '100%',
          maxWidth: 640,
          margin: '0 auto',
          maxHeight: 'calc(var(--app-height, 100dvh) - 48px)',
          overflowY: 'auto',
          background: 'var(--paper)',
          color: 'var(--kajal)',
          borderRadius: '28px 28px 0 0',
          padding: '10px 18px calc(24px + env(safe-area-inset-bottom))',
        }}
      >
        <div aria-hidden="true" style={{ width: 40, height: 5, borderRadius: 999, background: 'var(--line)', margin: '0 auto 12px' }} />
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
          <h2 id={titleId} className="font-display" style={{ fontSize: 24 }}>{title}</h2>
          <button type="button" className="n-icon-btn" onClick={onClose} aria-label="Close" style={{ background: 'var(--surface-raised)' }}>
            <X size={20} aria-hidden="true" />
          </button>
        </div>
        <div style={{ marginTop: 8 }}>{children}</div>
      </div>
    </div>,
    document.body,
  )
}

/** A screen's title: display type, with the machine's short status beside it. */
export function ScreenTitle({ title, status, statusColour, children }: { title: string; status?: ReactNode; statusColour?: string; children?: ReactNode }) {
  return (
    <div style={{ padding: '14px 4px 0' }}>
      <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', gap: 10 }}>
        <h1 className="font-display" style={{ fontSize: 32, color: 'var(--kajal)' }}>{title}</h1>
        {status && <DotText size={14} colour={statusColour}>{status}</DotText>}
      </div>
      {children && <p style={{ marginTop: 6, fontSize: 15.5, lineHeight: 1.45, color: 'var(--ink-70)' }}>{children}</p>}
    </div>
  )
}
