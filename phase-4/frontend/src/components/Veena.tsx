/**
 * Veena, Narad's mascot: the Madhubani veena from the first Narad design (a
 * crowned top gourd with a lotus, a fretted neck, the dashed sindoor halo, the
 * bindu), with a Madhubani face on the lower gourd so she can show what Narad
 * is doing. Four strings, one per avatāra in canonical order; a working
 * avatar's string takes its colour and hums, and an eye-orb rises from the
 * bridge as it is sent out.
 *
 * Drawn once in a 200 × 372 box. The 'face' variant crops to the lower gourd
 * for small places (the header). Colours are the instrument's own, not theme
 * tokens: she is the same ivory veena by day and by night.
 */
import type { CSSProperties } from 'react'
import { AVATAR_NAMES } from '@/lib/avatara-constants'

export type VeenaMood = 'calm' | 'hello' | 'listening' | 'thinking' | 'working' | 'ask' | 'done' | 'oops' | 'hush' | 'sleepy' | 'care'

interface MoodShape {
  brows: string
  eyes: string
  lids: string
  iris: number[][]
  lips: string
  mouth: string
  body: string
  x: string[]
}

const MOODS: Record<VeenaMood, MoodShape> = {
  "calm": {
    "brows": "M71 268Q82 260 93 268M107 268Q118 260 129 268",
    "eyes": "M70 281Q82 270 94 281Q82 290 70 281ZM106 281Q118 270 130 281Q118 290 106 281Z",
    "lids": "",
    "iris": [
      [
        82,
        280.5,
        4.2
      ],
      [
        118,
        280.5,
        4.2
      ]
    ],
    "lips": "M90 309Q95 305 100 307Q105 305 110 309Q100 316 90 309Z",
    "mouth": "",
    "body": "",
    "x": []
  },
  "hello": {
    "brows": "M71 268Q82 260 93 268M107 268Q118 260 129 268",
    "eyes": "",
    "lids": "M71 284Q82 273 93 284M107 284Q118 273 129 284",
    "iris": [],
    "lips": "M88 307Q100 318 112 307Q100 311 88 307Z",
    "mouth": "",
    "body": "vm-bow",
    "x": [
      "gliss"
    ]
  },
  "listening": {
    "brows": "M71 264Q82 255 93 264M107 264Q118 255 129 264",
    "eyes": "M69 281Q82 267 95 281Q82 293 69 281ZM105 281Q118 267 131 281Q118 293 105 281Z",
    "lids": "",
    "iris": [
      [
        82,
        279,
        4.6
      ],
      [
        118,
        279,
        4.6
      ]
    ],
    "lips": "M96 309a4 5 0 1 0 8 0a4 5 0 1 0 -8 0Z",
    "mouth": "",
    "body": "",
    "x": [
      "arcs",
      "all"
    ]
  },
  "thinking": {
    "brows": "M71 268Q82 260 93 268M107 263Q118 255 129 265",
    "eyes": "M70 281Q82 270 94 281Q82 290 70 281ZM106 281Q118 270 130 281Q118 290 106 281Z",
    "lids": "",
    "iris": [
      [
        86,
        277.5,
        4
      ],
      [
        122,
        277.5,
        4
      ]
    ],
    "lips": "",
    "mouth": "M94 309Q102 307 108 310",
    "body": "",
    "x": [
      "dots"
    ]
  },
  "working": {
    "brows": "M71 268Q82 260 93 268M107 268Q118 260 129 268",
    "eyes": "",
    "lids": "M71 280Q82 288 93 280M107 280Q118 288 129 280",
    "iris": [],
    "lips": "M90 309Q95 305 100 307Q105 305 110 309Q100 316 90 309Z",
    "mouth": "",
    "body": "vm-sway",
    "x": [
      "orb"
    ]
  },
  "ask": {
    "brows": "M71 266Q82 258 93 266M107 266Q118 258 129 266",
    "eyes": "M70 281Q82 270 94 281Q82 290 70 281ZM106 281Q118 270 130 281Q118 290 106 281Z",
    "lids": "",
    "iris": [
      [
        82,
        281,
        4.2
      ],
      [
        118,
        281,
        4.2
      ]
    ],
    "lips": "M93 309Q100 313 107 309Q100 307 93 309Z",
    "mouth": "",
    "body": "",
    "x": [
      "askring"
    ]
  },
  "done": {
    "brows": "M71 268Q82 260 93 268M107 268Q118 260 129 268",
    "eyes": "",
    "lids": "M71 284Q82 273 93 284M107 284Q118 273 129 284",
    "iris": [],
    "lips": "M86 305Q100 322 114 305Q100 310 86 305Z",
    "mouth": "",
    "body": "vm-hop",
    "x": [
      "burst"
    ]
  },
  "oops": {
    "brows": "M71 265Q82 268 93 262M107 262Q118 268 129 265",
    "eyes": "M70 281Q82 270 94 281Q82 290 70 281ZM106 281Q118 270 130 281Q118 290 106 281Z",
    "lids": "",
    "iris": [
      [
        79,
        283,
        4
      ],
      [
        115,
        283,
        4
      ]
    ],
    "lips": "",
    "mouth": "M90 310Q95 306 100 310Q105 314 110 310",
    "body": "vm-shake",
    "x": []
  },
  "hush": {
    "brows": "M71 268Q82 260 93 268M107 268Q118 260 129 268",
    "eyes": "",
    "lids": "M71 280Q82 288 93 280M107 280Q118 288 129 280",
    "iris": [],
    "lips": "",
    "mouth": "M95 309Q100 311 105 309",
    "body": "",
    "x": [
      "dim"
    ]
  },
  "sleepy": {
    "brows": "M71 270Q82 265 93 270M107 270Q118 265 129 270",
    "eyes": "",
    "lids": "M72 283Q82 287 92 283M108 283Q118 287 128 283",
    "iris": [],
    "lips": "M97 309a3 3.6 0 1 0 6 0a3 3.6 0 1 0 -6 0Z",
    "mouth": "",
    "body": "vm-droop",
    "x": [
      "zzz",
      "dim"
    ]
  },
  "care": {
    "brows": "M71 267Q82 262 93 267M107 267Q118 262 129 267",
    "eyes": "",
    "lids": "M71 280Q82 288 93 280M107 280Q118 288 129 280",
    "iris": [],
    "lips": "M92 308Q100 313 108 308Q100 310 92 308Z",
    "mouth": "",
    "body": "",
    "x": [
      "glow"
    ]
  }
}

const LABELS: Record<VeenaMood, string> = {"calm": "Veena, calm", "hello": "Veena, namaste", "listening": "Veena, listening", "thinking": "Veena, thinking", "working": "Veena, playing", "ask": "Veena, asking", "done": "Veena, proud", "oops": "Veena, oops", "hush": "Veena, hush", "sleepy": "Veena, sleepy", "care": "Veena, care"}

const KAJAL = '#1b1b1b'
const PAPER = '#ffffff'
const SINDOOR = '#c2410c'
const HALDI = '#fcd34d'
const TULSI = '#065f46'
const STRING_X = [86, 95.3, 104.7, 114]
const FULL_VB = [0, -14, 200, 386]
const FACE_VB = [16, 200, 168, 170]

interface Props {
  mood?: VeenaMood
  size?: number
  variant?: 'full' | 'face'
  /** The avatāra whose string hums (defaults to none). */
  active?: string | null
  /** False holds every pose still (lists, reduced motion is handled in CSS). */
  motion?: boolean
  className?: string
  style?: CSSProperties
}

export function Veena({ mood = 'calm', size = 120, variant = 'full', active = null, motion = true, className, style }: Props) {
  const m = MOODS[mood] ?? MOODS.calm
  const face = variant === 'face'
  const vb = face ? FACE_VB : FULL_VB
  const width = size
  const height = Math.round(size * vb[3] / vb[2])
  const listening = m.x.includes('all')
  const has = (x: string) => m.x.includes(x)
  const activeColour = active ? `var(--avatar-${active.toLowerCase()})` : 'var(--avatar-matsya)'
  const dim = has('dim')
  return (
    <svg
      viewBox={vb.join(' ')}
      width={width}
      height={height}
      role="img"
      aria-label={LABELS[mood] ?? 'Veena'}
      className={className}
      style={{ display: 'block', overflow: face ? 'hidden' : 'visible', flex: 'none', ...style }}
    >
      <g className={motion ? m.body.replace('vm-', 'vn-') : undefined}>
        {has('glow') && <circle cx="100" cy="286" r="74" fill={HALDI} opacity={0.25} />}
        <g className={motion && !face ? 'vn-halo' : undefined}>
          <circle cx="100" cy="286" r="80" className="vn-line" stroke={SINDOOR} strokeWidth={5} strokeDasharray="4 8" />
        </g>
        <rect x="78" y="70" width="44" height="180" fill={PAPER} stroke={KAJAL} strokeWidth={3.5} />
        <path d="M78 108H122M78 134H122M78 160H122M78 186H122M78 212H122" className="vn-line" stroke={KAJAL} strokeWidth={1.6} strokeDasharray="2 2" opacity={0.55} />
        <path d="M88 22C88 -4 112 -4 112 22L100 32Z" fill={PAPER} stroke={KAJAL} strokeWidth={3} strokeLinejoin="round" />
        <path d="M100 -4V-12" className="vn-line" stroke={KAJAL} strokeWidth={2} />
        <circle cx="100" cy="-13" r="2" fill={KAJAL} />
        <circle cx="100" cy="4" r="5" fill={SINDOOR} stroke={KAJAL} strokeWidth={1.8} />
        <circle cx="100" cy="54" r="30" fill={PAPER} stroke={KAJAL} strokeWidth={3.5} />
        <path d="M100 30C110 42 112 50 100 56C88 50 90 42 100 30Z" fill={TULSI} stroke={KAJAL} strokeWidth={1.8} />
        <path d="M100 56C112 62 110 70 100 80C90 70 88 62 100 56Z" fill={SINDOOR} stroke={KAJAL} strokeWidth={1.8} />
        <rect x="78" y="86" width="44" height="5" fill={KAJAL} />
        {AVATAR_NAMES.map((name, i) => {
          const lit = listening || (active != null && active.toLowerCase() === name.toLowerCase())
          return (
            <path
              key={name}
              d={`M${STRING_X[i]} 90V236`}
              className={motion && lit ? 'vn-line vn-vib' : 'vn-line'}
              stroke={lit ? `var(--avatar-${name.toLowerCase()})` : '#1e2447'}
              strokeWidth={lit ? 2.4 : 1.4}
            />
          )
        })}
        <circle cx="100" cy="286" r="62" fill={PAPER} stroke={KAJAL} strokeWidth={3.5} />
        <circle cx="100" cy="286" r="51" className="vn-line" stroke={SINDOOR} strokeWidth={1.8} strokeDasharray="1 4" />
        <path d="M40 286L50 279V293ZM160 286L150 279V293ZM100 345L108 334H92Z" fill={KAJAL} />
        <rect x="80" y="231" width="40" height="8" rx="1.5" fill={HALDI} stroke={KAJAL} strokeWidth={1.6} />
        <circle cx="100" cy="256" r="4.6" fill={SINDOOR} opacity={dim ? 0.35 : 1} className={motion && (mood === 'listening' || mood === 'thinking' || mood === 'ask') ? 'vn-bindu' : undefined} />
        <path d="M100 281Q97 293 99 297Q101 299 103 297" className="vn-line" stroke={KAJAL} strokeWidth={2} />
        <path d={m.brows} className="vn-line" stroke={KAJAL} strokeWidth={2.6} />
        <g className={motion && !face && m.eyes ? 'vn-blink' : undefined}>
          {m.eyes && <path d={m.eyes} fill={PAPER} stroke={KAJAL} strokeWidth={2.4} strokeLinejoin="round" />}
          {m.iris.map(([x, y, r]) => <circle key={`${x}-${y}`} cx={x} cy={y} r={r} fill={KAJAL} />)}
        </g>
        {m.lids && <path d={m.lids} className="vn-line" stroke={KAJAL} strokeWidth={2.8} />}
        {m.lips && <path d={m.lips} fill={SINDOOR} stroke={KAJAL} strokeWidth={1.4} strokeLinejoin="round" />}
        {m.mouth && <path d={m.mouth} className="vn-line" stroke={KAJAL} strokeWidth={2.4} />}
        {!face && has('dots') && [[150, 238, 3], [161, 224, 4], [175, 211, 5.2]].map(([x, y, r], i) => (
          <circle key={i} cx={x} cy={y} r={r} fill="currentColor" className={motion ? 'vn-dot' : undefined} style={{ animationDelay: `${i * 0.25}s` }} />
        ))}
        {!face && has('arcs') && (
          <path d="M172 268Q180 286 172 304M183 259Q194 286 183 313" className="vn-line" stroke="currentColor" strokeWidth={2.2} opacity={0.6} />
        )}
        {has('zzz') && (
          <text x="150" y="246" fill="currentColor" style={{ fontFamily: 'var(--font-hero)', fontSize: 24, fontWeight: 600 }}>z</text>
        )}
        {motion && has('orb') && (
          <g className="vn-orb">
            <circle cx="100" cy="236" r="7.5" fill={activeColour} />
            <circle cx="100" cy="236" r="3.6" fill={PAPER} />
            <circle cx="100" cy="236" r="1.4" fill={KAJAL} />
          </g>
        )}
      </g>
    </svg>
  )
}
