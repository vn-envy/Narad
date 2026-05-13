import { useState, useEffect, useCallback, useMemo } from 'react'
import { ChevronLeft, ChevronRight, ChevronDown, RefreshCw, Search, X, FolderOpen } from 'lucide-react'

const API = 'http://localhost:8000'
const USER_ID = 'default'

// ── Types ──────────────────────────────────────────────────────────────────────

interface Project {
  id: string
  name: string
  created_at: string | null
  session_count: number
}

interface WikiPage {
  entity: string
  filename: string
  preview: string
  size_chars: number
}

interface MemoryCard {
  ts: string
  avatar: string
  task: string
  summary?: string
  entity: string
}

type EntityType = 'goals' | 'decisions' | 'features' | 'insights' | 'context'
const ENTITY_TYPES: EntityType[] = ['goals', 'decisions', 'features', 'insights', 'context']

const ENTITY_COLORS: Record<string, string> = {
  decisions: '#065f46',
  features:  '#1d4ed8',
  goals:     '#6d28d9',
  insights:  '#92610a',
  context:   '#57534e',
}

const ENTITY_LABELS: Record<string, string> = {
  goals:     'Goals',
  decisions: 'Decisions',
  features:  'Features',
  insights:  'Insights',
  context:   'Context',
}

const AVATAR_COLORS: Record<string, string> = {
  parashurama: 'rgba(87,83,78,0.30)',
  matsya:      'rgba(6,95,70,0.35)',
  krishna:     'rgba(6,95,70,0.35)',
  varaha:      'rgba(194,65,12,0.35)',
  narasimha:   'rgba(220,38,38,0.35)',
  buddha:      'rgba(252,211,77,0.50)',
  rama:        'rgba(45,42,38,0.30)',
  vamana:      'rgba(120,113,108,0.30)',
  narad:       'rgba(194,65,12,0.35)',
}

function avatarBorder(name: string): string {
  return AVATAR_COLORS[name.toLowerCase()] ?? 'rgba(45,42,38,0.20)'
}

// ── Parsers & helpers ──────────────────────────────────────────────────────────

function parseWikiCards(markdown: string, entity: string): MemoryCard[] {
  const sections = markdown.split(/\n## /).slice(1)
  return sections.map(section => {
    const lines = section.split('\n')
    const ts = lines[0]?.trim() ?? ''
    const avatarMatch = section.match(/\*\*Avatar:\*\*\s*(.+?)(\s*$|\s+\\n|\n)/m)
    const taskMatch   = section.match(/\*\*Task:\*\*\s*(.+)/s)
    const summMatch   = section.match(/\*\*Summary:\*\*\s*(.+)/s)
    return {
      ts,
      avatar:  avatarMatch?.[1]?.trim() ?? 'Unknown',
      task:    taskMatch?.[1]?.replace(/\s*…\s*$/, '…').trim() ?? '',
      summary: summMatch?.[1]?.trim(),
      entity,
    }
  }).filter(c => c.task)
}

function formatDate(iso: string | null): string {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
  } catch {
    return iso.slice(0, 10)
  }
}

function matchesSearch(card: MemoryCard, query: string): boolean {
  if (!query) return true
  const q = query.toLowerCase()
  return (
    card.task.toLowerCase().includes(q) ||
    (card.summary ?? '').toLowerCase().includes(q) ||
    card.avatar.toLowerCase().includes(q) ||
    card.entity.toLowerCase().includes(q)
  )
}

// ── Sub-components ─────────────────────────────────────────────────────────────

function MemoryCardItem({ card }: { card: MemoryCard }) {
  const [expanded, setExpanded] = useState(false)
  const border = avatarBorder(card.avatar)
  const badgeBg = border.replace(/[\d.]+\)$/, '0.65)')

  return (
    <div
      className="cursor-pointer"
      style={{
        background: border.replace(/[\d.]+\)$/, '0.04)'),
        borderLeft: `2px solid ${border}`,
        padding: '7px 9px',
        marginBottom: 4,
        borderRadius: '0 3px 3px 0',
      }}
      onClick={() => setExpanded(e => !e)}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 2, gap: 4 }}>
        <span className="font-mono text-[8px] tracking-[0.06em]" style={{ color: 'rgba(45,42,38,0.40)', flexShrink: 0, marginTop: 1 }}>
          {formatDate(card.ts)}
        </span>
        <span
          className="font-mono text-[7px] tracking-[0.05em] uppercase"
          style={{
            color: 'var(--paper)',
            background: badgeBg,
            padding: '1px 4px',
            borderRadius: 2,
            flexShrink: 0,
          }}
        >
          {card.avatar.slice(0, 5)}
        </span>
      </div>
      <p
        className="font-serif text-[11px] leading-[1.4]"
        style={{
          color: 'var(--kajal)',
          fontStyle: 'italic',
          display: '-webkit-box',
          WebkitLineClamp: expanded ? undefined : 3,
          WebkitBoxOrient: 'vertical',
          overflow: expanded ? 'visible' : 'hidden',
        }}
      >
        {card.task}
      </p>
      {expanded && card.summary && (
        <p className="font-mono text-[9px] leading-[1.5]" style={{ color: 'rgba(45,42,38,0.55)', marginTop: 5 }}>
          {card.summary}
        </p>
      )}
    </div>
  )
}

function EntitySection({
  entity,
  cards,
  searchQuery,
}: {
  entity: EntityType
  cards: MemoryCard[]
  searchQuery: string
}) {
  const visible = cards.filter(c => matchesSearch(c, searchQuery))
  if (visible.length === 0) return null
  const color = ENTITY_COLORS[entity]

  return (
    <div style={{ marginBottom: 12 }}>
      <div
        className="flex items-center gap-1.5 mb-1.5 px-1"
        style={{ opacity: 0.70 }}
      >
        <div style={{ flex: 1, height: 1, background: `${color}40` }} />
        <span
          className="font-mono text-[8px] tracking-[0.10em] uppercase"
          style={{ color, flexShrink: 0 }}
        >
          {ENTITY_LABELS[entity]}
        </span>
        <span
          className="font-mono text-[8px]"
          style={{ color: `${color}99`, flexShrink: 0 }}
        >
          {visible.length}
        </span>
        <div style={{ flex: 1, height: 1, background: `${color}40` }} />
      </div>
      {visible.map((card, i) => (
        <MemoryCardItem key={i} card={card} />
      ))}
    </div>
  )
}

// ── Project selector dropdown ──────────────────────────────────────────────────

function ProjectSelector({
  projects,
  selected,
  onSelect,
  loading,
  onRefresh,
}: {
  projects: Project[]
  selected: Project | null
  onSelect: (p: Project | null) => void
  loading: boolean
  onRefresh: () => void
}) {
  const [open, setOpen] = useState(false)

  return (
    <div style={{ position: 'relative' }}>
      <button
        className="flex items-center gap-1.5 w-full px-3 py-2 text-left"
        style={{
          background: 'color-mix(in srgb, var(--kajal) 4%, var(--speckle))',
          borderBottom: '1px solid color-mix(in srgb, var(--kajal) 10%, transparent)',
          minHeight: 36,
        }}
        onClick={() => setOpen(o => !o)}
      >
        <FolderOpen size={10} style={{ color: 'rgba(45,42,38,0.45)', flexShrink: 0 }} />
        <span
          className="font-serif text-[11px] flex-1 truncate"
          style={{ color: selected ? 'var(--kajal)' : 'rgba(45,42,38,0.40)', fontStyle: selected ? 'italic' : 'normal' }}
        >
          {selected ? selected.name : 'Select project…'}
        </span>
        {selected && (
          <span className="font-mono text-[8px]" style={{ color: 'rgba(45,42,38,0.35)', flexShrink: 0 }}>
            {selected.session_count}s
          </span>
        )}
        <ChevronDown size={9} style={{ color: 'rgba(45,42,38,0.40)', flexShrink: 0, transform: open ? 'rotate(180deg)' : 'none', transition: 'transform 0.15s' }} />
      </button>

      {open && (
        <div
          style={{
            position: 'absolute',
            top: '100%',
            left: 0,
            right: 0,
            zIndex: 20,
            background: 'var(--paper)',
            border: '1px solid color-mix(in srgb, var(--kajal) 14%, transparent)',
            boxShadow: '0 4px 16px rgba(45,42,38,0.12)',
            borderRadius: '0 0 4px 4px',
            maxHeight: 200,
            overflowY: 'auto',
          }}
        >
          {selected && (
            <button
              className="flex items-center gap-2 w-full px-3 py-2 hover:bg-kajal/5 transition-colors text-left"
              style={{ borderBottom: '1px solid color-mix(in srgb, var(--kajal) 8%, transparent)' }}
              onClick={() => { onSelect(null); setOpen(false) }}
            >
              <span className="font-mono text-[9px]" style={{ color: 'rgba(45,42,38,0.45)' }}>— clear selection</span>
            </button>
          )}
          {loading ? (
            <p className="font-mono text-[9px] px-3 py-2" style={{ color: 'rgba(45,42,38,0.40)' }}>Loading…</p>
          ) : projects.length === 0 ? (
            <p className="font-mono text-[9px] px-3 py-2" style={{ color: 'rgba(45,42,38,0.40)' }}>No projects yet.</p>
          ) : (
            projects.map(p => (
              <button
                key={p.id}
                className="flex items-center gap-2 w-full px-3 py-2 hover:bg-kajal/5 transition-colors text-left"
                style={{
                  background: selected?.id === p.id ? 'rgba(194,65,12,0.04)' : undefined,
                  borderLeft: selected?.id === p.id ? '2px solid var(--marigold)' : '2px solid transparent',
                }}
                onClick={() => { onSelect(p); setOpen(false) }}
              >
                <span className="font-serif text-[11px] flex-1 truncate" style={{ color: 'var(--kajal)', fontStyle: 'italic' }}>
                  {p.name}
                </span>
                <span className="font-mono text-[8px]" style={{ color: 'rgba(45,42,38,0.35)', flexShrink: 0 }}>
                  {p.session_count}
                </span>
              </button>
            ))
          )}
          <button
            className="flex items-center gap-1.5 w-full px-3 py-2 hover:bg-kajal/5 transition-colors"
            style={{ borderTop: '1px solid color-mix(in srgb, var(--kajal) 8%, transparent)' }}
            onClick={() => { onRefresh(); setOpen(false) }}
          >
            <RefreshCw size={8} style={{ color: 'rgba(45,42,38,0.35)' }} className={loading ? 'animate-spin' : ''} />
            <span className="font-mono text-[8px]" style={{ color: 'rgba(45,42,38,0.40)' }}>Refresh</span>
          </button>
        </div>
      )}
    </div>
  )
}

// ── Main Component ─────────────────────────────────────────────────────────────

export function ProjectsPanel({
  open,
  onToggle,
}: {
  open: boolean
  onToggle: () => void
  onNewSession?: () => void
}) {
  const [projects, setProjects]                             = useState<Project[]>([])
  const [selectedProject, setSelectedProject]               = useState<Project | null>(null)
  const [allCards, setAllCards]                             = useState<Record<string, MemoryCard[]>>({})
  const [activeFilter, setActiveFilter]                     = useState<EntityType | 'all'>('all')
  const [searchQuery, setSearchQuery]                       = useState('')
  const [loadingProjects, setLoadingProjects]               = useState(false)
  const [loadingCards, setLoadingCards]                     = useState(false)

  const fetchProjects = useCallback(async () => {
    setLoadingProjects(true)
    try {
      const res = await fetch(`${API}/projects/${USER_ID}`)
      const data = await res.json()
      setProjects(data.projects ?? [])
    } catch { /* ignore */ } finally {
      setLoadingProjects(false)
    }
  }, [])

  const fetchAllCards = useCallback(async (projectId: string) => {
    setLoadingCards(true)
    setAllCards({})
    const results: Record<string, MemoryCard[]> = {}
    await Promise.all(
      ENTITY_TYPES.map(async entity => {
        try {
          const res = await fetch(`${API}/wiki/${USER_ID}/${projectId}/${entity}`)
          if (!res.ok) return
          const text = await res.text()
          results[entity] = parseWikiCards(text, entity)
        } catch { /* ignore */ }
      })
    )
    setAllCards(results)
    setLoadingCards(false)
  }, [])

  useEffect(() => { fetchProjects() }, [fetchProjects])

  useEffect(() => {
    if (selectedProject) {
      fetchAllCards(selectedProject.id)
      setActiveFilter('all')
      setSearchQuery('')
    } else {
      setAllCards({})
    }
  }, [selectedProject, fetchAllCards])

  // Flat list of all cards (for filtered/searched view)
  const flatCards = useMemo(() => {
    const entityOrder: EntityType[] = activeFilter === 'all'
      ? ENTITY_TYPES
      : [activeFilter]
    return entityOrder.flatMap(e => allCards[e] ?? [])
  }, [allCards, activeFilter])

  const totalCards = useMemo(
    () => Object.values(allCards).reduce((sum, arr) => sum + arr.length, 0),
    [allCards]
  )

  const hasResults = useMemo(
    () => flatCards.some(c => matchesSearch(c, searchQuery)),
    [flatCards, searchQuery]
  )

  if (!open) {
    return (
      <div
        style={{
          width: 40,
          background: 'var(--speckle)',
          borderRight: '1px solid color-mix(in srgb, var(--kajal) 10%, transparent)',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          paddingTop: 12,
          flexShrink: 0,
          cursor: 'pointer',
        }}
        onClick={onToggle}
        title="Open Smriti"
      >
        <ChevronRight size={12} style={{ color: 'rgba(45,42,38,0.40)', marginBottom: 8 }} />
        <span
          className="font-mono text-[8px] tracking-[0.14em] uppercase"
          style={{
            color: 'rgba(45,42,38,0.35)',
            writingMode: 'vertical-rl',
            transform: 'rotate(180deg)',
          }}
        >
          Smriti
        </span>
      </div>
    )
  }

  return (
    <div
      style={{
        width: 260,
        background: 'var(--speckle)',
        borderRight: '1px solid color-mix(in srgb, var(--kajal) 10%, transparent)',
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        overflow: 'hidden',
        flexShrink: 0,
      }}
    >
      {/* ── Header ── */}
      <div
        style={{
          height: 36,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '0 6px 0 12px',
          borderBottom: '1px solid color-mix(in srgb, var(--kajal) 10%, transparent)',
          flexShrink: 0,
          background: 'var(--kajal)',
        }}
      >
        <span className="font-mono text-[9px] tracking-[0.12em] uppercase" style={{ color: 'rgba(252,250,242,0.55)' }}>
          स्मृति  Smriti
        </span>
        <button
          className="p-1 rounded hover:bg-white/10 transition-colors"
          onClick={onToggle}
          title="Collapse panel"
        >
          <ChevronLeft size={9} style={{ color: 'rgba(252,250,242,0.40)' }} />
        </button>
      </div>

      {/* ── Project selector dropdown ── */}
      <ProjectSelector
        projects={projects}
        selected={selectedProject}
        onSelect={setSelectedProject}
        loading={loadingProjects}
        onRefresh={fetchProjects}
      />

      {/* ── Search bar ── */}
      <div
        style={{
          padding: '6px 8px',
          borderBottom: '1px solid color-mix(in srgb, var(--kajal) 8%, transparent)',
          flexShrink: 0,
        }}
      >
        <div
          className="flex items-center gap-1.5"
          style={{
            background: 'var(--paper)',
            border: '1px solid color-mix(in srgb, var(--kajal) 12%, transparent)',
            borderRadius: 4,
            padding: '4px 8px',
          }}
        >
          <Search size={9} style={{ color: 'rgba(45,42,38,0.35)', flexShrink: 0 }} />
          <input
            type="text"
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
            placeholder={selectedProject ? 'Search memory…' : 'Select a project first'}
            disabled={!selectedProject}
            className="flex-1 font-mono text-[10px] bg-transparent outline-none"
            style={{
              color: 'var(--kajal)',
              minWidth: 0,
            }}
          />
          {searchQuery && (
            <button onClick={() => setSearchQuery('')} style={{ flexShrink: 0 }}>
              <X size={8} style={{ color: 'rgba(45,42,38,0.40)' }} />
            </button>
          )}
        </div>
      </div>

      {/* ── Filter chips ── */}
      {selectedProject && (
        <div
          className="flex items-center gap-1 flex-wrap"
          style={{
            padding: '5px 8px',
            borderBottom: '1px solid color-mix(in srgb, var(--kajal) 8%, transparent)',
            flexShrink: 0,
          }}
        >
          {(['all', ...ENTITY_TYPES] as (EntityType | 'all')[]).map(filter => {
            const isActive = activeFilter === filter
            const count = filter === 'all' ? totalCards : (allCards[filter]?.length ?? 0)
            const color = filter === 'all' ? 'rgba(45,42,38,0.65)' : ENTITY_COLORS[filter]
            return (
              <button
                key={filter}
                onClick={() => setActiveFilter(filter)}
                className="font-mono text-[8px] tracking-[0.06em] uppercase px-1.5 py-px rounded transition-all"
                style={{
                  color: isActive ? 'var(--paper)' : color,
                  background: isActive
                    ? (filter === 'all' ? 'rgba(45,42,38,0.70)' : `${ENTITY_COLORS[filter]}cc`)
                    : 'transparent',
                  border: `1px solid ${isActive ? 'transparent' : `${color}40`}`,
                  opacity: count === 0 && filter !== 'all' ? 0.35 : 1,
                }}
              >
                {filter === 'all' ? 'all' : filter.slice(0, 4)}
                {count > 0 && (
                  <span style={{ marginLeft: 2, opacity: 0.65 }}>{count}</span>
                )}
              </button>
            )
          })}
        </div>
      )}

      {/* ── Memory content ── */}
      <div className="panel-scroll" style={{ flex: '1 1 0', minHeight: 0 }}>
        {!selectedProject ? (
          <div style={{ padding: '24px 16px', textAlign: 'center' }}>
            <FolderOpen size={18} style={{ color: 'rgba(45,42,38,0.20)', margin: '0 auto 8px' }} />
            <p className="font-mono text-[9px]" style={{ color: 'rgba(45,42,38,0.35)' }}>
              Select a project to browse memory.
            </p>
          </div>
        ) : loadingCards ? (
          <div style={{ padding: '12px 10px' }}>
            <p className="font-mono text-[9px]" style={{ color: 'rgba(45,42,38,0.40)' }}>Loading memory…</p>
          </div>
        ) : totalCards === 0 ? (
          <div style={{ padding: '24px 16px', textAlign: 'center' }}>
            <FolderOpen size={18} style={{ color: 'rgba(45,42,38,0.20)', margin: '0 auto 8px' }} />
            <p className="font-mono text-[9px]" style={{ color: 'rgba(45,42,38,0.35)' }}>
              No memory yet for this project.
            </p>
          </div>
        ) : !hasResults ? (
          <div style={{ padding: '12px 10px' }}>
            <p className="font-mono text-[9px]" style={{ color: 'rgba(45,42,38,0.40)' }}>
              No results for "{searchQuery}".
            </p>
          </div>
        ) : (
          <div style={{ padding: '8px 8px 16px' }}>
            {activeFilter === 'all' ? (
              // Grouped by entity type
              ENTITY_TYPES.map(entity => (
                <EntitySection
                  key={entity}
                  entity={entity}
                  cards={allCards[entity] ?? []}
                  searchQuery={searchQuery}
                />
              ))
            ) : (
              // Flat list for single entity filter
              (allCards[activeFilter] ?? [])
                .filter(c => matchesSearch(c, searchQuery))
                .map((card, i) => <MemoryCardItem key={i} card={card} />)
            )}
          </div>
        )}
      </div>
    </div>
  )
}
