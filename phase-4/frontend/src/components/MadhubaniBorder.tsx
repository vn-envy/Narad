/**
 * Madhubani border band — the four avatāras as folk glyphs, in canonical colour:
 *   fish   = Matsya (samudra lapis)     sun   = Rama (surya gold)
 *   peacock = Krishna (morpankh teal)   axe   = Parashurama (rakta crimson)
 * with lotus + bindu accents in sindoor/haldi (Narad's realm).
 * All colours from design tokens; scroll animation honours reduced-motion
 * via a pure-CSS keyframe (no JS animation library).
 */
import type { CSSProperties } from 'react'

interface MadhubaniBorderProps {
  className?: string
  position?: 'top' | 'bottom'
  height?: number
}

function Fish({ x, y, scale = 1 }: { x: number; y: number; scale?: number }) {
  return (
    <g transform={`translate(${x}, ${y}) scale(${scale})`}>
      <path
        d="M 0 0 C 4 -3, 12 -3, 16 0 C 12 3, 4 3, 0 0 Z"
        fill="var(--avatar-matsya)"
        stroke="var(--kajal)"
        strokeWidth="0.65"
        strokeLinejoin="round"
      />
      <path d="M -2 -2 L 1 0 L -2 2 Z" fill="var(--sindoor)" stroke="var(--kajal)" strokeWidth="0.45" />
      <circle cx="11.5" cy="-0.6" r="0.8" fill="var(--haldi)" />
    </g>
  )
}

function LotusDiamond({ x, y }: { x: number; y: number }) {
  return (
    <g transform={`translate(${x}, ${y})`}>
      <rect x="-6.5" y="-4.5" width="13" height="9" rx="1.2" fill="none" stroke="var(--sindoor)" strokeWidth="0.7" />
      <path d="M 0 -3.2 L 3.1 0 L 0 3.2 L -3.1 0 Z" fill="var(--haldi)" stroke="var(--kajal)" strokeWidth="0.6" />
      <path d="M -5.4 0 H 5.4" stroke="var(--kajal)" strokeWidth="0.45" opacity="0.5" />
    </g>
  )
}

function PeacockGlyph({ x, y }: { x: number; y: number }) {
  return (
    <g transform={`translate(${x}, ${y})`}>
      <path
        d="M -5.2 2.2 C -6.4 -1.5, -2.2 -4.2, 2.6 -3.6 C 4.8 -1.8, 4.8 1.7, 2.6 3.2 C -1.4 3.7, -3.3 2.8, -5.2 2.2 Z"
        fill="var(--avatar-krishna)"
        stroke="var(--kajal)"
        strokeWidth="0.65"
      />
      <circle cx="-1.5" cy="-1.1" r="0.8" fill="var(--haldi)" />
      <path d="M 2.4 -0.5 C 5.5 -2.3, 7.7 -2.0, 10.2 -0.3" fill="none" stroke="var(--sindoor)" strokeWidth="0.7" strokeDasharray="1.1 1.1" />
      <path d="M 2.4 1.3 C 5.5 -0.5, 7.7 -0.3, 10.2 1.3" fill="none" stroke="var(--sindoor)" strokeWidth="0.7" strokeDasharray="1.1 1.1" />
    </g>
  )
}

/** Rama — sun diamond of the solar line. */
function SunGlyph({ x, y }: { x: number; y: number }) {
  return (
    <g transform={`translate(${x}, ${y})`}>
      <path d="M -5.2 0 L 0 -3.7 L 5.2 0 L 0 3.7 Z" fill="var(--avatar-rama)" stroke="var(--kajal)" strokeWidth="0.5" opacity="0.95" />
      <circle cx="0" cy="0" r="1.25" fill="var(--paper)" />
    </g>
  )
}

/** Parashurama — the axe head. */
function AxeGlyph({ x, y }: { x: number; y: number }) {
  return (
    <g transform={`translate(${x}, ${y})`}>
      <line x1="-4.5" y1="3.4" x2="3.2" y2="-3.0" stroke="var(--kajal)" strokeWidth="0.9" strokeLinecap="round" />
      <path
        d="M 1.6 -4.2 C 4.4 -4.8, 6.4 -3.2, 6.2 -0.6 C 4.6 -1.6, 2.8 -2.0, 1.0 -1.8 C 0.8 -2.8, 1.0 -3.7, 1.6 -4.2 Z"
        fill="var(--avatar-parashurama)"
        stroke="var(--kajal)"
        strokeWidth="0.55"
        strokeLinejoin="round"
      />
    </g>
  )
}

function Tile({ x, h }: { x: number; h: number }) {
  const mid = h / 2
  const top = 1.2
  const innerTop = 3.4
  const bottom = h - 1.2
  const innerBottom = h - 3.4

  return (
    <g transform={`translate(${x}, 0)`}>
      <line x1="0" y1={top} x2="220" y2={top} stroke="var(--kajal)" strokeWidth="0.95" />
      <line x1="0" y1={innerTop} x2="220" y2={innerTop} stroke="var(--kajal)" strokeWidth="0.35" opacity="0.75" />
      <line x1="0" y1={innerBottom} x2="220" y2={innerBottom} stroke="var(--kajal)" strokeWidth="0.35" opacity="0.75" />
      <line x1="0" y1={bottom} x2="220" y2={bottom} stroke="var(--kajal)" strokeWidth="0.95" />

      <Fish x={16} y={mid} scale={0.92} />
      <LotusDiamond x={64} y={mid} />
      <PeacockGlyph x={108} y={mid} />
      <SunGlyph x={152} y={mid} />
      <AxeGlyph x={188} y={mid} />

      <circle cx="43" cy={mid - 2.8} r="0.8" fill="var(--sindoor)" opacity="0.55" />
      <circle cx="43" cy={mid + 2.8} r="0.8" fill="var(--haldi)" opacity="0.75" />
      <circle cx="133" cy={mid - 2.8} r="0.8" fill="var(--haldi)" opacity="0.75" />
      <circle cx="133" cy={mid + 2.8} r="0.8" fill="var(--sindoor)" opacity="0.55" />
      <circle cx="207" cy={mid} r="0.8" fill="var(--sindoor)" opacity="0.55" />
    </g>
  )
}

export function MadhubaniBorder({
  className = '',
  position = 'top',
  height = 14,
}: MadhubaniBorderProps) {
  const baseHeight = 14
  const h = Math.max(24, Math.round(height))
  const tileWidth = 220
  const scale = h / baseHeight
  const scaledTileWidth = tileWidth * scale
  const viewportWidth = scaledTileWidth * 2

  return (
    <div
      className={`w-full overflow-hidden bg-paper border-kajal ${position === 'top' ? 'border-b-[2px]' : 'border-t-[2px]'} ${className}`}
      style={{ height: h }}
    >
      <svg
        width="100%"
        height={h}
        viewBox={`0 0 ${viewportWidth} ${h}`}
        preserveAspectRatio="none"
        xmlns="http://www.w3.org/2000/svg"
        style={{ display: 'block' }}
      >
        <style>{`
          @keyframes madhubani-scroll {
            from { transform: translateX(0); }
            to   { transform: translateX(var(--madhubani-shift)); }
          }
          .madhubani-track { animation: madhubani-scroll 18s linear infinite; }
          @media (prefers-reduced-motion: reduce) {
            .madhubani-track { animation: none; }
          }
        `}</style>
        <g
          className="madhubani-track"
          style={{ '--madhubani-shift': `${-scaledTileWidth}px` } as CSSProperties}
        >
          <g transform={`scale(${scale})`}>
            <Tile x={0} h={baseHeight} />
            <Tile x={tileWidth} h={baseHeight} />
            <Tile x={tileWidth * 2} h={baseHeight} />
          </g>
        </g>
      </svg>
    </div>
  )
}
