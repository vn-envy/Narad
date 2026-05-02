/**
 * Mahati Veena logo — geometric reduction per the design brief.
 *
 * Proportions (from brief):
 *   Top gourd : bottom gourd diameter = 0.7 : 1.0
 *   Stem width : bottom gourd diameter = 0.25 : 1.0
 *   Stem height : total = 0.40
 *   Top gourd height : total = 0.28
 *   Bottom gourd height : total = 0.32
 *
 * Each of the 7 strings maps to one avatar. When that avatar is active,
 * the string picks up the avatar's canonical colour briefly.
 */

import type { AvatarName, AvatarState } from '../hooks/useAvatara'

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

interface Props {
  avatarStates?: Partial<Record<AvatarName, AvatarState>>
  naradActive?: boolean
  size?: number
}

export function MahatiLogo({ avatarStates = {}, naradActive = false, size = 80 }: Props) {
  const W = size
  const H = size * 1.6   // aspect ratio from brief proportions

  // Proportions
  const bottomR  = (W * 0.5)             // bottom gourd radius
  const topR     = bottomR * 0.7         // top gourd = 70% of bottom
  const stemW    = bottomR * 0.5         // stem width
  const bottomCY = H - bottomR           // bottom gourd centre Y
  const topCY    = topR                  // top gourd centre Y
  const stemTop  = topCY + topR
  const stemBot  = bottomCY - bottomR
  const cx       = W / 2

  // 7 strings evenly spaced across stem width
  const stringXs = Array.from({ length: 7 }, (_, i) => {
    const left  = cx - stemW / 2 + 2
    const right = cx + stemW / 2 - 2
    return left + (i / 6) * (right - left)
  })

  return (
    <svg
      width={W}
      height={H}
      viewBox={`0 0 ${W} ${H}`}
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
    >
      {/* Bottom gourd — tumba */}
      <ellipse
        cx={cx} cy={bottomCY}
        rx={bottomR - 1} ry={bottomR * 0.85}
        fill="#F5EBD7"
        stroke="#1A1815" strokeWidth="2.5"
      />

      {/* Top gourd — daanda */}
      <ellipse
        cx={cx} cy={topCY}
        rx={topR - 1} ry={topR * 0.85}
        fill="#F5EBD7"
        stroke="#1A1815" strokeWidth="2.5"
      />

      {/* Stem body */}
      <rect
        x={cx - stemW / 2} y={stemTop}
        width={stemW} height={stemBot - stemTop}
        fill="#F5EBD7"
        stroke="#1A1815" strokeWidth="1.5"
      />

      {/* 7 strings */}
      {AVATAR_NAMES.map((name, i) => {
        const state = avatarStates[name]
        const isActive = state === 'active'
        const isDone   = state === 'done'
        const colour   = isActive || isDone ? AVATAR_COLOURS[name] : '#1E2A5E'
        const opacity  = isDone ? 0.5 : 1
        return (
          <line
            key={name}
            x1={stringXs[i]} y1={stemTop + 2}
            x2={stringXs[i]} y2={stemBot - 2}
            stroke={colour}
            strokeWidth={isActive ? 2 : 1.2}
            opacity={opacity}
            style={isActive ? { filter: `drop-shadow(0 0 3px ${colour})` } : undefined}
          >
            {isActive && (
              <animate
                attributeName="strokeWidth"
                values="1.2;2.5;1.2"
                dur="0.6s"
                repeatCount="indefinite"
              />
            )}
          </line>
        )
      })}

      {/* Bindu — Narad's presence in the bottom gourd */}
      <circle
        cx={cx}
        cy={bottomCY}
        r={bottomR * 0.1}
        fill="#F28E1C"
        opacity={naradActive ? 1 : 0.7}
      >
        {naradActive && (
          <animate
            attributeName="r"
            values={`${bottomR * 0.08};${bottomR * 0.14};${bottomR * 0.08}`}
            dur="0.9s"
            repeatCount="indefinite"
          />
        )}
      </circle>

      {/* Kesari gourd halo accent */}
      <ellipse
        cx={cx} cy={bottomCY}
        rx={bottomR + 4} ry={bottomR * 0.85 + 3}
        fill="none"
        stroke="#E55A1F"
        strokeWidth="1"
        strokeDasharray="4 6"
        opacity="0.5"
      />
    </svg>
  )
}
