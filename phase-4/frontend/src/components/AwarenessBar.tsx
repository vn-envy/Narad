import { useState } from 'react'
import type { AvatarName, AvatarStatus } from '../hooks/useAvatara'

import { AVATAR_NAMES, AVATAR_COLOURS, AVATAR_ABBREV } from '@/lib/avatara-constants'

interface Props {
  avatars: Record<AvatarName, AvatarStatus>
  activeSteps: number
  onOpenDarshan: () => void
}

export function AwarenessBar({
  avatars,
  activeSteps,
  onOpenDarshan,
}: Props) {
  const [hoveredAvatar, setHoveredAvatar] = useState<AvatarName | null>(null)

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
                  {st?.discipline && (
                    <span className="block text-[9px] opacity-60">
                      {st.discipline}
                    </span>
                  )}
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

      {/* Dashboard open button */}
      <button
        onClick={onOpenDarshan}
        title="Open Narad Dashboard (Traces, Memory, Tasks, Karma)"
        className="flex flex-col items-center gap-0.5 group transition-opacity"
        style={{ opacity: 0.6 }}
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
          Dashboard
        </span>
      </button>
    </div>
  )
}
