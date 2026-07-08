import { useEffect, useState } from 'react'

/**
 * MaharishiAvatar — the Gurukul's resident teacher, a 2D maharishi
 * inspired by Ved Vyas: topknot, long white beard, saffron robe,
 * rudraksha mala, seated cross-legged.
 *
 * SWAPPING IN CUSTOM ART (no code changes needed):
 *   Drop SVG files into `public/maharishi/` named after each pose:
 *     public/maharishi/idle.svg, teaching.svg, thinking.svg, reading.svg,
 *     blessing.svg, celebrating.svg, quizzing.svg, meditating.svg
 *   The component probes for the file and uses it when present; the inline
 *   placeholder below renders only when no custom file exists.
 */

export type MaharishiPose =
  | 'idle'
  | 'teaching'
  | 'thinking'
  | 'reading'
  | 'blessing'
  | 'celebrating'
  | 'quizzing'
  | 'meditating'

export const MAHARISHI_POSES: MaharishiPose[] = [
  'idle', 'teaching', 'thinking', 'reading',
  'blessing', 'celebrating', 'quizzing', 'meditating',
]

// Palette — theme vars with warm fallbacks.
const SKIN = '#d9a06b'
const SKIN_SHADE = '#c68d59'
const BEARD = '#f6f1e4'
const ROBE = 'var(--marigold, #e8a33d)'
const ROBE_DEEP = '#c77f22'
const INK = 'var(--kajal, #2d2a26)'
const TILAK = '#c2410c'

// ── shared anatomy ─────────────────────────────────────────────────────────────

function Base({ eyes, brows }: { eyes?: React.ReactNode; brows?: React.ReactNode }) {
  return (
    <g>
      {/* seated robe body */}
      <path d="M32 118 Q28 86 46 74 L74 74 Q92 86 88 118 Q88 126 76 127 L44 127 Q32 126 32 118 Z" fill={ROBE} />
      {/* crossed legs base */}
      <path d="M28 118 Q60 132 92 118 Q92 130 78 132 L42 132 Q28 130 28 118 Z" fill={ROBE_DEEP} />
      {/* robe sash */}
      <path d="M52 75 Q60 96 54 118" stroke={ROBE_DEEP} strokeWidth="3.5" fill="none" strokeLinecap="round" />
      {/* mala */}
      <path d="M50 78 Q60 92 70 78" stroke="none" fill="none" />
      {[0, 1, 2, 3, 4, 5, 6].map(i => {
        const t = i / 6
        const x = 50 + 20 * t
        const y = 79 + 12 * Math.sin(Math.PI * t)
        return <circle key={i} cx={x} cy={y} r="1.8" fill="#7a4a21" />
      })}
      {/* neck + head */}
      <rect x="55" y="62" width="10" height="8" rx="3" fill={SKIN_SHADE} />
      <circle cx="60" cy="47" r="19" fill={SKIN} />
      {/* topknot */}
      <circle cx="60" cy="26" r="6.5" fill={BEARD} />
      <path d="M52 30 Q60 22 68 30 Q64 26 60 26 Q56 26 52 30 Z" fill={BEARD} />
      {/* hair sweep */}
      <path d="M42 44 Q42 28 60 28 Q78 28 78 44 Q74 34 60 34 Q46 34 42 44 Z" fill={BEARD} />
      {/* beard */}
      <path d="M44 50 Q44 74 60 80 Q76 74 76 50 Q72 62 60 62 Q48 62 44 50 Z" fill={BEARD} />
      {/* moustache */}
      <path d="M52 56 Q60 61 68 56 Q64 59 60 59 Q56 59 52 56 Z" fill="#e9e2cf" />
      {/* tilak */}
      <path d="M60 36 L58 42 L62 42 Z" fill={TILAK} />
      {/* brows + eyes (pose-overridable) */}
      {brows ?? (
        <g stroke={INK} strokeWidth="1.6" strokeLinecap="round">
          <path d="M50 44 Q53.5 42 57 44" fill="none" />
          <path d="M63 44 Q66.5 42 70 44" fill="none" />
        </g>
      )}
      {eyes ?? (
        <g fill={INK}>
          <circle cx="53.5" cy="48" r="1.8" />
          <circle cx="66.5" cy="48" r="1.8" />
        </g>
      )}
    </g>
  )
}

function RestingArm({ side }: { side: 'left' | 'right' }) {
  const sign = side === 'left' ? -1 : 1
  const shoulderX = 60 + sign * 14
  const handX = 60 + sign * 24
  return (
    <g>
      <path
        d={`M${shoulderX} 80 Q${60 + sign * 26} 92 ${handX} 108`}
        stroke={ROBE}
        strokeWidth="9"
        fill="none"
        strokeLinecap="round"
      />
      <circle cx={handX} cy="110" r="4.5" fill={SKIN} />
    </g>
  )
}

const ClosedEyes = (
  <g stroke={INK} strokeWidth="1.6" strokeLinecap="round" fill="none">
    <path d="M50.5 48 Q53.5 50 56.5 48" />
    <path d="M63.5 48 Q66.5 50 69.5 48" />
  </g>
)

const UpGazeEyes = (
  <g fill={INK}>
    <circle cx="53.5" cy="46.5" r="1.8" />
    <circle cx="66.5" cy="46.5" r="1.8" />
  </g>
)

const RaisedBrow = (
  <g stroke={INK} strokeWidth="1.6" strokeLinecap="round" fill="none">
    <path d="M50 44 Q53.5 42 57 44" />
    <path d="M63 41.5 Q66.5 39.5 70 41.5" />
  </g>
)

// ── poses ──────────────────────────────────────────────────────────────────────

const POSES: Record<MaharishiPose, () => React.ReactNode> = {
  idle: () => (
    <g>
      <Base />
      <RestingArm side="left" />
      <RestingArm side="right" />
    </g>
  ),

  teaching: () => (
    <g>
      <Base />
      <RestingArm side="left" />
      {/* right arm raised, index finger up */}
      <path d="M74 80 Q88 70 90 52" stroke={ROBE} strokeWidth="9" fill="none" strokeLinecap="round" />
      <circle cx="90" cy="49" r="4.5" fill={SKIN} />
      <path d="M90 49 L90 40" stroke={SKIN} strokeWidth="3" strokeLinecap="round" />
    </g>
  ),

  thinking: () => (
    <g>
      <Base eyes={UpGazeEyes} />
      <RestingArm side="left" />
      {/* right hand strokes the beard */}
      <path d="M74 80 Q84 76 74 66" stroke={ROBE} strokeWidth="9" fill="none" strokeLinecap="round" />
      <circle cx="72" cy="65" r="4.5" fill={SKIN} />
      {/* thought marks */}
      <circle cx="88" cy="34" r="2" fill={INK} opacity="0.35" />
      <circle cx="94" cy="26" r="2.6" fill={INK} opacity="0.25" />
    </g>
  ),

  reading: () => (
    <g>
      <Base eyes={ClosedEyes} />
      {/* palm-leaf manuscript held with both hands */}
      <path d="M46 80 Q42 92 48 98" stroke={ROBE} strokeWidth="9" fill="none" strokeLinecap="round" />
      <path d="M74 80 Q78 92 72 98" stroke={ROBE} strokeWidth="9" fill="none" strokeLinecap="round" />
      <g transform="rotate(-4 60 100)">
        <rect x="42" y="94" width="36" height="13" rx="2" fill="#f3e7c8" stroke="#c9b285" strokeWidth="1" />
        <g stroke="#a08a5f" strokeWidth="1.2" strokeLinecap="round">
          <path d="M46 98.5 H74" />
          <path d="M46 102.5 H68" />
        </g>
      </g>
      <circle cx="47" cy="98" r="4" fill={SKIN} />
      <circle cx="73" cy="98" r="4" fill={SKIN} />
    </g>
  ),

  blessing: () => (
    <g>
      <Base />
      <RestingArm side="left" />
      {/* abhaya mudra — right palm forward */}
      <path d="M74 80 Q88 74 92 62" stroke={ROBE} strokeWidth="9" fill="none" strokeLinecap="round" />
      <g transform="rotate(8 93 57)">
        <rect x="88.5" y="50" width="9" height="12" rx="4" fill={SKIN} />
        <g stroke={SKIN_SHADE} strokeWidth="0.9">
          <path d="M91 51 V57" />
          <path d="M93.5 50.5 V57" />
          <path d="M96 51 V57" />
        </g>
      </g>
      {/* gentle radiance */}
      <g stroke={ROBE} strokeWidth="1.4" strokeLinecap="round" opacity="0.6">
        <path d="M102 50 L107 47" />
        <path d="M103 58 L109 58" />
        <path d="M101 66 L106 69" />
      </g>
    </g>
  ),

  celebrating: () => (
    <g>
      <Base />
      {/* both arms up */}
      <path d="M46 80 Q34 68 33 54" stroke={ROBE} strokeWidth="9" fill="none" strokeLinecap="round" />
      <path d="M74 80 Q86 68 87 54" stroke={ROBE} strokeWidth="9" fill="none" strokeLinecap="round" />
      <circle cx="33" cy="51" r="4.5" fill={SKIN} />
      <circle cx="87" cy="51" r="4.5" fill={SKIN} />
      {/* sparkles */}
      <g fill={ROBE}>
        <path d="M26 38 l1.6 3.4 3.4 1.6 -3.4 1.6 -1.6 3.4 -1.6 -3.4 -3.4 -1.6 3.4 -1.6 Z" />
        <path d="M94 34 l1.4 3 3 1.4 -3 1.4 -1.4 3 -1.4 -3 -3 -1.4 3 -1.4 Z" />
        <circle cx="60" cy="18" r="2" />
      </g>
    </g>
  ),

  quizzing: () => (
    <g transform="rotate(2 60 100)">
      <Base brows={RaisedBrow} />
      <RestingArm side="left" />
      {/* right hand presents a small scroll */}
      <path d="M74 80 Q86 84 88 94" stroke={ROBE} strokeWidth="9" fill="none" strokeLinecap="round" />
      <circle cx="89" cy="96" r="4.5" fill={SKIN} />
      <rect x="84" y="84" width="11" height="8" rx="2" fill="#f3e7c8" stroke="#c9b285" strokeWidth="1" />
      <text x="89.5" y="90.5" textAnchor="middle" fontSize="7" fontWeight="700" fill={TILAK}>?</text>
    </g>
  ),

  meditating: () => (
    <g>
      {/* aura */}
      <circle cx="60" cy="72" r="52" fill={ROBE} opacity="0.08" />
      <circle cx="60" cy="72" r="42" fill={ROBE} opacity="0.08" />
      <Base eyes={ClosedEyes} />
      {/* dhyana mudra — hands folded in lap */}
      <path d="M46 80 Q46 96 56 104" stroke={ROBE} strokeWidth="9" fill="none" strokeLinecap="round" />
      <path d="M74 80 Q74 96 64 104" stroke={ROBE} strokeWidth="9" fill="none" strokeLinecap="round" />
      <ellipse cx="60" cy="106" rx="8" ry="4.5" fill={SKIN} />
    </g>
  ),
}

// ── custom-art probing (public/maharishi/{pose}.svg) ──────────────────────────

const customPoseCache: Record<string, boolean> = {}

function useCustomPose(pose: MaharishiPose): string | null {
  const src = `/maharishi/${pose}.svg`
  const [available, setAvailable] = useState<boolean>(customPoseCache[pose] === true)
  useEffect(() => {
    if (pose in customPoseCache) {
      setAvailable(customPoseCache[pose])
      return
    }
    let cancelled = false
    const probe = new Image()
    probe.onload = () => {
      customPoseCache[pose] = true
      if (!cancelled) setAvailable(true)
    }
    probe.onerror = () => {
      customPoseCache[pose] = false
      if (!cancelled) setAvailable(false)
    }
    probe.src = src
    return () => { cancelled = true }
  }, [pose, src])
  return available ? src : null
}

// ── component ──────────────────────────────────────────────────────────────────

interface Props {
  pose?: MaharishiPose
  size?: number
  animate?: boolean
  title?: string
}

export function MaharishiAvatar({ pose = 'idle', size = 96, animate = true, title }: Props) {
  const customSrc = useCustomPose(pose)
  const render = POSES[pose] ?? POSES.idle

  return (
    <div
      style={{
        width: size,
        height: size * (140 / 120),
        display: 'inline-flex',
        alignItems: 'flex-end',
        justifyContent: 'center',
        animation: animate
          ? pose === 'celebrating'
            ? 'maharishi-bounce 0.9s ease-in-out infinite'
            : 'maharishi-breathe 3.6s ease-in-out infinite'
          : undefined,
        transformOrigin: '50% 100%',
      }}
      title={title}
      aria-label={`Maharishi — ${pose}`}
      role="img"
    >
      <style>{`
        @keyframes maharishi-breathe {
          0%, 100% { transform: scale(1); }
          50% { transform: scale(1.015) translateY(-1px); }
        }
        @keyframes maharishi-bounce {
          0%, 100% { transform: translateY(0); }
          50% { transform: translateY(-5px); }
        }
      `}</style>
      {customSrc ? (
        <img src={customSrc} alt={`Maharishi — ${pose}`} style={{ width: '100%', height: '100%', objectFit: 'contain' }} />
      ) : (
        <svg viewBox="0 0 120 140" width="100%" height="100%" xmlns="http://www.w3.org/2000/svg">
          {render()}
        </svg>
      )}
    </div>
  )
}
