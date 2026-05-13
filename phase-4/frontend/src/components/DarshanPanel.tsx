import type { AvatarName, AvatarStatus, SessionInfo } from '../hooks/useAvatara'
import { MahatiLogo } from './MahatiLogo'
import { KarmaSheet } from './KarmaSheet'
import { ZigzagBank } from './Motifs'
import { cn } from '@/lib/utils'

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

const DEVA: Record<AvatarName, string> = {
  Matsya:      'मत्स्य',
  Varaha:      'वराह',
  Narasimha:   'नरसिंह',
  Rama:        'राम',
  Krishna:     'कृष्ण',
  Buddha:      'बुद्ध',
  Parashurama: 'परशुराम',
  Vamana:      'वामन',
}

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
  const SVG_W = 340
  const SVG_H = 320
  const cx = SVG_W / 2
  const cy = SVG_H / 2
  const R  = 118

  const avatarStates = Object.fromEntries(
    AVATAR_NAMES.map(n => [n, avatars[n]?.state ?? 'idle'])
  ) as Record<AvatarName, AvatarStatus['state']>

  const activeCount = AVATAR_NAMES.filter(n => avatars[n]?.state === 'active').length
  const doneCount   = AVATAR_NAMES.filter(n => avatars[n]?.state === 'done').length

  return (
    <div className="flex flex-col h-full overflow-hidden" style={{ background: 'var(--paper)', borderBottom: '1px solid color-mix(in srgb, var(--kajal) 10%, transparent)' }}>

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
        <div className={cn('flex items-center gap-1.5', streaming ? '' : 'ml-auto')}>
          <KarmaSheet />
        </div>
        {/* Zigzag motif bottom edge */}
        <div className="absolute bottom-0 left-0 w-full overflow-hidden" style={{ height: 12, opacity: 0.15 }}>
          <ZigzagBank color="var(--paper)" className="w-full" />
        </div>
      </div>

      {/* Avatar graph — warm paper SVG */}
      <div className="flex-1 flex items-center justify-center p-2" style={{ background: 'var(--paper)' }}>
        <svg width={SVG_W} height={SVG_H} viewBox={`0 0 ${SVG_W} ${SVG_H}`}>
          <defs>
            <pattern id="madhubani-hatch-darshan" width="4" height="4" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
              <line x1="0" y1="0" x2="0" y2="4" stroke="#c2410c" strokeWidth="0.5" />
            </pattern>
          </defs>

          {/* HalftoneCorner — top-right decorative motif */}
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

          {/* Connecting lines */}
          {AVATAR_NAMES.map((name, i) => {
            const pt = avatarPoint(i, AVATAR_NAMES.length, cx, cy, R)
            const state = avatars[name]?.state ?? 'idle'
            const isActive = state === 'active'
            const isDone   = state === 'done'
            const colour   = isActive
              ? `${AVATAR_COLOURS[name]}cc`
              : isDone
              ? `${AVATAR_COLOURS[name]}60`
              : 'rgba(45,42,38,0.08)'
            return (
              <line
                key={name}
                x1={cx} y1={cy}
                x2={pt.x} y2={pt.y}
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

          {/* Avatar nodes */}
          {AVATAR_NAMES.map((name, i) => {
            const pt    = avatarPoint(i, AVATAR_NAMES.length, cx, cy, R)
            const state = avatars[name]?.state ?? 'idle'
            const isActive = state === 'active'
            const isDone   = state === 'done'
            const colour   = AVATAR_COLOURS[name]
            const nodeR    = isActive ? 22 : 18

            const nodeFill = isActive
              ? 'url(#madhubani-hatch-darshan)'
              : isDone
              ? `${colour}22`
              : 'var(--paper)'

            return (
              <g key={name} transform={`translate(${pt.x},${pt.y})`}>
                {isActive && (
                  <circle r={30} fill={colour} opacity={0.08}>
                    <animate attributeName="r" values="22;32;22" dur="1.2s" repeatCount="indefinite" />
                    <animate attributeName="opacity" values="0.10;0.04;0.10" dur="1.2s" repeatCount="indefinite" />
                  </circle>
                )}

                <circle
                  r={nodeR}
                  fill={nodeFill}
                  stroke={colour}
                  strokeWidth="2"
                  strokeOpacity={isActive || isDone ? 0.85 : 0.30}
                />

                <text
                  textAnchor="middle"
                  dy={isActive || isDone ? 4 : 5}
                  fontSize={isDone && (avatars[name]?.latencyMs ?? 0) > 0 ? 7 : 8}
                  fontFamily="Inter, sans-serif"
                  fontWeight="600"
                  fill={isActive || isDone ? colour : `${colour}90`}
                >
                  {name.slice(0, 5)}
                </text>

                {isDone && avatars[name]?.latencyMs && (
                  <text textAnchor="middle" dy={15} fontSize={7} fontFamily="JetBrains Mono, monospace" fill={colour} opacity={0.70}>
                    {(avatars[name].latencyMs! / 1000).toFixed(1)}s
                  </text>
                )}

                <text textAnchor="middle" dy={nodeR + 14} fontSize={9} fontFamily="Tiro Devanagari Sanskrit, serif" fill={colour} opacity={0.75}>
                  {DEVA[name]}
                </text>
              </g>
            )
          })}

          {/* Centre — Narad */}
          <foreignObject x={cx - 30} y={cy - 40} width={60} height={80}>
            <div style={{ display: 'flex', justifyContent: 'center' }}>
              <MahatiLogo avatarStates={avatarStates} naradActive={naradActive} size={48} />
            </div>
          </foreignObject>
        </svg>
      </div>

      {/* Session stats footer */}
      <div
        className="flex flex-wrap gap-1.5 px-4 py-2 min-h-[38px] items-center flex-shrink-0"
        style={{
          background: 'var(--speckle)',
          borderTop: '1px solid color-mix(in srgb, var(--kajal) 10%, transparent)',
        }}
      >
        {streaming ? (
          <span
            className="text-chip px-2.5 py-1 rounded organic-border"
            style={{ background: 'var(--marigold)', color: 'var(--paper)' }}
          >
            {activeCount > 0
              ? `${activeCount} avatar${activeCount > 1 ? 's' : ''} running…`
              : naradActive
              ? 'Narad routing…'
              : `${doneCount} done`}
          </span>
        ) : currentSession ? (
          <>
            <span
              className="text-chip px-2.5 py-1 rounded organic-border"
              style={{ background: 'var(--kajal)', color: 'var(--paper)' }}
            >
              {currentSession.avatarsFired.join(' → ')}
            </span>
            {currentSession.totalMs && (
              <span
                className="text-chip px-2.5 py-1 rounded organic-border"
                style={{ background: 'var(--speckle)', color: 'var(--kajal)', borderColor: 'color-mix(in srgb, var(--kajal) 15%, transparent)' }}
              >
                {(currentSession.totalMs / 1000).toFixed(1)}s
              </span>
            )}
            {currentSession.totalTokens != null && currentSession.totalTokens > 0 && (
              <span
                className="text-chip px-2.5 py-1 rounded organic-border font-mono"
                style={{ background: 'var(--speckle)', color: 'var(--kajal)', borderColor: 'color-mix(in srgb, var(--kajal) 15%, transparent)' }}
                title={`Prompt: ${currentSession.promptTokens?.toLocaleString() ?? '?'} · Completion: ${currentSession.completionTokens?.toLocaleString() ?? '?'}`}
              >
                {currentSession.totalTokens.toLocaleString()} tok
              </span>
            )}
            {currentSession.tokPerSec != null && currentSession.tokPerSec > 0 && (
              <span
                className="text-chip px-2.5 py-1 rounded organic-border font-mono"
                style={{ background: 'var(--speckle)', color: 'var(--kajal)', borderColor: 'color-mix(in srgb, var(--kajal) 15%, transparent)' }}
              >
                {currentSession.tokPerSec} tok/s
              </span>
            )}
          </>
        ) : (
          <span
            className="text-chip px-2.5 py-1 rounded organic-border opacity-40"
            style={{ background: 'var(--paper)', color: 'var(--kajal)' }}
          >
            Awaiting query…
          </span>
        )}
      </div>
    </div>
  )
}
