import { useState, useEffect, useRef } from 'react'
import { useCopilotAction, useCopilotReadable, useCopilotChat } from '@copilotkit/react-core'
import { TextMessage, Role } from '@copilotkit/runtime-client-gql'

interface ConceptNode {
  id: string
  label: string
  parentId?: string
  note: string
}

export function ConceptDiagramArtifact({ topic }: { topic: string }) {
  const [nodes, setNodes] = useState<ConceptNode[]>([])
  const [hovered, setHovered] = useState<string | null>(null)
  const [generating, setGenerating] = useState(true)
  const containerRef = useRef<HTMLDivElement>(null)

  const { appendMessage } = useCopilotChat()

  useCopilotReadable({
    description: 'Concept nodes in the diagram',
    value: nodes,
  })

  useCopilotAction({
    name: 'addConcept',
    description: 'Add a concept node to the diagram. Call 6–8 times to build the map.',
    parameters: [
      { name: 'id',       type: 'string', description: 'Unique short identifier (slug)' },
      { name: 'label',    type: 'string', description: 'Display label for the node' },
      { name: 'parentId', type: 'string', description: 'ID of the parent node (omit for root)', required: false },
      { name: 'note',     type: 'string', description: 'One-sentence explanation shown on hover' },
    ],
    handler: ({ id, label, parentId, note }) => {
      setNodes(prev => [...prev, { id, label, parentId: parentId || undefined, note }])
      setGenerating(false)
    },
  })

  useEffect(() => {
    const timer = setTimeout(() => {
      appendMessage(new TextMessage({
        role: Role.User,
        content: `Build a concept map for: "${topic}". Add a root node (id: "root") and 5–7 child/grandchild nodes using addConcept. Each node needs a one-sentence note. Only call the action, no prose.`,
      }))
    }, 400)
    return () => clearTimeout(timer)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [topic])

  if (generating && nodes.length === 0) {
    return (
      <div className="flex items-center justify-center h-full">
        <p className="font-mono text-[10px]" style={{ color: 'rgba(45,42,38,0.35)' }}>
          building concept map…
        </p>
      </div>
    )
  }

  // Position nodes radially around the root
  const root = nodes.find(n => !n.parentId) ?? nodes[0]
  const children = nodes.filter(n => n.parentId === root?.id)
  const grandchildren = nodes.filter(n => n.parentId && n.parentId !== root?.id)

  const W = 280, H = 200
  const cx = W / 2, cy = H / 2
  const r1 = 70, r2 = 130

  function pos(index: number, total: number, radius: number, offset = 0): { x: number; y: number } {
    const angle = offset + (index / total) * 2 * Math.PI - Math.PI / 2
    return { x: cx + radius * Math.cos(angle), y: cy + radius * Math.sin(angle) }
  }

  const nodePositions: Record<string, { x: number; y: number }> = {}
  if (root) nodePositions[root.id] = { x: cx, y: cy }
  children.forEach((n, i) => { nodePositions[n.id] = pos(i, children.length, r1) })
  grandchildren.forEach((n, i) => {
    const parent = nodes.find(p => p.id === n.parentId)
    const parentPos = parent ? nodePositions[parent.id] : { x: cx, y: cy }
    const angle = Math.atan2(parentPos.y - cy, parentPos.x - cx)
    const spread = 0.4
    const a = angle + spread * (i % 2 === 0 ? 1 : -1) * (Math.floor(i / 2) + 1)
    nodePositions[n.id] = {
      x: Math.max(20, Math.min(W - 20, parentPos.x + r2 * 0.5 * Math.cos(a))),
      y: Math.max(16, Math.min(H - 16, parentPos.y + r2 * 0.5 * Math.sin(a))),
    }
  })

  const edges = nodes.filter(n => n.parentId && nodePositions[n.id] && nodePositions[n.parentId!])

  return (
    <div ref={containerRef} className="relative w-full h-full overflow-hidden" style={{ padding: 8 }}>
      <svg width="100%" height="100%" viewBox={`0 0 ${W} ${H}`} style={{ position: 'absolute', inset: 0 }}>
        {edges.map(n => {
          const p = nodePositions[n.parentId!]
          const c = nodePositions[n.id]
          return (
            <line key={`edge-${n.id}`}
              x1={p.x} y1={p.y} x2={c.x} y2={c.y}
              stroke="color-mix(in srgb, #2d2a26 12%, transparent)"
              strokeWidth={1}
            />
          )
        })}
        {nodes.map(n => {
          const p = nodePositions[n.id]
          if (!p) return null
          const isRoot = n.id === root?.id
          const isHovered = hovered === n.id
          return (
            <g key={n.id}
              onMouseEnter={() => setHovered(n.id)}
              onMouseLeave={() => setHovered(null)}
              style={{ cursor: 'default' }}
            >
              <circle
                cx={p.x} cy={p.y}
                r={isRoot ? 20 : 13}
                fill={isRoot ? 'var(--kajal)' : isHovered ? 'var(--speckle)' : 'var(--paper)'}
                stroke={isRoot ? 'var(--kajal)' : 'color-mix(in srgb, var(--kajal) 20%, transparent)'}
                strokeWidth={isRoot ? 0 : 1}
                style={{ transition: 'fill 0.15s ease' }}
              />
              <text
                x={p.x} y={p.y}
                textAnchor="middle" dominantBaseline="middle"
                fontSize={isRoot ? 7 : 6}
                fontFamily="monospace"
                fill={isRoot ? 'rgba(252,250,242,0.85)' : 'rgba(45,42,38,0.7)'}
                style={{ pointerEvents: 'none', userSelect: 'none' }}
              >
                {n.label.length > 12 ? n.label.slice(0, 11) + '…' : n.label}
              </text>
            </g>
          )
        })}
      </svg>

      {/* Hover tooltip */}
      {hovered && (
        <div
          className="absolute bottom-2 left-2 right-2 rounded px-2 py-1"
          style={{
            background: 'var(--kajal)',
            pointerEvents: 'none',
          }}
        >
          <p className="font-mono text-[9px] leading-snug" style={{ color: 'rgba(252,250,242,0.75)' }}>
            {nodes.find(n => n.id === hovered)?.note}
          </p>
        </div>
      )}
    </div>
  )
}
