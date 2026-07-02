import type { AvatarName, AvatarStatus, SessionInfo } from '../hooks/useAvatara'
import { MahatiLogo } from './MahatiLogo'
import { ZigzagBank } from './Motifs'

import { AVATAR_NAMES, AVATAR_COLOURS, DEVA } from '@/lib/avatara-constants'

interface Props {
  avatars: Record<AvatarName, AvatarStatus>
  naradActive: boolean
  streaming: boolean
  currentSession: SessionInfo | null
}

function avatarPoint(index: number, total: number, cx: number, cy: number, r: number) {
  const angle = ((index * 360) / total - 90) * (Math.PI / 180)
  return { x: cx + r * Math.cos(angle), y: cy + r * Math.sin(angle) }
}

export function DarshanPanel({ avatars, naradActive, streaming, currentSession }: Props) {
  const SVG_W = 300
  const SVG_H = 280
  const cx = SVG_W / 2
  const cy = SVG_H / 2
  const R  = 102

  const avatarStates = Object.fromEntries(
    AVATAR_NAMES.map(n => [n, avatars[n]?.state ?? 'idle'])
  ) as Record<AvatarName, AvatarStatus['state']>

  const activeCount = AVATAR_NAMES.filter(n => avatars[n]?.state === 'active').length
  const doneCount   = AVATAR_NAMES.filter(n => avatars[n]?.state === 'done').length
  const idleCount   = AVATAR_NAMES.length - activeCount - doneCount
  const sessionSummary = currentSession?.avatarsFired?.length
    ? currentSession.avatarsFired.join(' → ')
    : 'Awaiting a routed turn'
  const stats = [
    {
      label: 'Active',
      value: String(activeCount),
      hint: streaming ? 'avatars currently running' : 'avatars active right now',
      accent: 'var(--marigold)',
    },
    {
      label: 'Completed',
      value: String(doneCount),
      hint: idleCount > 0 ? `${idleCount} idle avatars` : 'all active routes completed',
      accent: 'var(--tulsi)',
    },
    {
      label: 'Tokens',
      value: currentSession?.totalTokens ? currentSession.totalTokens.toLocaleString() : '—',
      hint: currentSession?.promptTokens != null && currentSession?.completionTokens != null
        ? `${currentSession.promptTokens.toLocaleString()} prompt · ${currentSession.completionTokens.toLocaleString()} completion`
        : 'current turn token footprint',
      accent: 'var(--kajal)',
    },
    {
      label: 'Throughput',
      value: currentSession?.tokPerSec ? `${currentSession.tokPerSec}` : '—',
      hint: currentSession?.tokPerSec ? 'tokens per second' : (currentSession?.totalMs ? `${(currentSession.totalMs / 1000).toFixed(1)}s total` : 'awaiting synthesis'),
      accent: 'var(--mor)',
    },
  ]

  return (
    <div className="flex flex-col h-full min-h-0 overflow-y-auto overflow-x-hidden" style={{ background: 'var(--paper)', borderBottom: '1px solid color-mix(in srgb, var(--kajal) 10%, transparent)' }}>

      {/* Header — dark kajal with Playfair italic */}
      <div
        className="flex items-center gap-2 px-4 py-2.5 flex-shrink-0 relative overflow-hidden"
        style={{ background: 'var(--kajal)', minHeight: 44 }}
      >
        <span
          className="label-hero text-[16px] leading-none"
          style={{ color: 'var(--paper)' }}
        >
          दर्शन  DARSHAN
        </span>
        {streaming && (
          <span
            className="ml-auto font-mono text-[10px] px-2 py-0.5 rounded organic-border"
            style={{
              color: 'var(--marigold)',
              background: 'rgba(194,65,12,0.15)',
              borderColor: 'rgba(194,65,12,0.40)',
              animation: 'pulse 1.5s ease-in-out infinite',
            }}
            >
            ● LIVE
          </span>
        )}
        {/* Zigzag motif bottom edge */}
        <div className="absolute bottom-0 left-0 w-full overflow-hidden" style={{ height: 12, opacity: 0.15 }}>
          <ZigzagBank color="var(--paper)" className="w-full" />
        </div>
      </div>

      <div style={{ padding: 16, display: 'grid', gap: 16 }}>
        <div style={{ display: 'grid', gap: 12 }} className="sm:grid-cols-2 xl:grid-cols-4">
          {stats.map(stat => (
            <div
              key={stat.label}
              style={{
                padding: '14px 16px',
                borderRadius: 18,
                border: '1px solid rgba(26,24,21,0.08)',
                background: `linear-gradient(145deg, color-mix(in srgb, ${stat.accent} 10%, var(--paper)) 0%, rgba(252,250,242,0.94) 100%)`,
              }}
            >
              <div style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.16em', color: 'rgba(26,24,21,0.42)' }}>
                {stat.label}
              </div>
              <div style={{ marginTop: 6, fontSize: 26, fontWeight: 700, color: stat.accent, fontFamily: 'var(--font-hero)' }}>
                {stat.value}
              </div>
              <div style={{ marginTop: 4, fontSize: 12, lineHeight: 1.5, color: 'rgba(26,24,21,0.56)' }}>
                {stat.hint}
              </div>
            </div>
          ))}
        </div>

        <div style={{ display: 'grid', gap: 16 }} className="xl:grid-cols-[1.05fr_0.95fr]">
          <section
            style={{
              padding: 18,
              borderRadius: 22,
              border: '1px solid rgba(26,24,21,0.08)',
              background: 'rgba(252,250,242,0.9)',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' }}>
              <div style={{ fontSize: 18, fontWeight: 700, color: 'var(--kajal)', fontFamily: 'var(--font-hero)' }}>
                Live weave
              </div>
              <div style={{ fontSize: 12, color: 'rgba(26,24,21,0.5)' }}>
                {streaming ? 'routing and synthesis in motion' : 'latest routed turn'}
              </div>
            </div>

            <div style={{ display: 'flex', justifyContent: 'center', marginTop: 12 }}>
              <svg width={SVG_W} height={SVG_H} viewBox={`0 0 ${SVG_W} ${SVG_H}`}>
                <defs>
                  <pattern id="madhubani-hatch-darshan" width="4" height="4" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
                    <line x1="0" y1="0" x2="0" y2="4" stroke="#c2410c" strokeWidth="0.5" />
                  </pattern>
                </defs>

                {Array.from({ length: 16 }, (_, i) => {
                  const row = Math.floor(i / 4)
                  const col = i % 4
                  return (
                    <circle
                      key={i}
                      cx={SVG_W - 14 - col * 9}
                      cy={14 + row * 9}
                      r={1.2}
                      fill="#c2410c"
                      opacity={0.15 + (i % 3) * 0.05}
                    />
                  )
                })}

                {AVATAR_NAMES.map((name, i) => {
                  const pt = avatarPoint(i, AVATAR_NAMES.length, cx, cy, R)
                  const state = avatars[name]?.state ?? 'idle'
                  const isActive = state === 'active'
                  const isDone = state === 'done'
                  const colour = isActive
                    ? `${AVATAR_COLOURS[name]}cc`
                    : isDone
                    ? `${AVATAR_COLOURS[name]}60`
                    : 'rgba(45,42,38,0.08)'
                  return (
                    <line
                      key={name}
                      x1={cx}
                      y1={cy}
                      x2={pt.x}
                      y2={pt.y}
                      stroke={colour}
                      strokeWidth={isActive ? 1.5 : 1}
                      strokeDasharray={isActive ? '4 3' : undefined}
                      opacity={1}
                    >
                      {isActive && (
                        <animate
                          attributeName="strokeDashoffset"
                          values="14;0"
                          dur="0.4s"
                          repeatCount="indefinite"
                        />
                      )}
                    </line>
                  )
                })}

                {AVATAR_NAMES.map((name, i) => {
                  const pt = avatarPoint(i, AVATAR_NAMES.length, cx, cy, R)
                  const state = avatars[name]?.state ?? 'idle'
                  const isActive = state === 'active'
                  const isDone = state === 'done'
                  const colour = AVATAR_COLOURS[name]
                  const nodeR = isActive ? 21 : 17
                  const nodeFill = isActive
                    ? 'url(#madhubani-hatch-darshan)'
                    : isDone
                    ? `${colour}22`
                    : 'var(--paper)'

                  return (
                    <g key={name} transform={`translate(${pt.x},${pt.y})`}>
                      {isActive && (
                        <circle r={29} fill={colour} opacity={0.08}>
                          <animate attributeName="r" values="22;31;22" dur="1.2s" repeatCount="indefinite" />
                          <animate attributeName="opacity" values="0.10;0.04;0.10" dur="1.2s" repeatCount="indefinite" />
                        </circle>
                      )}

                      <circle
                        r={nodeR}
                        fill={nodeFill}
                        stroke={colour}
                        strokeWidth="2"
                        strokeOpacity={isActive || isDone ? 0.85 : 0.3}
                      />

                      <text
                        textAnchor="middle"
                        dy={isDone && (avatars[name]?.latencyMs ?? 0) > 0 ? 2 : 4}
                        fontSize={isDone && (avatars[name]?.latencyMs ?? 0) > 0 ? 7 : 8}
                        fontFamily="Inter, sans-serif"
                        fontWeight="600"
                        fill={isActive || isDone ? colour : `${colour}90`}
                      >
                        {name.slice(0, 5)}
                      </text>

                      {isDone && avatars[name]?.latencyMs && (
                        <text textAnchor="middle" dy={14} fontSize={7} fontFamily="JetBrains Mono, monospace" fill={colour} opacity={0.70}>
                          {(avatars[name].latencyMs! / 1000).toFixed(1)}s
                        </text>
                      )}

                      <text textAnchor="middle" dy={nodeR + 14} fontSize={9} fontFamily="Tiro Devanagari Sanskrit, serif" fill={colour} opacity={0.75}>
                        {DEVA[name]}
                      </text>
                    </g>
                  )
                })}

                <foreignObject x={cx - 30} y={cy - 36} width={60} height={72}>
                  <div style={{ display: 'flex', justifyContent: 'center' }}>
                    <MahatiLogo avatarStates={avatarStates} naradActive={naradActive} size={46} />
                  </div>
                </foreignObject>
              </svg>
            </div>

            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 12 }}>
              <span
                className="text-chip px-2.5 py-1 rounded organic-border"
                style={{ background: streaming ? 'var(--marigold)' : 'var(--kajal)', color: 'var(--paper)' }}
              >
                {streaming
                  ? (activeCount > 0 ? `${activeCount} avatar${activeCount > 1 ? 's' : ''} running` : 'Narad routing')
                  : sessionSummary}
              </span>
              {currentSession?.totalMs && (
                <span
                  className="text-chip px-2.5 py-1 rounded organic-border"
                  style={{ background: 'var(--speckle)', color: 'var(--kajal)', borderColor: 'color-mix(in srgb, var(--kajal) 15%, transparent)' }}
                >
                  {(currentSession.totalMs / 1000).toFixed(1)}s total
                </span>
              )}
              {currentSession?.tokPerSec != null && currentSession.tokPerSec > 0 && (
                <span
                  className="text-chip px-2.5 py-1 rounded organic-border font-mono"
                  style={{ background: 'var(--speckle)', color: 'var(--kajal)', borderColor: 'color-mix(in srgb, var(--kajal) 15%, transparent)' }}
                >
                  {currentSession.tokPerSec} tok/s
                </span>
              )}
            </div>
          </section>

          <section
            style={{
              padding: 18,
              borderRadius: 22,
              border: '1px solid rgba(26,24,21,0.08)',
              background: 'rgba(252,250,242,0.9)',
            }}
          >
            <div style={{ fontSize: 18, fontWeight: 700, color: 'var(--kajal)', fontFamily: 'var(--font-hero)' }}>
              Current weave
            </div>
            <div style={{ marginTop: 6, fontSize: 12, lineHeight: 1.5, color: 'rgba(26,24,21,0.55)' }}>
              Session continuity, avatar status, and the latest routed path stay readable here without needing the trace pane.
            </div>

            <div style={{ marginTop: 14, padding: '12px 14px', borderRadius: 14, background: 'rgba(26,24,21,0.03)', border: '1px solid rgba(26,24,21,0.06)' }}>
              <div style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.16em', color: 'rgba(26,24,21,0.42)' }}>
                Session focus
              </div>
              <div style={{ marginTop: 8, fontSize: 13.5, lineHeight: 1.6, color: 'var(--kajal)' }}>
                {sessionSummary}
              </div>
              {currentSession?.sessionId && (
                <div style={{ marginTop: 8, fontSize: 10.5, color: 'rgba(26,24,21,0.42)', fontFamily: 'var(--font-mono)' }}>
                  {currentSession.sessionId}
                </div>
              )}
            </div>

            <div style={{ display: 'grid', gap: 10, marginTop: 14 }}>
              {AVATAR_NAMES.map(name => {
                const status = avatars[name]
                const state = status?.state ?? 'idle'
                const accent = AVATAR_COLOURS[name]
                return (
                  <div
                    key={name}
                    style={{
                      padding: '12px 14px',
                      borderRadius: 14,
                      border: '1px solid rgba(26,24,21,0.06)',
                      background: state === 'active' ? `${accent}12` : 'rgba(26,24,21,0.03)',
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                      <span style={{ fontSize: 12.5, fontWeight: 700, color: accent }}>
                        {DEVA[name]} {name}
                      </span>
                      <span
                        style={{
                          padding: '2px 7px',
                          borderRadius: 999,
                          background: state === 'active' ? `${accent}18` : 'rgba(26,24,21,0.05)',
                          color: state === 'active' ? accent : 'rgba(26,24,21,0.5)',
                          fontSize: 10,
                          fontWeight: 700,
                          textTransform: 'uppercase',
                        }}
                      >
                        {state}
                      </span>
                      {status?.discipline && (
                        <span style={{ fontSize: 10.5, color: 'rgba(26,24,21,0.46)' }}>
                          {status.discipline}
                        </span>
                      )}
                      {status?.latencyMs && (
                        <span style={{ marginLeft: 'auto', fontSize: 10.5, color: 'rgba(26,24,21,0.42)', fontFamily: 'var(--font-mono)' }}>
                          {(status.latencyMs / 1000).toFixed(1)}s
                        </span>
                      )}
                    </div>
                    {status?.task && (
                      <div style={{ marginTop: 8, fontSize: 12, lineHeight: 1.55, color: 'rgba(26,24,21,0.58)' }}>
                        {status.task.slice(0, 140)}{status.task.length > 140 ? '…' : ''}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          </section>
        </div>
      </div>
    </div>
  )
}
