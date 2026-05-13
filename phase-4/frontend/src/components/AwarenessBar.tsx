import { useState } from 'react'
import type { AvatarName, AvatarStatus } from '../hooks/useAvatara'

const AVATAR_NAMES: AvatarName[] = [
  'Matsya', 'Varaha', 'Narasimha', 'Rama', 'Krishna', 'Buddha', 'Parashurama', 'Vamana'
]

const AVATAR_COLOURS: Record<AvatarName, string> = {
  Matsya:      '#065f46',
  Varaha:      '#c2410c',
  Narasimha:   '#c2410c',
  Rama:        '#2d2a26',
  Krishna:     '#065f46',
  Buddha:      '#92610a',
  Parashurama: '#57534e',
  Vamana:      '#78716c',
}

const AVATAR_ABBREV: Record<AvatarName, string> = {
  Matsya:      'Ma',
  Varaha:      'Va',
  Narasimha:   'Na',
  Rama:        'Ra',
  Krishna:     'Kr',
  Buddha:      'Bu',
  Parashurama: 'Pa',
  Vamana:      'Vm',
}

interface Props {
  avatars: Record<AvatarName, AvatarStatus>
  totalTokens: number
  activeSteps: number
  onOpenDarshan: () => void
}

export function AwarenessBar({ avatars, totalTokens, activeSteps, onOpenDarshan }: Props) {
  const [hoveredAvatar, setHoveredAvatar] = useState<AvatarName | null>(null)

  const tokStr = totalTokens >= 1000
    ? `${(totalTokens / 1000).toFixed(1)}k`
    : String(totalTokens)

  return (
    <div
      className="flex flex-col items-center py-3 gap-3 h-full overflow-hidden"
      style={{
        width: 72,
        background: 'var(--kajal)',
        borderLeft: '1px solid rgba(252,250,242,0.06)',
      }}
    >
      {/* Avatar dots */}
      <div className="flex flex-col items-center gap-2 flex-1">
        {AVATAR_NAMES.map(name => {
          const st = avatars[name]
          const active = st?.state === 'active'
          const done   = st?.state === 'done'
          const colour = AVATAR_COLOURS[name]

          return (
            <div
              key={name}
              className="relative flex flex-col items-center"
              onMouseEnter={() => setHoveredAvatar(name)}
              onMouseLeave={() => setHoveredAvatar(null)}
            >
              {/* Pulse ring when active */}
              {active && (
                <span
                  className="absolute inset-0 rounded-full animate-ping"
                  style={{ backgroundColor: colour, opacity: 0.35 }}
                />
              )}
              <div
                className="flex items-center justify-center rounded-full text-[8px] font-bold transition-all"
                style={{
                  width: 28,
                  height: 28,
                  backgroundColor: active
                    ? colour
                    : done
                    ? `${colour}66`
                    : 'rgba(252,250,242,0.07)',
                  color: active || done ? '#fcfaf2' : 'rgba(252,250,242,0.35)',
                  border: active ? `1.5px solid ${colour}` : '1.5px solid transparent',
                  letterSpacing: '0.04em',
                }}
              >
                {AVATAR_ABBREV[name]}
              </div>

              {/* Tooltip */}
              {hoveredAvatar === name && (
                <div
                  className="absolute left-full ml-2 z-50 px-2 py-1 rounded text-[10px] whitespace-nowrap pointer-events-none"
                  style={{
                    background: 'var(--kajal)',
                    border: '1px solid rgba(252,250,242,0.15)',
                    color: 'rgba(252,250,242,0.85)',
                    top: '50%',
                    transform: 'translateY(-50%)',
                  }}
                >
                  <span className="font-semibold">{name}</span>
                  {st?.task && (
                    <span className="block text-[9px] opacity-60 max-w-[140px] truncate">
                      {st.task}
                    </span>
                  )}
                </div>
              )}
            </div>
          )
        })}
      </div>

      {/* Step count */}
      {activeSteps > 0 && (
        <div
          className="text-center font-mono"
          style={{ color: '#FFC837', fontSize: 9, lineHeight: '1.2' }}
        >
          <div style={{ fontSize: 11, fontWeight: 700 }}>{activeSteps}</div>
          <div style={{ opacity: 0.7 }}>steps</div>
        </div>
      )}

      {/* Token count */}
      <div
        className="text-center font-mono"
        style={{ color: 'rgba(252,250,242,0.35)', fontSize: 9, lineHeight: '1.2' }}
      >
        <div style={{ fontSize: 10, color: 'rgba(252,250,242,0.55)' }}>{tokStr}</div>
        <div>tok</div>
      </div>

      {/* Darshan open button */}
      <button
        onClick={onOpenDarshan}
        title="Open Darshan Dashboard"
        className="flex flex-col items-center gap-0.5 group transition-opacity"
        style={{ opacity: 0.55 }}
      >
        <span
          className="flex items-center justify-center rounded"
          style={{
            width: 28,
            height: 22,
            border: '1px solid rgba(252,250,242,0.20)',
            fontSize: 12,
            color: 'rgba(252,250,242,0.7)',
            background: 'rgba(252,250,242,0.06)',
          }}
        >
          ⊞
        </span>
        <span
          className="font-mono uppercase tracking-widest"
          style={{ fontSize: 7, color: 'rgba(252,250,242,0.4)', letterSpacing: '0.12em' }}
        >
          Darshan
        </span>
      </button>
    </div>
  )
}
