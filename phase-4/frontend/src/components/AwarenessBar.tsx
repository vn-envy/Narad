/**
 * AwarenessBar — navigation, plus the avatāras' presence on desktop.
 *
 * Desktop: the right-edge rail. Each avatāra appears as its Devanagari initial
 * over its Mahati string position (1–4); active avatars breathe in their
 * canonical colour. Below them, every surface.
 * Phone: a bottom bar with the four places a person goes (Chat, Activity,
 * Paths, You), 58 px tall, above the gesture bar. Memory and System open from
 * You there.
 */
import { useState } from 'react'
import { Bell, Brain, MessageCircle, Route, Settings2, UserRound, type LucideIcon } from 'lucide-react'
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

const ICONS: Record<AppSurface, LucideIcon> = {
  chat: MessageCircle,
  activity: Bell,
  workspaces: Route,
  memory: Brain,
  system: Settings2,
  you: UserRound,
}

function navLabel(label: string, id: AppSurface, unread: number, working: boolean): string {
  if (id === 'activity' && unread > 0) return `${label}, ${unread} unread`
  if (id === 'chat' && working) return `${label}, Narad is working`
  return label
}

function badgeText(count: number): string {
  return count > 99 ? '99+' : String(count)
}

/** The phone's bottom navigation. */
function BottomNav({ activeSurface, onNavigate, unread, working }: {
  activeSurface: AppSurface
  onNavigate: (surface: AppSurface) => void
  unread: number
  working: boolean
}) {
  // Memory and System live under You on a phone.
  const current = activeSurface === 'memory' || activeSurface === 'system' ? 'you' : activeSurface
  return (
    <nav className="bottom-nav" aria-label="Main">
      {SURFACE_ITEMS.filter(item => item.phone).map(item => {
        const Icon = ICONS[item.id]
        const active = item.id === current
        return (
          <button
            key={item.id}
            type="button"
            aria-current={active ? 'page' : undefined}
            aria-label={navLabel(item.label, item.id, unread, working)}
            onClick={() => onNavigate(item.id)}
          >
            <span className="bottom-nav-icon" aria-hidden="true">
              <Icon size={21} strokeWidth={active ? 2.3 : 1.9} />
            </span>
            <span aria-hidden="true">{item.label}</span>
            {item.id === 'activity' && unread > 0 && (
              <span className="nav-badge" aria-hidden="true">{badgeText(unread)}</span>
            )}
            {item.id === 'chat' && working && (
              <span
                className="nav-badge"
                aria-hidden="true"
                style={{ minWidth: 10, width: 10, height: 10, padding: 0, top: 9, animation: 'breath 1.4s ease-in-out infinite' }}
              />
            )}
          </button>
        )
      })}
    </nav>
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

  if (horizontal) {
    return <BottomNav activeSurface={activeSurface} onNavigate={onNavigate} unread={unread} working={activeSteps > 0} />
  }

  return (
    <div
      className="flex flex-col items-center h-full overflow-hidden"
      style={{
        width: 72,
        padding: '10px 8px',
        background: 'var(--chrome)',
        borderLeft: '1px solid rgba(252,250,242,0.06)',
      }}
    >
      {/* Avatar strings */}
      <div className="flex flex-col items-center gap-2.5 flex-1">
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
                  fontSize: 8,
                  marginTop: 2,
                  letterSpacing: '0.08em',
                  color: active ? colour : 'rgba(252,250,242,0.28)',
                }}
              >
                {i + 1}
              </span>

              {/* Tooltip, left of the rail */}
              {hoveredAvatar === name && (
                <div
                  className="absolute right-full mr-2 z-50 px-2.5 py-1.5 rounded whitespace-nowrap pointer-events-none"
                  style={{
                    background: 'var(--chrome)',
                    border: `1px solid rgba(${rgb}, 0.45)`,
                    color: 'rgba(252,250,242,0.9)',
                    top: '50%',
                    transform: 'translateY(-50%)',
                    boxShadow: '0 4px 16px rgba(0,0,0,0.35)',
                  }}
                >
                  <span className="flex items-baseline gap-1.5">
                    <span style={{ fontFamily: 'var(--font-deva)', color: colour, fontSize: 12 }}>{DEVA[name]}</span>
                    <span className="font-semibold text-[11px]">{name}</span>
                    <span className="font-mono text-[9px] opacity-45">string {i + 1}</span>
                  </span>
                  {st?.discipline && (
                    <span className="block text-[10px] opacity-60 mt-0.5">
                      {st.discipline}
                    </span>
                  )}
                  {st?.task && (
                    <span className="block text-[10px] opacity-60 max-w-[160px] truncate">
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
          style={{ color: 'var(--haldi)', fontSize: 10, lineHeight: '1.2' }}
        >
          <div style={{ fontSize: 12, fontWeight: 700 }}>{activeSteps}</div>
          <div style={{ opacity: 0.7 }}>steps</div>
        </div>
      )}

      <nav
        aria-label="Main"
        style={{
          display: 'grid',
          gridTemplateColumns: '1fr',
          gap: 4,
          borderTop: '1px solid rgba(252,250,242,0.08)',
          paddingTop: 9,
        }}
      >
        {SURFACE_ITEMS.map(item => {
          const Icon = ICONS[item.id]
          const active = item.id === activeSurface
          return (
            <button
              key={item.id}
              type="button"
              aria-current={active ? 'page' : undefined}
              aria-label={navLabel(item.label, item.id, unread, activeSteps > 0)}
              title={item.label}
              onClick={() => onNavigate(item.id)}
              style={{
                position: 'relative',
                minWidth: 56,
                minHeight: 46,
                padding: '5px 4px',
                border: 0,
                borderRadius: 9,
                background: active ? 'rgba(252,250,242,0.12)' : 'transparent',
                color: active ? '#fcfaf2' : 'rgba(252,250,242,0.55)',
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: 'center',
                gap: 3,
                cursor: 'pointer',
                transition: 'background 150ms ease, color 150ms ease',
              }}
            >
              <Icon size={16} aria-hidden="true" />
              <span aria-hidden="true" style={{ fontSize: 10, fontWeight: 600, whiteSpace: 'nowrap' }}>
                {item.label}
              </span>
              {item.id === 'activity' && unread > 0 && (
                <span className="nav-badge" aria-hidden="true" style={{ top: 2, left: 'auto', right: 4 }}>{badgeText(unread)}</span>
              )}
            </button>
          )
        })}
      </nav>
    </div>
  )
}
