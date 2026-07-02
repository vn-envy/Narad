/**
 * Mahati Veena logo — Narad's instrument, drawn in the manuscript stroke language.
 *
 * Anatomy (viewBox 0 0 100 160):
 *   pegbox scroll → top gourd (0.7×) → dandi (stem) → bottom gourd (1.0×) → bridge
 *
 * The four strings run the full length of the dandi — one per avatāra, in
 * canonical order (string 1 = Matsya … string 4 = Parashurama). An active
 * avatar's string takes its canonical colour and plucks (damped lateral
 * vibration). The sindoor bindu in the bottom gourd is Narad himself; it
 * breathes while Narad is orchestrating.
 *
 * All colours come from design tokens, so the mark sits correctly on both
 * the kajal header and paper surfaces (and in lamp-lit night mode).
 */

import type { AvatarState } from '../hooks/useAvatara'
import { AVATAR_COLOURS, AVATAR_NAMES } from '@/lib/avatara-constants'

interface Props {
  avatarStates?: Partial<Record<string, AvatarState>>
  naradActive?: boolean
  size?: number
}

const VB_W = 100
const VB_H = 160

export function MahatiLogo({ avatarStates = {}, naradActive = false, size = 80 }: Props) {
  const W = size
  const H = size * (VB_H / VB_W)  // 1.6 aspect, as before

  const cx = 50

  // Strings: evenly spaced across the dandi, full length pegbox → bridge.
  const stringSpread = 9
  const stringXs = Array.from({ length: AVATAR_NAMES.length }, (_, i) => {
    if (AVATAR_NAMES.length === 1) return cx
    return cx - stringSpread / 2 + (i / (AVATAR_NAMES.length - 1)) * stringSpread
  })
  const stringTop = 10
  const stringBottom = 138

  return (
    <svg
      width={W}
      height={H}
      viewBox={`0 0 ${VB_W} ${VB_H}`}
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-label="Mahati veena — Narad"
    >
      {/* Sindoor halo around the resonating gourd */}
      <ellipse
        cx={cx} cy={130}
        rx={27} ry={24}
        fill="none"
        stroke="var(--sindoor)"
        strokeWidth="1"
        strokeDasharray="3 5"
        opacity="0.45"
      />

      {/* Bottom gourd — tumba (pear-shaped resonator) */}
      <path
        d={`M ${cx} 106
            C 66 106, 74 118, 74 131
            C 74 145, 63 152, ${cx} 152
            C 37 152, 26 145, 26 131
            C 26 118, 34 106, ${cx} 106 Z`}
        fill="var(--paper)"
        stroke="var(--kajal)"
        strokeWidth="2.5"
        strokeLinejoin="round"
      />

      {/* Top gourd — smaller resonator (0.7×) */}
      <path
        d={`M ${cx} 16
            C 61 16, 67 24, 67 33
            C 67 43, 59 48, ${cx} 48
            C 41 48, 33 43, 33 33
            C 33 24, 39 16, ${cx} 16 Z`}
        fill="var(--paper)"
        stroke="var(--kajal)"
        strokeWidth="2.2"
        strokeLinejoin="round"
      />

      {/* Dandi — stem joining the gourds */}
      <path
        d={`M ${cx - 6.5} 44 L ${cx - 5.5} 110 L ${cx + 5.5} 110 L ${cx + 6.5} 44 Z`}
        fill="var(--paper)"
        stroke="var(--kajal)"
        strokeWidth="1.5"
        strokeLinejoin="round"
      />

      {/* Pegbox scroll */}
      <path
        d={`M ${cx - 5} 16 C ${cx - 5} 8, ${cx - 2} 4, ${cx + 3} 5 C ${cx + 7} 6, ${cx + 6} 12, ${cx + 2} 12`}
        fill="none"
        stroke="var(--kajal)"
        strokeWidth="2"
        strokeLinecap="round"
      />

      {/* Gourd ornament rings — manuscript detail */}
      <path
        d={`M 32 124 C 38 120, 62 120, 68 124`}
        fill="none" stroke="var(--sindoor)" strokeWidth="1" opacity="0.5"
      />
      <path
        d={`M 38 27 C 43 24.5, 57 24.5, 62 27`}
        fill="none" stroke="var(--sindoor)" strokeWidth="0.9" opacity="0.5"
      />

      {/* Bridge on the bottom gourd */}
      <line
        x1={cx - 8} y1={stringBottom + 2}
        x2={cx + 8} y2={stringBottom + 2}
        stroke="var(--kajal)"
        strokeWidth="2"
        strokeLinecap="round"
      />

      {/* Four strings — one per avatāra, full length */}
      {AVATAR_NAMES.map((name, i) => {
        const state = avatarStates[name]
        const isActive = state === 'active'
        const isDone   = state === 'done'
        const colour = isActive || isDone
          ? AVATAR_COLOURS[name]
          : 'color-mix(in srgb, var(--kajal) 45%, transparent)'
        return (
          <line
            key={name}
            x1={stringXs[i]} y1={stringTop}
            x2={stringXs[i]} y2={stringBottom}
            stroke={colour}
            strokeWidth={isActive ? 2 : 1.1}
            opacity={isDone ? 0.55 : 1}
            style={
              isActive
                ? {
                    filter: `drop-shadow(0 0 3px ${AVATAR_COLOURS[name]})`,
                    animation: 'string-pluck 0.55s ease-out infinite',
                    transformBox: 'fill-box',
                  }
                : undefined
            }
          />
        )
      })}

      {/* Bindu — Narad's presence in the resonating gourd */}
      <circle
        cx={cx}
        cy={130}
        r={5}
        fill="var(--sindoor)"
        opacity={naradActive ? 1 : 0.7}
        style={
          naradActive
            ? { animation: 'breath 1.4s ease-in-out infinite', transformBox: 'fill-box', transformOrigin: 'center' }
            : undefined
        }
      />
    </svg>
  )
}
