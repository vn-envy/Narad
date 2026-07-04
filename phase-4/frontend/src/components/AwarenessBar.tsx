/**
 * AwarenessBar — the right-edge presence rail.
 * Each avatāra appears as its Devanagari initial over its Mahati string
 * position (1–4). Active avatars breathe in their canonical colour.
 */
import { useState } from 'react'
import type { AvatarName, AvatarStatus } from '../hooks/useAvatara'

import { AVATAR_NAMES, AVATAR_COLOURS, AVATAR_RGB, DEVA } from '@/lib/avatara-constants'

interface Props {
  avatars: Record<AvatarName, AvatarStatus>
  activeSteps: number
  onOpenDarshan: () => void
  /** Phone layout: render as a bottom bar instead of the right-edge rail. */
  horizontal?: boolean
}

export function AwarenessBar({
  avatars,
  activeSteps,
  onOpenDarshan,
  horizontal = false,
}: Props) {
  const [hoveredAvatar, setHoveredAvatar] = useState<AvatarName | null>(null)

  return (
    <div
      className={
        horizontal
          ? 'flex flex-row items-center px-4 gap-4 w-full overflow-hidden'
          : 'flex flex-col items-center py-3 gap-3 h-full overflow-hidden'
      }
      style={
        horizontal
          ? {
              height: 'calc(60px + env(safe-area-inset-bottom))',
              paddingBottom: 'env(safe-area-inset-bottom)',
              background: 'var(--kajal)',
              borderTop: '1px solid rgba(252,250,242,0.06)',
            }
          : {
              width: 72,
              background: 'var(--kajal)',
              borderLeft: '1px solid rgba(252,250,242,0.06)',
            }
      }
    >
      {/* Avatar strings */}
      <div
        className={
          horizontal
            ? 'flex flex-row items-center justify-center gap-5 flex-1'
            : 'flex flex-col items-center gap-2.5 flex-1'
        }
      >
        {AVATAR_NAMES.map((name, i) => {
          const st = avatars[name]
          const active = st?.state === 'active'
          const done   = st?.state === 'done'
          const colour = AVATAR_COLOURS[name]
          const rgb    = AVATAR_RGB[name]
          const deva   = DEVA[name]?.charAt(0) ?? name.charAt(0)

          return (
            <div
              key={name}
              className="relative flex flex-col items-center"
              onMouseEnter={() => setHoveredAvatar(name)}
              onMouseLeave={() => setHoveredAvatar(null)}
            >
              {/* Breath halo when active */}
              {active && (
                <span
                  className="absolute inset-[-4px] rounded-full pointer-events-none"
                  style={{
                    background: `radial-gradient(circle, rgba(${rgb},0.45) 0%, transparent 70%)`,
                    animation: 'breath 1.4s ease-in-out infinite',
                  }}
                />
              )}
              <div
                className="relative flex items-center justify-center rounded-full transition-all duration-200"
                style={{
                  width: 32,
                  height: 32,
                  fontFamily: 'var(--font-deva)',
                  fontSize: 15,
                  lineHeight: 1,
                  backgroundColor: active
                    ? colour
                    : done
                    ? `rgba(${rgb}, 0.30)`
                    : 'rgba(252,250,242,0.06)',
                  color: active
                    ? '#fcfaf2'
                    : done
                    ? 'rgba(252,250,242,0.85)'
                    : 'rgba(252,250,242,0.40)',
                  border: active
                    ? `1.5px solid rgba(252,250,242,0.35)`
                    : `1.5px solid rgba(${rgb}, ${done ? 0.4 : 0.28})`,
                }}
              >
                {deva}
              </div>
              {/* Mahati string position */}
              <span
                className="font-mono"
                style={{
                  fontSize: 7,
                  marginTop: 2,
                  letterSpacing: '0.08em',
                  color: active ? colour : 'rgba(252,250,242,0.22)',
                }}
              >
                {i + 1}
              </span>

              {/* Tooltip — left of the rail, or above the bottom bar */}
              {hoveredAvatar === name && (
                <div
                  className={
                    horizontal
                      ? 'absolute bottom-full mb-2 z-50 px-2.5 py-1.5 rounded whitespace-nowrap pointer-events-none'
                      : 'absolute right-full mr-2 z-50 px-2.5 py-1.5 rounded whitespace-nowrap pointer-events-none'
                  }
                  style={{
                    background: 'var(--kajal)',
                    border: `1px solid rgba(${rgb}, 0.45)`,
                    color: 'rgba(252,250,242,0.9)',
                    ...(horizontal
                      ? { left: '50%', transform: 'translateX(-50%)' }
                      : { top: '50%', transform: 'translateY(-50%)' }),
                    boxShadow: '0 4px 16px rgba(0,0,0,0.35)',
                  }}
                >
                  <span className="flex items-baseline gap-1.5">
                    <span style={{ fontFamily: 'var(--font-deva)', color: colour, fontSize: 12 }}>{DEVA[name]}</span>
                    <span className="font-semibold text-[10px]">{name}</span>
                    <span className="font-mono text-[8px] opacity-45">string {i + 1}</span>
                  </span>
                  {st?.discipline && (
                    <span className="block text-[9px] opacity-60 mt-0.5">
                      {st.discipline}
                    </span>
                  )}
                  {st?.task && (
                    <span className="block text-[9px] opacity-60 max-w-[160px] truncate">
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
          style={{ color: 'var(--haldi)', fontSize: 9, lineHeight: '1.2' }}
        >
          <div style={{ fontSize: 11, fontWeight: 700 }}>{activeSteps}</div>
          <div style={{ opacity: 0.7 }}>steps</div>
        </div>
      )}

      {/* Dashboard open button */}
      <button
        onClick={onOpenDarshan}
        title="Open Narad Dashboard (Traces, Memory, Tasks, Karma)"
        className="flex flex-col items-center gap-0.5 group transition-opacity opacity-60 hover:opacity-100"
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
