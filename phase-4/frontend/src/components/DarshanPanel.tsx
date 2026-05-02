/**
 * Darshan Panel — the live call graph.
 *
 * 7 avatar nodes at heptagonal positions (360/7 ≈ 51.43° apart).
 * Narad at the centre. Lines radiate out.
 * Active nodes light up with canonical colours. Done nodes show latency.
 *
 * Rule from visual-system.md:
 *   Darshan view = full saturated treatment, the wow-moment users show off.
 */

import type { AvatarName, AvatarStatus, SessionInfo } from '../hooks/useAvatara'
import { MahatiLogo } from './MahatiLogo'

const AVATAR_NAMES: AvatarName[] = [
  'Matsya', 'Varaha', 'Narasimha', 'Rama', 'Krishna', 'Buddha', 'Parashurama'
]

const AVATAR_COLOURS: Record<AvatarName, string> = {
  Matsya:      '#1E2A5E',
  Varaha:      '#E55A1F',
  Narasimha:   '#C0392B',
  Rama:        '#2E7D4F',
  Krishna:     '#1F7A8C',
  Buddha:      '#F2C14E',
  Parashurama: '#4A4A4A',
}

// Devanagari avatar names per design brief
const DEVA: Record<AvatarName, string> = {
  Matsya:      'मत्स्य',
  Varaha:      'वराह',
  Narasimha:   'नरसिंह',
  Rama:        'राम',
  Krishna:     'कृष्ण',
  Buddha:      'बुद्ध',
  Parashurama: 'परशुराम',
}

interface Props {
  avatars: Record<AvatarName, AvatarStatus>
  naradActive: boolean
  streaming: boolean
  currentSession: SessionInfo | null
}

function heptagonPoint(index: number, cx: number, cy: number, r: number) {
  // Start at top (−90°), go clockwise
  const angle = ((index * 360) / 7 - 90) * (Math.PI / 180)
  return { x: cx + r * Math.cos(angle), y: cy + r * Math.sin(angle) }
}

export function DarshanPanel({ avatars, naradActive, streaming, currentSession }: Props) {
  const SVG_W = 340
  const SVG_H = 340
  const cx = SVG_W / 2
  const cy = SVG_H / 2
  const R  = 120   // orbit radius

  const avatarStates = Object.fromEntries(
    AVATAR_NAMES.map(n => [n, avatars[n]?.state ?? 'idle'])
  ) as Record<AvatarName, AvatarStatus['state']>

  const activeCount = AVATAR_NAMES.filter(n => avatars[n]?.state === 'active').length
  const doneCount   = AVATAR_NAMES.filter(n => avatars[n]?.state === 'done').length

  return (
    <div style={styles.panel}>
      {/* Header */}
      <div style={styles.header}>
        <span style={styles.headerTitle}>दर्शन</span>
        <span style={styles.headerSub}>DARSHAN</span>
        {streaming && <span style={styles.liveChip}>● LIVE</span>}
      </div>

      {/* Heptagonal call graph */}
      <div style={styles.graphWrap}>
        <svg width={SVG_W} height={SVG_H} viewBox={`0 0 ${SVG_W} ${SVG_H}`}>
          {/* Halftone dot corner motif */}
          {Array.from({ length: 25 }, (_, i) => {
            const row = Math.floor(i / 5)
            const col = i % 5
            return (
              <circle
                key={i}
                cx={SVG_W - 20 - col * 10}
                cy={20 + row * 10}
                r={1.5}
                fill="#F28E1C"
                opacity={0.3 + (i % 3) * 0.1}
              />
            )
          })}

          {/* Connecting lines — Narad to each avatar */}
          {AVATAR_NAMES.map((name, i) => {
            const pt = heptagonPoint(i, cx, cy, R)
            const state = avatars[name]?.state ?? 'idle'
            const isActive = state === 'active'
            const isDone   = state === 'done'
            const colour   = isActive || isDone ? AVATAR_COLOURS[name] : '#1A181530'
            return (
              <line
                key={name}
                x1={cx} y1={cy}
                x2={pt.x} y2={pt.y}
                stroke={colour}
                strokeWidth={isActive ? 2 : 1}
                strokeDasharray={isActive ? '4 3' : undefined}
                opacity={isDone ? 0.6 : 1}
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
            const pt    = heptagonPoint(i, cx, cy, R)
            const state = avatars[name]?.state ?? 'idle'
            const isActive = state === 'active'
            const isDone   = state === 'done'
            const colour   = AVATAR_COLOURS[name]
            const nodeR    = isActive ? 22 : 18

            return (
              <g key={name} transform={`translate(${pt.x},${pt.y})`}>
                {/* Glow ring when active */}
                {isActive && (
                  <circle r={30} fill={colour} opacity={0.12}>
                    <animate
                      attributeName="r"
                      values="22;32;22"
                      dur="1.2s"
                      repeatCount="indefinite"
                    />
                    <animate
                      attributeName="opacity"
                      values="0.15;0.05;0.15"
                      dur="1.2s"
                      repeatCount="indefinite"
                    />
                  </circle>
                )}

                {/* Node circle */}
                <circle
                  r={nodeR}
                  fill={isActive || isDone ? colour : '#F5EBD7'}
                  stroke={colour}
                  strokeWidth="2.5"
                  opacity={isActive || isDone ? 1 : 0.6}
                />

                {/* Latin name */}
                <text
                  textAnchor="middle"
                  dy={isActive || isDone ? 4 : 5}
                  fontSize={isDone && (avatars[name]?.latencyMs ?? 0) > 0 ? 7 : 8}
                  fontFamily="Inter, sans-serif"
                  fontWeight="600"
                  fill={isActive || isDone ? '#F5EBD7' : colour}
                >
                  {name.slice(0, 5)}
                </text>

                {/* Latency badge */}
                {isDone && avatars[name]?.latencyMs && (
                  <text
                    textAnchor="middle"
                    dy={15}
                    fontSize={7}
                    fontFamily="JetBrains Mono, monospace"
                    fill={colour}
                    opacity={0.85}
                  >
                    {(avatars[name].latencyMs! / 1000).toFixed(1)}s
                  </text>
                )}

                {/* Devanagari sub-label */}
                <text
                  textAnchor="middle"
                  dy={nodeR + 14}
                  fontSize={9}
                  fontFamily="Tiro Devanagari Sanskrit, serif"
                  fill={colour}
                  opacity={0.8}
                >
                  {DEVA[name]}
                </text>
              </g>
            )
          })}

          {/* Centre — Narad with Mahati logo */}
          <foreignObject x={cx - 30} y={cy - 40} width={60} height={80}>
            <div style={{ display: 'flex', justifyContent: 'center' }}>
              <MahatiLogo
                avatarStates={avatarStates}
                naradActive={naradActive}
                size={48}
              />
            </div>
          </foreignObject>
        </svg>
      </div>

      {/* Session stats footer */}
      <div style={styles.statsRow}>
        {streaming ? (
          <span style={styles.statChip}>
            {activeCount > 0
              ? `${activeCount} avatar${activeCount > 1 ? 's' : ''} running…`
              : naradActive
              ? 'Narad routing…'
              : `${doneCount} done`}
          </span>
        ) : currentSession ? (
          <>
            <span style={styles.statChip}>
              {currentSession.avatarsFired.join(' → ')}
            </span>
            {currentSession.totalMs && (
              <span style={{ ...styles.statChip, background: 'var(--kajal)', color: 'var(--paper)' }}>
                {(currentSession.totalMs / 1000).toFixed(1)}s total
              </span>
            )}
          </>
        ) : (
          <span style={{ ...styles.statChip, opacity: 0.4 }}>Awaiting query…</span>
        )}
      </div>
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  panel: {
    display: 'flex',
    flexDirection: 'column',
    height: '100%',
    background: 'var(--paper)',
    borderLeft: '2.5px solid var(--kajal)',
    position: 'relative',
    overflow: 'hidden',
  },
  header: {
    display: 'flex',
    alignItems: 'baseline',
    gap: 8,
    padding: '18px 20px 12px',
    borderBottom: '2px solid var(--kajal)',
    background: 'var(--nila)',
  },
  headerTitle: {
    fontFamily: 'var(--font-deva)',
    fontSize: 22,
    color: 'var(--marigold)',
    lineHeight: 1,
  },
  headerSub: {
    fontFamily: 'var(--font-hero)',
    fontSize: 13,
    color: 'var(--paper)',
    letterSpacing: '0.12em',
    opacity: 0.8,
  },
  liveChip: {
    marginLeft: 'auto',
    fontFamily: 'var(--font-mono)',
    fontSize: 10,
    color: 'var(--sindoor)',
    background: 'rgba(192,57,43,0.15)',
    padding: '2px 8px',
    borderRadius: 20,
    border: '1px solid var(--sindoor)',
    animation: 'pulse 1.5s ease-in-out infinite',
  },
  graphWrap: {
    flex: 1,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    padding: 8,
  },
  statsRow: {
    display: 'flex',
    flexWrap: 'wrap',
    gap: 6,
    padding: '10px 16px',
    borderTop: '2px solid var(--kajal)',
    minHeight: 44,
    alignItems: 'center',
  },
  statChip: {
    fontFamily: 'var(--font-mono)',
    fontSize: 11,
    background: 'var(--marigold)',
    color: 'var(--kajal)',
    padding: '3px 10px',
    borderRadius: 20,
    border: '1.5px solid var(--kajal)',
    fontWeight: 600,
  },
}
