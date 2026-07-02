import { useMemo, useState } from 'react'

interface ConceptNode {
  id: string
  label: string
  note: string
}

interface ConceptEdge {
  source: string
  target: string
  label?: string
}

interface Props {
  topic: string
  doc: {
    nodes?: ConceptNode[]
    edges?: ConceptEdge[]
  }
}

export function ConceptDiagramArtifact({ topic, doc }: Props) {
  const nodes = useMemo<ConceptNode[]>(() => {
    if (Array.isArray(doc.nodes) && doc.nodes.length > 0) return doc.nodes
    return [
      { id: 'topic', label: topic, note: `The central concept being studied: ${topic}.` },
      { id: 'intuition', label: 'intuition', note: `Why ${topic} matters.` },
      { id: 'mechanics', label: 'mechanics', note: `How ${topic} works.` },
      { id: 'examples', label: 'examples', note: `Where ${topic} appears.` },
    ]
  }, [doc.nodes, topic])
  const edges = useMemo<ConceptEdge[]>(() => {
    if (Array.isArray(doc.edges) && doc.edges.length > 0) return doc.edges
    const root = nodes[0]?.id ?? 'topic'
    return nodes.slice(1).map(node => ({ source: root, target: node.id }))
  }, [doc.edges, nodes])

  const [hovered, setHovered] = useState<string | null>(null)

  const W = 320
  const H = 260
  const cx = W / 2
  const cy = H / 2
  const root = nodes[0]
  const children = nodes.slice(1)
  const positions: Record<string, { x: number; y: number }> = {}
  if (root) {
    positions[root.id] = { x: cx, y: cy }
  }
  children.forEach((node, index) => {
    const angle = (index / Math.max(children.length, 1)) * 2 * Math.PI - Math.PI / 2
    positions[node.id] = {
      x: cx + 95 * Math.cos(angle),
      y: cy + 95 * Math.sin(angle),
    }
  })

  return (
    <div className="relative w-full h-full overflow-hidden" style={{ padding: 10 }}>
      <svg width="100%" height="100%" viewBox={`0 0 ${W} ${H}`} style={{ position: 'absolute', inset: 0 }}>
        {edges.map(edge => {
          const source = positions[edge.source]
          const target = positions[edge.target]
          if (!source || !target) return null
          return (
            <line
              key={`${edge.source}-${edge.target}`}
              x1={source.x}
              y1={source.y}
              x2={target.x}
              y2={target.y}
              stroke="color-mix(in srgb, #2d2a26 12%, transparent)"
              strokeWidth={1}
            />
          )
        })}

        {nodes.map(node => {
          const point = positions[node.id]
          if (!point) return null
          const isRoot = node.id === root?.id
          const isHovered = hovered === node.id
          return (
            <g
              key={node.id}
              onMouseEnter={() => setHovered(node.id)}
              onMouseLeave={() => setHovered(null)}
              style={{ cursor: 'default' }}
            >
              <circle
                cx={point.x}
                cy={point.y}
                r={isRoot ? 24 : 16}
                fill={isRoot ? 'var(--kajal)' : isHovered ? 'var(--speckle)' : 'var(--paper)'}
                stroke={isRoot ? 'var(--kajal)' : 'color-mix(in srgb, var(--kajal) 20%, transparent)'}
                strokeWidth={isRoot ? 0 : 1}
              />
              <text
                x={point.x}
                y={point.y}
                textAnchor="middle"
                dominantBaseline="middle"
                fontSize={isRoot ? 8 : 7}
                fontFamily="monospace"
                fill={isRoot ? 'rgba(252,250,242,0.85)' : 'rgba(45,42,38,0.7)'}
                style={{ pointerEvents: 'none', userSelect: 'none' }}
              >
                {node.label.length > 14 ? `${node.label.slice(0, 13)}…` : node.label}
              </text>
            </g>
          )
        })}
      </svg>

      <div
        className="absolute bottom-2 left-2 right-2 rounded px-3 py-2"
        style={{ background: 'var(--kajal)' }}
      >
        <p className="font-mono text-[9px] leading-snug" style={{ color: 'rgba(252,250,242,0.78)' }}>
          {hovered
            ? nodes.find(node => node.id === hovered)?.note
            : 'Hover a node to inspect the explanation. Use the main chat to add or remove nodes explicitly.'}
        </p>
      </div>
    </div>
  )
}
