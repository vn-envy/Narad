/**
 * The avatāras as the first Narad design drew them: one thick line in the
 * avatar's colour, drawing itself back and forth while that avatar works, with
 * one small secondary motion each (the fish's bubble trail, Rama's arrow
 * nocking, Krishna's notes rising, the axe handle swinging in). At rest (in
 * lists, or motion off) every line is complete and still.
 */
import type { CSSProperties } from 'react'

const F = { fill: 'currentColor' } as const

function glyph(name: string) {
  switch (name.toLowerCase()) {
    case 'matsya':
      return (
        <>
          <path d="M20 50C20 20 80 20 80 50C80 80 20 80 20 50Z" className="av-l av-draw av-d4" pathLength={1} />
          <path d="M20 50L5 35M20 50L5 65L5 35" className="av-l" style={{ fill: 'currentColor', fillOpacity: 0.2 }} />
          <circle cx="70" cy="45" r="4" {...F} />
          <path d="M40 35C50 45 40 65 50 70" className="av-l" style={{ strokeDasharray: '2 4' }} />
          <path d="M30 70C40 85 30 95 40 100" className="av-l av-flow" pathLength={1} style={{ strokeWidth: 1, opacity: 0.5 }} />
        </>
      )
    case 'rama':
      return (
        <>
          <path d="M30 10C80 10 80 90 30 90" className="av-l av-draw av-d3" pathLength={1} />
          <path d="M30 10L30 90" className="av-l" style={{ strokeWidth: 1, strokeDasharray: '2 2' }} />
          <g className="av-arrow">
            <path d="M10 50L90 50M80 40L90 50L80 60" className="av-l" />
            <path d="M10 45L20 50L10 55Z" {...F} />
          </g>
          <path d="M50 30C55 35 60 30 55 25M50 70C55 65 60 70 55 75" className="av-l" style={{ strokeWidth: 1 }} />
        </>
      )
    case 'krishna':
      return (
        <>
          <path d="M20 80L80 20" className="av-l av-draw av-d2" pathLength={1} style={{ strokeWidth: 6 }} />
          <circle cx="65" cy="35" r="2" className="av-note" {...F} />
          <circle cx="55" cy="45" r="2" className="av-note av-n2" {...F} />
          <circle cx="45" cy="55" r="2" className="av-note av-n3" {...F} />
          <path d="M20 40C30 10 60 5 80 20C50 30 40 60 20 40Z" className="av-l av-feather" style={{ strokeWidth: 1.5, fill: 'currentColor', fillOpacity: 0.1, transformOrigin: '20px 40px' }} />
          <circle cx="45" cy="25" r="6" className="av-l av-jewel" style={{ strokeWidth: 2, transformOrigin: '45px 25px' }} />
          <circle cx="45" cy="25" r="2" {...F} />
        </>
      )
    case 'parashurama':
      return (
        <>
          <path d="M40 90L60 10" className="av-l av-draw av-d15" pathLength={1} style={{ strokeWidth: 8 }} />
          <path d="M55 30L75 25C80 50 60 60 45 70C35 50 40 35 55 30Z" className="av-l" style={{ fill: 'currentColor', fillOpacity: 0.1 }} />
          <path d="M72 25L75 15M48 65L40 75" className="av-l" />
          <circle cx="65" cy="40" r="2" {...F} />
          <path d="M68 35C72 38 70 42 68 45" className="av-l" style={{ strokeWidth: 1 }} />
        </>
      )
    default: // Narad's own small veena
      return (
        <>
          <circle cx="50" cy="14" r="8" className="av-l" />
          <path d="M44 22H56V60H44Z" className="av-l" />
          <path d="M41 32H59M41 42H59M41 52H59" className="av-l" style={{ strokeWidth: 1.5, strokeDasharray: '2 2' }} />
          <circle cx="50" cy="76" r="17" className="av-l av-draw av-d3" pathLength={1} style={{ fill: 'currentColor', fillOpacity: 0.12 }} />
          <circle cx="50" cy="76" r="3.5" {...F} />
        </>
      )
  }
}

interface Props {
  name: string
  size?: number
  /** True while this avatar works: the line draws itself. */
  live?: boolean
  className?: string
  style?: CSSProperties
}

export function AvatarGlyph({ name, size = 40, live = false, className, style }: Props) {
  const key = name.toLowerCase()
  const colour = key === 'narad' ? 'var(--avatar-narad)' : `var(--avatar-${key})`
  return (
    <svg
      viewBox="0 0 100 100"
      width={size}
      height={size}
      aria-hidden="true"
      className={[live ? '' : 'av-still', className].filter(Boolean).join(' ')}
      style={{ display: 'block', flex: 'none', overflow: 'visible', color: colour, ...style }}
    >
      {glyph(key)}
    </svg>
  )
}
