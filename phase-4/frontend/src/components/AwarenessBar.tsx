/**
 * AwarenessBar — the right-edge presence rail.
 * Each avatāra appears as its Devanagari initial over its Mahati string
 * position (1–4). Active avatars breathe in their canonical colour.
 */
import { useState } from 'react'
import type { AvatarName, AvatarStatus } from '../hooks/useAvatara'

import { AVATAR_NAMES, AVATAR_COLOURS, AVATAR_RGB, DEVA } from '@/lib/avatara-constants'
import { SURFACE_ITEMS, type AppSurface } from '@/lib/surfaces'

interface Props {
  avatars: Record<AvatarName, AvatarStatus>
  activeSteps: number
  activeSurface: AppSurface
  onNavigate: (surface: AppSurface) => void
  /** Phone layout: render as a bottom bar instead of the right-edge rail. */
  horizontal?: boolean
  /** Unread Activity items, shown as a badge on Activity. */
  unread?: number
}

function CountBadge({ count, tone, horizontal }: { count: number; tone: string; horizontal: boolean }) {
  return (
    <span
      aria-hidden="true"
      style={{
        position: 'absolute',
        top: 5,
        right: horizontal ? '22%' : 6,
        minWidth: 15,
        height: 15,
        padding: '0 4px',
        borderRadius: 999,
        background: tone,
        color: '#fcfaf2',
        fontSize: 8,
        fontWeight: 700,
        display: 'grid',
        placeItems: 'center',
      }}
    >
      {count > 99 ? '99+' : count}
    </span>
  )
}

export function AwarenessBar({
  avatars,
  activeSteps,
  activeSurface,
  onNavigate,
  horizontal = false,
  unread = 0,
}: Props) {
  const [hoveredAvatar, setHoveredAvatar] = useState<AvatarName | null>(null)

  return (
    <div
      className={
        horizontal
          ? 'flex flex-row items-center w-full overflow-hidden'
          : 'flex flex-col items-center h-full overflow-hidden'
      }
      style={
        horizontal
          ? {
              height: 'calc(62px + env(safe-area-inset-bottom))',
              paddingBottom: 'env(safe-area-inset-bottom)',
              background: 'var(--kajal)',
              borderTop: '1px solid rgba(252,250,242,0.06)',
            }
          : {
              width: 72,
              padding: '10px 8px',
              background: 'var(--kajal)',
              borderLeft: '1px solid rgba(252,250,242,0.06)',
            }
      }
    >
      {/* Avatar strings */}
      <div
        className={
          horizontal
            ? 'hidden'
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
      {!horizontal && activeSteps > 0 && (
        <div
          className="text-center font-mono"
          style={{ color: 'var(--haldi)', fontSize: 9, lineHeight: '1.2' }}
        >
          <div style={{ fontSize: 11, fontWeight: 700 }}>{activeSteps}</div>
          <div style={{ opacity: 0.7 }}>steps</div>
        </div>
      )}

      <nav
        aria-label="Narad surfaces"
        style={{
          width: horizontal ? '100%' : 'auto',
          display: 'grid',
          gridTemplateColumns: horizontal ? `repeat(${SURFACE_ITEMS.length}, minmax(0, 1fr))` : '1fr',
          gap: horizontal ? 0 : 5,
          borderTop: horizontal ? 'none' : '1px solid rgba(252,250,242,0.08)',
          paddingTop: horizontal ? 0 : 9,
        }}
      >
        {SURFACE_ITEMS.map(item => {
          const active = item.id === activeSurface
          return (
            <button
              key={item.id}
              type="button"
              aria-current={active ? 'page' : undefined}
              aria-label={item.id === 'activity' && unread > 0 ? `Open ${item.label}, ${unread} unread` : `Open ${item.label}`}
              title={item.label}
              onClick={() => onNavigate(item.id)}
              style={{
                position: 'relative',
                minWidth: horizontal ? 0 : 54,
                minHeight: horizontal ? 54 : 42,
                padding: horizontal ? '6px 2px' : '5px 4px',
                border: 0,
                borderRadius: horizontal ? 0 : 9,
                background: active ? 'rgba(252,250,242,0.12)' : 'transparent',
                color: active ? '#fcfaf2' : 'rgba(252,250,242,0.44)',
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: 'center',
                gap: 2,
                cursor: 'pointer',
                transition: 'background 150ms ease, color 150ms ease',
              }}
            >
              <span style={{ fontSize: 14, lineHeight: 1 }}>{item.icon}</span>
              <span
                style={{
                  fontFamily: 'var(--font-mono)',
                  fontSize: horizontal ? 8 : 7,
                  letterSpacing: '0.07em',
                  textTransform: 'uppercase',
                  whiteSpace: 'nowrap',
                }}
              >
                {item.label}
              </span>
              {horizontal && item.id === 'chat' && activeSteps > 0 && (
                <CountBadge count={activeSteps} tone="var(--sindoor)" horizontal={horizontal} />
              )}
              {item.id === 'activity' && unread > 0 && (
                <CountBadge count={unread} tone="var(--kesari)" horizontal={horizontal} />
              )}
            </button>
          )
        })}
      </nav>
    </div>
  )
}
