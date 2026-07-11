import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { apiJson } from '@/lib/api'
import { avatarColour } from '@/lib/avatara-constants'

interface GraphNode {
  id: string
  kind: 'project' | 'page' | 'section' | 'session' | 'avatar'
  label: string
  weight: number
  meta?: Record<string, unknown>
  trace?: {
    path?: string
    anchor?: string
    preview?: string
    content?: string
    episode_ids?: string[]
  }
}

interface GraphEdge {
  source: string
  target: string
  kind: string
}

interface GraphPayload {
  nodes: GraphNode[]
  edges: GraphEdge[]
  stats?: { projects?: number; wiki_sections_total?: number }
}

interface Positioned extends GraphNode {
  x: number
  y: number
  r: number
}

const VIEW_W = 920
const VIEW_H = 540

const KIND_COLOUR: Record<GraphNode['kind'], string> = {
  project: '#c2410c', // sindoor — anchors of the graph
  page: '#1e2447', // nila
  section: '#92610a', // haldi-deep
  session: '#2d6cdf', // gagan
  avatar: '#0f766e', // mor (overridden per avatar below)
}

const KIND_LABEL: Record<GraphNode['kind'], string> = {
  project: 'Project',
  page: 'Wiki page',
  section: 'Memory entry',
  session: 'Session',
  avatar: 'Avatar',
}

function nodeColour(node: GraphNode): string {
  if (node.kind === 'avatar') {
    try {
      return avatarColour(node.label) || KIND_COLOUR.avatar
    } catch {
      return KIND_COLOUR.avatar
    }
  }
  return KIND_COLOUR[node.kind]
}

function nodeRadius(node: GraphNode): number {
  const base = node.kind === 'project' ? 15 : node.kind === 'page' ? 11 : node.kind === 'avatar' ? 10 : node.kind === 'session' ? 8 : 5.5
  return base + Math.min(Math.sqrt(Math.max(node.weight, 1)) * 1.6, 9)
}

/**
 * Small deterministic force layout — repulsion + edge springs + centre pull.
 * Node counts here are modest (sections are capped server-side), so an O(n²)
 * pass for ~250 iterations is instant and avoids a graph-library dependency.
 */
function runLayout(nodes: GraphNode[], edges: GraphEdge[]): Positioned[] {
  const count = nodes.length
  if (count === 0) return []
  const index = new Map(nodes.map((node, i) => [node.id, i]))
  const xs = new Float64Array(count)
  const ys = new Float64Array(count)

  const ringFor = (kind: GraphNode['kind']) =>
    kind === 'project' ? 40 : kind === 'avatar' ? 110 : kind === 'page' ? 150 : kind === 'session' ? 210 : 235

  nodes.forEach((node, i) => {
    // Deterministic seed: golden-angle spiral per ring, so layouts are stable
    // across reloads of the same graph.
    const angle = i * 2.39996
    const ring = ringFor(node.kind)
    xs[i] = VIEW_W / 2 + Math.cos(angle) * ring
    ys[i] = VIEW_H / 2 + Math.sin(angle) * ring * 0.62
  })

  const springs = edges
    .map(edge => ({ a: index.get(edge.source), b: index.get(edge.target), kind: edge.kind }))
    .filter((s): s is { a: number; b: number; kind: string } => s.a !== undefined && s.b !== undefined)

  const restFor = (kind: string) =>
    kind === 'has_page' ? 95 : kind === 'contains' ? 52 : kind === 'in' ? 130 : 105

  for (let iter = 0; iter < 250; iter++) {
    const heat = 1 - iter / 250
    // Pairwise repulsion
    for (let i = 0; i < count; i++) {
      for (let j = i + 1; j < count; j++) {
        let dx = xs[i] - xs[j]
        let dy = ys[i] - ys[j]
        const distSq = dx * dx + dy * dy || 0.01
        if (distSq > 220 * 220) continue
        const force = (900 / distSq) * heat
        const dist = Math.sqrt(distSq)
        dx /= dist
        dy /= dist
        xs[i] += dx * force
        ys[i] += dy * force
        xs[j] -= dx * force
        ys[j] -= dy * force
      }
    }
    // Edge springs
    for (const spring of springs) {
      const dx = xs[spring.b] - xs[spring.a]
      const dy = ys[spring.b] - ys[spring.a]
      const dist = Math.sqrt(dx * dx + dy * dy) || 0.01
      const pull = ((dist - restFor(spring.kind)) / dist) * 0.045 * heat
      xs[spring.a] += dx * pull
      ys[spring.a] += dy * pull
      xs[spring.b] -= dx * pull
      ys[spring.b] -= dy * pull
    }
    // Centre gravity (elliptical, matches the canvas aspect)
    for (let i = 0; i < count; i++) {
      xs[i] += (VIEW_W / 2 - xs[i]) * 0.012 * heat
      ys[i] += (VIEW_H / 2 - ys[i]) * 0.02 * heat
    }
  }

  return nodes.map((node, i) => ({
    ...node,
    x: Math.max(26, Math.min(VIEW_W - 26, xs[i])),
    y: Math.max(22, Math.min(VIEW_H - 22, ys[i])),
    r: nodeRadius(node),
  }))
}

interface Props {
  userId: string
}

export function KnowledgeGraphPanel({ userId }: Props) {
  const [payload, setPayload] = useState<GraphPayload | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [projectFilter, setProjectFilter] = useState<string | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [pan, setPan] = useState({ x: 0, y: 0, zoom: 1 })
  const dragRef = useRef<{ startX: number; startY: number; panX: number; panY: number } | null>(null)

  const load = useCallback(async (project: string | null) => {
    setLoading(true)
    setError(null)
    try {
      const query = project ? `?project_id=${encodeURIComponent(project)}` : ''
      const data = await apiJson<GraphPayload>(`/smriti/graph/${userId}${query}`)
      setPayload({ nodes: data.nodes ?? [], edges: data.edges ?? [], stats: data.stats })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to load knowledge graph')
    } finally {
      setLoading(false)
    }
  }, [userId])

  useEffect(() => {
    load(projectFilter)
  }, [load, projectFilter])

  const positioned = useMemo(
    () => runLayout(payload?.nodes ?? [], payload?.edges ?? []),
    [payload],
  )
  const byId = useMemo(() => new Map(positioned.map(node => [node.id, node])), [positioned])

  const projects = useMemo(
    () => (payload?.nodes ?? []).filter(node => node.kind === 'project'),
    [payload],
  )

  const neighbours = useMemo(() => {
    if (!selectedId || !payload) return null
    const linked = new Set<string>([selectedId])
    for (const edge of payload.edges) {
      if (edge.source === selectedId) linked.add(edge.target)
      if (edge.target === selectedId) linked.add(edge.source)
    }
    return linked
  }, [payload, selectedId])

  const selected = selectedId ? byId.get(selectedId) ?? null : null

  const onPointerDown = useCallback((event: React.PointerEvent<SVGSVGElement>) => {
    dragRef.current = { startX: event.clientX, startY: event.clientY, panX: pan.x, panY: pan.y }
  }, [pan.x, pan.y])

  const onPointerMove = useCallback((event: React.PointerEvent<SVGSVGElement>) => {
    if (!dragRef.current) return
    const dx = (event.clientX - dragRef.current.startX) / pan.zoom
    const dy = (event.clientY - dragRef.current.startY) / pan.zoom
    setPan(current => ({ ...current, x: dragRef.current!.panX + dx, y: dragRef.current!.panY + dy }))
  }, [pan.zoom])

  const endDrag = useCallback(() => {
    dragRef.current = null
  }, [])

  const zoomBy = useCallback((factor: number) => {
    setPan(current => ({ ...current, zoom: Math.max(0.5, Math.min(2.6, current.zoom * factor)) }))
  }, [])

  const sectionTotal = payload?.stats?.wiki_sections_total ?? 0

  return (
    <section
      style={{
        marginTop: 16,
        padding: 18,
        borderRadius: 18,
        border: '1px solid rgba(26,24,21,0.08)',
        background: 'rgba(252,250,242,0.9)',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' }}>
        <div style={{ fontSize: 18, fontWeight: 700, color: 'var(--kajal)', fontFamily: 'var(--font-hero)' }}>
          Knowledge graph
        </div>
        <div style={{ fontSize: 12, color: 'rgba(26,24,21,0.52)' }}>
          {sectionTotal > 0 ? `${sectionTotal} memory entries across ${projects.length || 1} project${projects.length === 1 ? '' : 's'} — click any node to trace it to its source.` : 'How projects, memory entries, sessions, and avatars connect.'}
        </div>
        <button
          type="button"
          onClick={() => load(projectFilter)}
          style={{ marginLeft: 'auto', padding: '6px 11px', borderRadius: 10, border: '1px solid rgba(26,24,21,0.12)', background: 'rgba(252,250,242,0.85)', color: 'var(--kajal)', cursor: 'pointer', fontSize: 11.5, fontWeight: 600 }}
        >
          Refresh
        </button>
      </div>

      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 12 }}>
        <button
          type="button"
          onClick={() => { setProjectFilter(null); setSelectedId(null) }}
          style={{
            padding: '5px 10px', borderRadius: 999, fontSize: 11.5, fontWeight: 600, cursor: 'pointer',
            border: `1px solid ${projectFilter === null ? 'rgba(194,65,12,0.4)' : 'rgba(26,24,21,0.12)'}`,
            background: projectFilter === null ? 'rgba(194,65,12,0.12)' : 'rgba(252,250,242,0.7)',
            color: projectFilter === null ? 'var(--sindoor)' : 'rgba(26,24,21,0.52)',
          }}
        >
          All projects
        </button>
        {projects.map(project => {
          const pid = String(project.meta?.project_id ?? project.label)
          const active = projectFilter === pid
          return (
            <button
              key={project.id}
              type="button"
              onClick={() => { setProjectFilter(active ? null : pid); setSelectedId(null) }}
              style={{
                padding: '5px 10px', borderRadius: 999, fontSize: 11.5, fontWeight: 600, cursor: 'pointer',
                border: `1px solid ${active ? 'rgba(194,65,12,0.4)' : 'rgba(26,24,21,0.12)'}`,
                background: active ? 'rgba(194,65,12,0.12)' : 'rgba(252,250,242,0.7)',
                color: active ? 'var(--sindoor)' : 'rgba(26,24,21,0.52)',
              }}
            >
              {project.label}
            </button>
          )
        })}
      </div>

      <div style={{ display: 'grid', gap: 14, marginTop: 14 }} className="xl:grid-cols-[minmax(0,1.7fr)_minmax(240px,1fr)]">
        <div style={{ position: 'relative', borderRadius: 16, border: '1px solid rgba(26,24,21,0.08)', background: 'linear-gradient(160deg, rgba(252,250,242,0.6) 0%, rgba(243,239,225,0.75) 100%)', overflow: 'hidden' }}>
          {loading && (
            <div className="skeleton" style={{ position: 'absolute', inset: 0 }} aria-label="Loading knowledge graph" />
          )}
          {!loading && error && (
            <div style={{ padding: 20, fontSize: 12.5, color: 'var(--kesari)' }}>{error}</div>
          )}
          {!loading && !error && positioned.length === 0 && (
            <div style={{ padding: 20, fontSize: 12.5, color: 'rgba(26,24,21,0.5)' }}>
              No knowledge captured yet — memories appear here as you work with Narad.
            </div>
          )}
          {!loading && !error && positioned.length > 0 && (
            <>
              <svg
                viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
                style={{ display: 'block', width: '100%', height: 'auto', cursor: dragRef.current ? 'grabbing' : 'grab', touchAction: 'none' }}
                onPointerDown={onPointerDown}
                onPointerMove={onPointerMove}
                onPointerUp={endDrag}
                onPointerLeave={endDrag}
                role="img"
                aria-label="Knowledge graph of projects, wiki pages, memory entries, sessions, and avatars"
              >
                <g transform={`translate(${VIEW_W / 2},${VIEW_H / 2}) scale(${pan.zoom}) translate(${pan.x - VIEW_W / 2},${pan.y - VIEW_H / 2})`}>
                  {(payload?.edges ?? []).map((edge, i) => {
                    const a = byId.get(edge.source)
                    const b = byId.get(edge.target)
                    if (!a || !b) return null
                    const lit = neighbours ? neighbours.has(edge.source) && neighbours.has(edge.target) && (edge.source === selectedId || edge.target === selectedId) : false
                    return (
                      <line
                        key={`${edge.source}-${edge.target}-${i}`}
                        x1={a.x} y1={a.y} x2={b.x} y2={b.y}
                        stroke={lit ? 'var(--sindoor)' : 'rgba(26,24,21,0.55)'}
                        strokeWidth={lit ? 1.6 : 0.7}
                        opacity={neighbours && !lit ? 0.10 : lit ? 0.85 : 0.22}
                      />
                    )
                  })}
                  {positioned.map(node => {
                    const dimmed = neighbours ? !neighbours.has(node.id) : false
                    const isSelected = node.id === selectedId
                    return (
                      <g
                        key={node.id}
                        transform={`translate(${node.x},${node.y})`}
                        style={{ cursor: 'pointer' }}
                        opacity={dimmed ? 0.28 : 1}
                        onPointerDown={event => event.stopPropagation()}
                        onClick={() => setSelectedId(current => current === node.id ? null : node.id)}
                      >
                        <circle
                          r={node.r}
                          fill={nodeColour(node)}
                          opacity={node.kind === 'section' ? 0.75 : 0.92}
                          stroke={isSelected ? 'var(--kajal)' : 'rgba(252,250,242,0.9)'}
                          strokeWidth={isSelected ? 2.4 : 1.2}
                        />
                        {(node.kind === 'project' || node.kind === 'page' || node.kind === 'avatar') && (
                          <text
                            y={node.r + 12}
                            textAnchor="middle"
                            style={{ fontSize: node.kind === 'project' ? 12 : 10.5, fontWeight: 700, fill: 'var(--kajal)', paintOrder: 'stroke', stroke: 'rgba(252,250,242,0.85)', strokeWidth: 3 }}
                          >
                            {node.label.length > 18 ? `${node.label.slice(0, 17)}…` : node.label}
                          </text>
                        )}
                      </g>
                    )
                  })}
                </g>
              </svg>
              <div style={{ position: 'absolute', right: 10, top: 10, display: 'flex', gap: 6 }}>
                {[{ label: '+', factor: 1.25 }, { label: '−', factor: 0.8 }].map(control => (
                  <button
                    key={control.label}
                    type="button"
                    onClick={() => zoomBy(control.factor)}
                    aria-label={control.label === '+' ? 'Zoom in' : 'Zoom out'}
                    style={{ width: 28, height: 28, borderRadius: 9, border: '1px solid rgba(26,24,21,0.14)', background: 'rgba(252,250,242,0.92)', color: 'var(--kajal)', fontSize: 15, fontWeight: 700, cursor: 'pointer', lineHeight: 1 }}
                  >
                    {control.label}
                  </button>
                ))}
              </div>
              <div style={{ position: 'absolute', left: 12, bottom: 10, display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                {(Object.keys(KIND_LABEL) as GraphNode['kind'][]).map(kind => (
                  <span key={kind} style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 10.5, color: 'rgba(26,24,21,0.55)' }}>
                    <span style={{ width: 8, height: 8, borderRadius: 99, background: KIND_COLOUR[kind], display: 'inline-block' }} />
                    {KIND_LABEL[kind]}
                  </span>
                ))}
              </div>
            </>
          )}
        </div>

        <div style={{ borderRadius: 16, border: '1px solid rgba(26,24,21,0.08)', background: 'rgba(26,24,21,0.03)', padding: 14, minHeight: 180 }}>
          {!selected && (
            <div style={{ fontSize: 12.5, lineHeight: 1.6, color: 'rgba(26,24,21,0.5)' }}>
              <div style={{ fontWeight: 700, color: 'rgba(26,24,21,0.65)', marginBottom: 6 }}>Trace a memory</div>
              Select any node to see exactly where it came from — the wiki page, the session, and the verbatim text Narad recorded.
            </div>
          )}
          {selected && (
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                <span style={{ padding: '2px 8px', borderRadius: 999, fontSize: 10, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.1em', background: nodeColour(selected), color: 'var(--paper)' }}>
                  {KIND_LABEL[selected.kind]}
                </span>
                {typeof selected.meta?.avatar === 'string' && selected.meta.avatar && (
                  <span style={{ fontSize: 11, fontWeight: 600, color: 'rgba(26,24,21,0.55)' }}>
                    by {String(selected.meta.avatar)}
                  </span>
                )}
              </div>
              <div style={{ marginTop: 8, fontSize: 14.5, fontWeight: 700, color: 'var(--kajal)', overflowWrap: 'anywhere' }}>
                {selected.label}
              </div>
              <div style={{ marginTop: 6, display: 'grid', gap: 3, fontSize: 11.5, color: 'rgba(26,24,21,0.55)' }}>
                {selected.kind === 'project' && (
                  <>
                    <span>{String(selected.meta?.page_count ?? 0)} wiki pages · {String(selected.meta?.session_count ?? 0)} sessions</span>
                    <span>{String(selected.meta?.episode_count ?? 0)} recorded episodes</span>
                  </>
                )}
                {selected.kind === 'page' && <span>{String(selected.meta?.section_count ?? 0)} entries on this page</span>}
                {selected.kind === 'session' && <span>{String(selected.meta?.episode_count ?? 0)} episodes · last {String(selected.meta?.last_ts ?? '').slice(0, 16).replace('T', ' ')}</span>}
                {selected.kind === 'avatar' && <span>{String(selected.meta?.section_count ?? 0)} memory entries recorded</span>}
              </div>
              {selected.trace?.content && (
                <pre
                  style={{
                    marginTop: 10, padding: '10px 12px', borderRadius: 12, maxHeight: 190, overflowY: 'auto',
                    background: 'rgba(252,250,242,0.9)', border: '1px solid rgba(26,24,21,0.08)',
                    fontSize: 11, lineHeight: 1.55, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', color: 'rgba(26,24,21,0.75)',
                  }}
                >
                  {selected.trace.content}
                </pre>
              )}
              {!selected.trace?.content && selected.trace?.preview && (
                <div style={{ marginTop: 10, fontSize: 12, lineHeight: 1.55, color: 'rgba(26,24,21,0.65)' }}>
                  {selected.trace.preview}
                </div>
              )}
              {selected.trace?.path && (
                <div style={{ marginTop: 8, fontSize: 10.5, color: 'rgba(26,24,21,0.42)', overflowWrap: 'anywhere' }}>
                  {selected.trace.path.split('/').slice(-3).join('/')}
                  {selected.trace.anchor ? ` § ${selected.trace.anchor}` : ''}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </section>
  )
}
