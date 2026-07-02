import { motion } from 'motion/react'

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
        fill="#065f46"
        stroke="#2d2a26"
        strokeWidth="0.65"
        strokeLinejoin="round"
      />
      <path d="M -2 -2 L 1 0 L -2 2 Z" fill="#c2410c" stroke="#2d2a26" strokeWidth="0.45" />
      <circle cx="11.5" cy="-0.6" r="0.8" fill="#fcd34d" />
    </g>
  )
}

function LotusDiamond({ x, y }: { x: number; y: number }) {
  return (
    <g transform={`translate(${x}, ${y})`}>
      <rect x="-6.5" y="-4.5" width="13" height="9" rx="1.2" fill="none" stroke="#c2410c" strokeWidth="0.7" />
      <path d="M 0 -3.2 L 3.1 0 L 0 3.2 L -3.1 0 Z" fill="#fcd34d" stroke="#2d2a26" strokeWidth="0.6" />
      <path d="M -5.4 0 H 5.4" stroke="#2d2a26" strokeWidth="0.45" opacity="0.5" />
    </g>
  )
}

function PeacockGlyph({ x, y }: { x: number; y: number }) {
  return (
    <g transform={`translate(${x}, ${y})`}>
      <path
        d="M -5.2 2.2 C -6.4 -1.5, -2.2 -4.2, 2.6 -3.6 C 4.8 -1.8, 4.8 1.7, 2.6 3.2 C -1.4 3.7, -3.3 2.8, -5.2 2.2 Z"
        fill="#065f46"
        stroke="#2d2a26"
        strokeWidth="0.65"
      />
      <circle cx="-1.5" cy="-1.1" r="0.8" fill="#fcd34d" />
      <path d="M 2.4 -0.5 C 5.5 -2.3, 7.7 -2.0, 10.2 -0.3" fill="none" stroke="#c2410c" strokeWidth="0.7" strokeDasharray="1.1 1.1" />
      <path d="M 2.4 1.3 C 5.5 -0.5, 7.7 -0.3, 10.2 1.3" fill="none" stroke="#c2410c" strokeWidth="0.7" strokeDasharray="1.1 1.1" />
    </g>
  )
}

function SunTriangle({ x, y }: { x: number; y: number }) {
  return (
    <g transform={`translate(${x}, ${y})`}>
      <path d="M -5.2 0 L 0 -3.7 L 5.2 0 L 0 3.7 Z" fill="#2d2a26" opacity="0.92" />
      <circle cx="0" cy="0" r="1.25" fill="#c2410c" />
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
      <line x1="0" y1={top} x2="220" y2={top} stroke="#2d2a26" strokeWidth="0.95" />
      <line x1="0" y1={innerTop} x2="220" y2={innerTop} stroke="#2d2a26" strokeWidth="0.35" opacity="0.75" />
      <line x1="0" y1={innerBottom} x2="220" y2={innerBottom} stroke="#2d2a26" strokeWidth="0.35" opacity="0.75" />
      <line x1="0" y1={bottom} x2="220" y2={bottom} stroke="#2d2a26" strokeWidth="0.95" />

      <Fish x={20} y={mid} scale={0.92} />
      <LotusDiamond x={69} y={mid} />
      <PeacockGlyph x={116} y={mid} />
      <SunTriangle x={170} y={mid} />
      <Fish x={197} y={mid} scale={0.72} />

      <circle cx="47" cy={mid - 2.8} r="0.8" fill="#c2410c" opacity="0.55" />
      <circle cx="47" cy={mid + 2.8} r="0.8" fill="#fcd34d" opacity="0.75" />
      <circle cx="144" cy={mid - 2.8} r="0.8" fill="#fcd34d" opacity="0.75" />
      <circle cx="144" cy={mid + 2.8} r="0.8" fill="#c2410c" opacity="0.55" />
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
        <motion.g
          animate={{ x: [0, -scaledTileWidth] }}
          transition={{ duration: 18, ease: 'linear', repeat: Infinity }}
          transform={`scale(${scale})`}
        >
          <Tile x={0} h={baseHeight} />
          <Tile x={tileWidth} h={baseHeight} />
          <Tile x={tileWidth * 2} h={baseHeight} />
        </motion.g>
      </svg>
    </div>
  )
}
