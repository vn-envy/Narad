import { useCallback, useEffect, useMemo, useState } from 'react'
import { Clock3, FolderOpen, RefreshCw, Search } from 'lucide-react'
import { apiFetch } from '@/lib/api'

const USER_ID = 'default'

interface Project {
  id: string
  name: string
  created_at: string | null
  session_count: number
}

interface WikiPageMeta {
  entity: string
  filename: string
  preview: string
  size_chars: number
}

interface SessionMeta {
  session_id: string
  ts: string | null
  query: string | null
  avatars: string[]
  total_ms: number | null
}

interface MemoryCard {
  id: string
  ts: string
  avatar: string
  task: string
  summary: string
  entity: string
}

type EntityType = 'goals' | 'decisions' | 'features' | 'insights' | 'context'

const ENTITY_ORDER: EntityType[] = ['goals', 'decisions', 'features', 'insights', 'context']

const ENTITY_META: Record<EntityType, { label: string; color: string }> = {
  goals: { label: 'Goals', color: 'var(--marigold)' },
  decisions: { label: 'Decisions', color: 'var(--tulsi)' },
  features: { label: 'Features', color: 'var(--gagan)' },
  insights: { label: 'Insights', color: 'var(--haldi)' },
  context: { label: 'Context', color: 'var(--loha)' },
}

export interface ProjectWorkspaceContext {
  projectId: string
  projectName: string
  sessionIds: string[]
  latestQuery: string | null
  latestSessionAt: string | null
  sessionCount: number
  wikiPageCount: number
  totalMemoryCards: number
  recentAvatars: string[]
  highlights: Array<{
    label: string
    text: string
    color: string
  }>
}

interface Props {
  onProjectChange?: (context: ProjectWorkspaceContext | null) => void
}

function formatDate(iso: string | null): string {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
  } catch {
    return iso.slice(0, 10)
  }
}

function formatTime(iso: string | null): string {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString('en-US', {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return iso
  }
}

function formatDuration(totalMs: number | null): string {
  if (!totalMs || totalMs <= 0) return '—'
  return totalMs >= 1000 ? `${(totalMs / 1000).toFixed(1)}s` : `${totalMs}ms`
}

function truncate(text: string | null | undefined, limit = 180): string {
  const value = (text ?? '').trim()
  if (!value) return '—'
  return value.length > limit ? `${value.slice(0, limit - 1)}…` : value
}

function parseWikiCards(markdown: string, entity: EntityType): MemoryCard[] {
  const sections = markdown.split(/\n## /).slice(1)
  const cards: MemoryCard[] = []
  for (const section of sections) {
    const lines = section.split('\n')
    const ts = lines[0]?.trim() ?? ''
    const avatarMatch = section.match(/\*\*Avatar:\*\*\s*(.+?)(\s*$|\s+\\n|\n)/m)
    const taskMatch = section.match(/\*\*Task:\*\*\s*(.+?)(\n\*\*Summary:\*\*|\s*$)/s)
    const summaryMatch = section.match(/\*\*Summary:\*\*\s*(.+)/s)
    const task = taskMatch?.[1]?.trim() ?? ''
    if (!task) continue
    cards.push({
      id: `${entity}-${ts}-${task.slice(0, 40)}`,
      ts,
      avatar: avatarMatch?.[1]?.trim() ?? 'Unknown',
      task,
      summary: summaryMatch?.[1]?.trim() ?? '',
      entity,
    })
  }
  return cards
}

function StatTile({ label, value, hint }: { label: string; value: string; hint: string }) {
  return (
    <div
      style={{
        background: 'rgba(252,250,242,0.82)',
        border: '1px solid rgba(26,24,21,0.08)',
        borderRadius: 18,
        padding: '13px 14px',
      }}
    >
      <div style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.14em', color: 'rgba(26,24,21,0.4)' }}>
        {label}
      </div>
      <div style={{ marginTop: 6, fontSize: 22, fontWeight: 700, color: 'var(--kajal)' }}>{value}</div>
      <div style={{ marginTop: 2, fontSize: 12, color: 'rgba(26,24,21,0.52)' }}>{hint}</div>
    </div>
  )
}

export function ProjectsView({ onProjectChange }: Props) {
  const [projects, setProjects] = useState<Project[]>([])
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null)
  const [projectSearch, setProjectSearch] = useState('')
  const [pages, setPages] = useState<WikiPageMeta[]>([])
  const [allCards, setAllCards] = useState<Record<string, MemoryCard[]>>({})
  const [sessions, setSessions] = useState<SessionMeta[]>([])
  const [loadingProjects, setLoadingProjects] = useState(false)
  const [loadingDetails, setLoadingDetails] = useState(false)

  const selectedProject = useMemo(
    () => projects.find(project => project.id === selectedProjectId) ?? null,
    [projects, selectedProjectId]
  )

  const fetchProjects = useCallback(async () => {
    setLoadingProjects(true)
    try {
      const res = await apiFetch(`/projects/${USER_ID}`)
      const data = await res.json()
      const nextProjects = Array.isArray(data.projects) ? data.projects : []
      setProjects(nextProjects)
      setSelectedProjectId(current =>
        current && nextProjects.some((project: Project) => project.id === current)
          ? current
          : nextProjects[0]?.id ?? null
      )
    } catch {
      setProjects([])
      setSelectedProjectId(null)
    } finally {
      setLoadingProjects(false)
    }
  }, [])

  const fetchProjectDetails = useCallback(async (projectId: string) => {
    setLoadingDetails(true)
    try {
      const [pagesRes, sessionsRes] = await Promise.all([
        apiFetch(`/wiki/${USER_ID}/${projectId}`),
        apiFetch(`/sessions/${USER_ID}/${projectId}`),
      ])

      const pagesData = pagesRes.ok ? await pagesRes.json() : { pages: [] }
      const sessionsData = sessionsRes.ok ? await sessionsRes.json() : { sessions: [] }
      const nextPages: WikiPageMeta[] = Array.isArray(pagesData.pages) ? pagesData.pages : []
      setPages(nextPages)
      setSessions(Array.isArray(sessionsData.sessions) ? sessionsData.sessions : [])

      const nextCards: Record<string, MemoryCard[]> = {}
      await Promise.all(
        nextPages.map(async page => {
          try {
            const pageRes = await apiFetch(`/wiki/${USER_ID}/${projectId}/${page.entity}`)
            if (!pageRes.ok) return
            const markdown = await pageRes.text()
            nextCards[page.entity] = parseWikiCards(markdown, page.entity as EntityType)
          } catch {
            nextCards[page.entity] = []
          }
        })
      )
      setAllCards(nextCards)
    } catch {
      setPages([])
      setSessions([])
      setAllCards({})
    } finally {
      setLoadingDetails(false)
    }
  }, [])

  useEffect(() => {
    fetchProjects()
  }, [fetchProjects])

  useEffect(() => {
    if (!selectedProjectId) {
      setPages([])
      setSessions([])
      setAllCards({})
      return
    }
    fetchProjectDetails(selectedProjectId)
  }, [selectedProjectId, fetchProjectDetails])

  const filteredProjects = useMemo(() => {
    const q = projectSearch.trim().toLowerCase()
    if (!q) return projects
    return projects.filter(project => project.name.toLowerCase().includes(q))
  }, [projects, projectSearch])

  const totalCards = useMemo(
    () => Object.values(allCards).reduce((sum, cards) => sum + cards.length, 0),
    [allCards]
  )

  const recentAvatars = useMemo(() => {
    const avatars = new Set<string>()
    for (const session of sessions.slice(0, 6)) {
      for (const avatar of session.avatars) avatars.add(avatar)
    }
    return Array.from(avatars)
  }, [sessions])

  const signalCards = useMemo(() => {
    const signals: Array<MemoryCard & { color: string; label: string }> = []
    for (const entity of ENTITY_ORDER) {
      const firstCard = (allCards[entity] ?? [])[0]
      if (!firstCard) continue
      signals.push({
        ...firstCard,
        color: ENTITY_META[entity].color,
        label: ENTITY_META[entity].label,
      })
    }
    return signals.slice(0, 4)
  }, [allCards])

  const knowledgeAnchors = useMemo(() => {
    return ENTITY_ORDER
      .map(entity => {
        const page = pages.find(candidate => candidate.entity === entity)
        if (!page) return null
        return {
          entity,
          label: ENTITY_META[entity].label,
          color: ENTITY_META[entity].color,
          preview: truncate(page.preview, 170),
          sizeChars: page.size_chars,
        }
      })
      .filter(Boolean) as Array<{
      entity: EntityType
      label: string
      color: string
      preview: string
      sizeChars: number
    }>
  }, [pages])

  useEffect(() => {
    if (!onProjectChange) return
    if (!selectedProject) {
      onProjectChange(null)
      return
    }
    onProjectChange({
      projectId: selectedProject.id,
      projectName: selectedProject.name,
      sessionIds: sessions.map(session => session.session_id),
      latestQuery: sessions[0]?.query ?? null,
      latestSessionAt: sessions[0]?.ts ?? null,
      sessionCount: selectedProject.session_count,
      wikiPageCount: pages.length,
      totalMemoryCards: totalCards,
      recentAvatars,
      highlights: knowledgeAnchors.slice(0, 3).map(anchor => ({
        label: anchor.label,
        text: anchor.preview,
        color: anchor.color,
      })),
    })
  }, [knowledgeAnchors, onProjectChange, pages.length, recentAvatars, selectedProject, sessions, totalCards])

  return (
    <div
      className="flex h-full overflow-hidden"
      style={{ background: 'linear-gradient(180deg, rgba(252,250,242,0.98) 0%, rgba(243,239,225,0.96) 100%)' }}
    >
      <aside
        style={{
          width: 286,
          borderRight: '1px solid rgba(26,24,21,0.08)',
          background: 'rgba(252,250,242,0.72)',
          display: 'flex',
          flexDirection: 'column',
          minHeight: 0,
        }}
      >
        <div style={{ padding: '18px 18px 14px', borderBottom: '1px solid rgba(26,24,21,0.08)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <div>
              <div style={{ fontSize: 10, letterSpacing: '0.16em', textTransform: 'uppercase', color: 'rgba(26,24,21,0.42)' }}>
                Projects
              </div>
              <div style={{ marginTop: 4, fontSize: 22, fontWeight: 700, color: 'var(--kajal)' }}>
                Project Flow
              </div>
            </div>
            <button
              onClick={fetchProjects}
              title="Refresh projects"
              style={{
                marginLeft: 'auto',
                width: 32,
                height: 32,
                borderRadius: 10,
                border: '1px solid rgba(26,24,21,0.08)',
                background: 'rgba(252,250,242,0.8)',
                color: 'rgba(26,24,21,0.55)',
                cursor: 'pointer',
              }}
            >
              <RefreshCw size={14} className={loadingProjects ? 'animate-spin' : ''} />
            </button>
          </div>

          <div
            style={{
              marginTop: 12,
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              padding: '10px 12px',
              borderRadius: 14,
              background: 'rgba(243,239,225,0.92)',
              border: '1px solid rgba(26,24,21,0.08)',
            }}
          >
            <Search size={14} style={{ color: 'rgba(26,24,21,0.35)' }} />
            <input
              value={projectSearch}
              onChange={event => setProjectSearch(event.target.value)}
              placeholder="Search projects…"
              style={{
                flex: 1,
                border: 'none',
                outline: 'none',
                background: 'transparent',
                color: 'var(--kajal)',
                fontSize: 13,
              }}
            />
          </div>
        </div>

        <div style={{ flex: 1, overflowY: 'auto', padding: 10 }}>
          {loadingProjects ? (
            <div style={{ padding: 12, fontSize: 13, color: 'rgba(26,24,21,0.45)' }}>Loading projects…</div>
          ) : filteredProjects.length === 0 ? (
            <div style={{ padding: 20, textAlign: 'center', color: 'rgba(26,24,21,0.45)' }}>
              <FolderOpen size={20} style={{ margin: '0 auto 10px' }} />
              No matching projects yet.
            </div>
          ) : (
            filteredProjects.map(project => {
              const selected = project.id === selectedProjectId
              return (
                <button
                  key={project.id}
                  type="button"
                  onClick={() => setSelectedProjectId(project.id)}
                  style={{
                    width: '100%',
                    textAlign: 'left',
                    marginBottom: 8,
                    padding: '14px 14px 12px',
                    borderRadius: 16,
                    border: selected ? '1px solid rgba(194,65,12,0.24)' : '1px solid rgba(26,24,21,0.08)',
                    background: selected
                      ? 'linear-gradient(135deg, rgba(194,65,12,0.08), rgba(252,250,242,0.94))'
                      : 'rgba(252,250,242,0.84)',
                    cursor: 'pointer',
                  }}
                >
                  <div style={{ fontSize: 14, fontWeight: 700, color: selected ? 'var(--marigold)' : 'var(--kajal)' }}>
                    {project.name}
                  </div>
                  <div style={{ marginTop: 6, display: 'flex', gap: 10, fontSize: 11, color: 'rgba(26,24,21,0.48)' }}>
                    <span>{project.session_count} session{project.session_count === 1 ? '' : 's'}</span>
                    <span>{formatDate(project.created_at)}</span>
                  </div>
                </button>
              )
            })
          )}
        </div>
      </aside>

      <section style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
        {!selectedProject ? (
          <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 32 }}>
            <div
              style={{
                maxWidth: 560,
                padding: 28,
                borderRadius: 24,
                background: 'rgba(252,250,242,0.82)',
                border: '1px solid rgba(26,24,21,0.08)',
                textAlign: 'center',
              }}
            >
              <FolderOpen size={26} style={{ margin: '0 auto 12px', color: 'var(--marigold)' }} />
              <div style={{ fontSize: 24, fontWeight: 700, color: 'var(--kajal)' }}>Select a project to continue the work</div>
              <p style={{ marginTop: 10, fontSize: 14, lineHeight: 1.6, color: 'rgba(26,24,21,0.55)' }}>
                This surface now centers the active trajectory of a project: recent asks, execution cues, and the anchors that should shape the next board.
              </p>
            </div>
          </div>
        ) : (
          <>
            <div style={{ padding: '18px 22px 14px', borderBottom: '1px solid rgba(26,24,21,0.08)' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                <div>
                  <div style={{ fontSize: 10, letterSpacing: '0.16em', textTransform: 'uppercase', color: 'rgba(26,24,21,0.42)' }}>
                    Active Project
                  </div>
                  <div style={{ marginTop: 4, fontSize: 24, fontWeight: 700, color: 'var(--kajal)' }}>{selectedProject.name}</div>
                </div>
                <button
                  type="button"
                  onClick={() => fetchProjectDetails(selectedProject.id)}
                  style={{
                    marginLeft: 'auto',
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 8,
                    padding: '9px 12px',
                    borderRadius: 12,
                    border: '1px solid rgba(26,24,21,0.08)',
                    background: 'rgba(252,250,242,0.82)',
                    color: 'rgba(26,24,21,0.6)',
                    cursor: 'pointer',
                    fontSize: 12,
                    fontWeight: 600,
                  }}
                >
                  <RefreshCw size={13} className={loadingDetails ? 'animate-spin' : ''} />
                  Refresh
                </button>
              </div>

              <div style={{ display: 'grid', gap: 12, marginTop: 16, gridTemplateColumns: 'repeat(4, minmax(0, 1fr))' }}>
                <StatTile label="Sessions" value={String(selectedProject.session_count)} hint="conversations tied to this project" />
                <StatTile label="Latest" value={formatDate(sessions[0]?.ts ?? selectedProject.created_at)} hint="most recent recorded movement" />
                <StatTile label="Anchors" value={String(knowledgeAnchors.length)} hint="wiki pages shaping execution" />
                <StatTile label="Signals" value={String(totalCards)} hint="memory cards available for recall" />
              </div>
            </div>

            <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: 22 }}>
              <div style={{ display: 'grid', gap: 18, gridTemplateColumns: 'minmax(0, 1.2fr) minmax(320px, 0.8fr)' }}>
                <div style={{ display: 'grid', gap: 18 }}>
                  <section
                    style={{
                      padding: 18,
                      borderRadius: 20,
                      background: 'rgba(252,250,242,0.82)',
                      border: '1px solid rgba(26,24,21,0.08)',
                    }}
                  >
                    <div style={{ fontSize: 12, fontWeight: 700, letterSpacing: '0.12em', textTransform: 'uppercase', color: 'rgba(26,24,21,0.45)' }}>
                      Current Trajectory
                    </div>
                    <div style={{ marginTop: 12, fontSize: 18, fontWeight: 700, color: 'var(--kajal)' }}>
                      {truncate(sessions[0]?.query, 140) || 'No recent prompt captured yet.'}
                    </div>
                    <div style={{ marginTop: 10, fontSize: 13, lineHeight: 1.6, color: 'rgba(26,24,21,0.58)' }}>
                      {knowledgeAnchors[0]?.preview ||
                        'This project has not accumulated enough structured context yet. Start with a crisp planning prompt and Narad will begin anchoring goals, decisions, and features here.'}
                    </div>

                    <div style={{ display: 'grid', gap: 12, marginTop: 16, gridTemplateColumns: 'repeat(3, minmax(0, 1fr))' }}>
                      <div style={{ padding: 14, borderRadius: 16, background: 'rgba(243,239,225,0.84)', border: '1px solid rgba(26,24,21,0.08)' }}>
                        <div style={{ fontSize: 10, letterSpacing: '0.14em', textTransform: 'uppercase', color: 'rgba(26,24,21,0.4)' }}>Latest session</div>
                        <div style={{ marginTop: 8, fontSize: 15, fontWeight: 700, color: 'var(--kajal)' }}>
                          {formatTime(sessions[0]?.ts ?? null)}
                        </div>
                      </div>
                      <div style={{ padding: 14, borderRadius: 16, background: 'rgba(243,239,225,0.84)', border: '1px solid rgba(26,24,21,0.08)' }}>
                        <div style={{ fontSize: 10, letterSpacing: '0.14em', textTransform: 'uppercase', color: 'rgba(26,24,21,0.4)' }}>Active avatars</div>
                        <div style={{ marginTop: 8, fontSize: 15, fontWeight: 700, color: 'var(--kajal)' }}>
                          {recentAvatars.length || 0}
                        </div>
                      </div>
                      <div style={{ padding: 14, borderRadius: 16, background: 'rgba(243,239,225,0.84)', border: '1px solid rgba(26,24,21,0.08)' }}>
                        <div style={{ fontSize: 10, letterSpacing: '0.14em', textTransform: 'uppercase', color: 'rgba(26,24,21,0.4)' }}>Project created</div>
                        <div style={{ marginTop: 8, fontSize: 15, fontWeight: 700, color: 'var(--kajal)' }}>
                          {formatDate(selectedProject.created_at)}
                        </div>
                      </div>
                    </div>
                  </section>

                  <section
                    style={{
                      padding: 18,
                      borderRadius: 20,
                      background: 'rgba(252,250,242,0.82)',
                      border: '1px solid rgba(26,24,21,0.08)',
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
                      <div style={{ fontSize: 12, fontWeight: 700, letterSpacing: '0.12em', textTransform: 'uppercase', color: 'rgba(26,24,21,0.45)' }}>
                        Recent Sessions
                      </div>
                      <div style={{ marginLeft: 'auto', fontSize: 11, color: 'rgba(26,24,21,0.45)' }}>
                        {sessions.length} linked
                      </div>
                    </div>

                    <div style={{ display: 'grid', gap: 10 }}>
                      {sessions.length === 0 ? (
                        <div style={{ padding: 18, borderRadius: 16, background: 'rgba(243,239,225,0.84)', border: '1px solid rgba(26,24,21,0.08)', color: 'rgba(26,24,21,0.48)' }}>
                          No session metadata recorded for this project yet.
                        </div>
                      ) : (
                        sessions.slice(0, 6).map(session => (
                          <article
                            key={session.session_id}
                            style={{
                              padding: '14px 16px',
                              borderRadius: 18,
                              background: 'rgba(252,250,242,0.84)',
                              border: '1px solid rgba(26,24,21,0.08)',
                            }}
                          >
                            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
                              <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--kajal)' }}>{formatTime(session.ts)}</span>
                              <span style={{ marginLeft: 'auto', fontSize: 11, color: 'rgba(26,24,21,0.42)' }}>{formatDuration(session.total_ms)}</span>
                            </div>
                            <div style={{ fontSize: 13, lineHeight: 1.58, color: 'rgba(26,24,21,0.66)' }}>
                              {truncate(session.query, 190) || 'No query preview available.'}
                            </div>
                            {session.avatars.length > 0 && (
                              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 10 }}>
                                {session.avatars.map(avatar => (
                                  <span
                                    key={avatar}
                                    style={{
                                      padding: '4px 8px',
                                      borderRadius: 999,
                                      background: 'rgba(243,239,225,0.9)',
                                      border: '1px solid rgba(26,24,21,0.08)',
                                      fontSize: 11,
                                      color: 'rgba(26,24,21,0.62)',
                                    }}
                                  >
                                    {avatar}
                                  </span>
                                ))}
                              </div>
                            )}
                          </article>
                        ))
                      )}
                    </div>
                  </section>
                </div>

                <div style={{ display: 'grid', gap: 18 }}>
                  <section
                    style={{
                      padding: 18,
                      borderRadius: 20,
                      background: 'rgba(252,250,242,0.82)',
                      border: '1px solid rgba(26,24,21,0.08)',
                    }}
                  >
                    <div style={{ fontSize: 12, fontWeight: 700, letterSpacing: '0.12em', textTransform: 'uppercase', color: 'rgba(26,24,21,0.45)' }}>
                      Knowledge Anchors
                    </div>
                    <div style={{ display: 'grid', gap: 10, marginTop: 12 }}>
                      {knowledgeAnchors.length === 0 ? (
                        <div style={{ padding: 16, borderRadius: 16, background: 'rgba(243,239,225,0.84)', border: '1px solid rgba(26,24,21,0.08)', color: 'rgba(26,24,21,0.48)' }}>
                          Compiled wiki anchors have not been generated yet for this project.
                        </div>
                      ) : (
                        knowledgeAnchors.map(anchor => (
                          <article
                            key={anchor.entity}
                            style={{
                              padding: '14px 15px',
                              borderRadius: 18,
                              background: 'rgba(252,250,242,0.84)',
                              border: '1px solid rgba(26,24,21,0.08)',
                              boxShadow: `inset 3px 0 0 ${anchor.color}`,
                            }}
                          >
                            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                              <span style={{ fontSize: 12, fontWeight: 700, color: anchor.color }}>{anchor.label}</span>
                              <span style={{ marginLeft: 'auto', fontSize: 10, color: 'rgba(26,24,21,0.4)' }}>{anchor.sizeChars} chars</span>
                            </div>
                            <div style={{ marginTop: 8, fontSize: 12, lineHeight: 1.55, color: 'rgba(26,24,21,0.62)' }}>
                              {anchor.preview}
                            </div>
                          </article>
                        ))
                      )}
                    </div>
                  </section>

                  <section
                    style={{
                      padding: 18,
                      borderRadius: 20,
                      background: 'rgba(252,250,242,0.82)',
                      border: '1px solid rgba(26,24,21,0.08)',
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                      <div style={{ fontSize: 12, fontWeight: 700, letterSpacing: '0.12em', textTransform: 'uppercase', color: 'rgba(26,24,21,0.45)' }}>
                        Execution Signals
                      </div>
                      <Clock3 size={14} style={{ marginLeft: 'auto', color: 'rgba(26,24,21,0.35)' }} />
                    </div>

                    <div style={{ display: 'grid', gap: 10, marginTop: 12 }}>
                      {signalCards.length === 0 ? (
                        <div style={{ padding: 16, borderRadius: 16, background: 'rgba(243,239,225,0.84)', border: '1px solid rgba(26,24,21,0.08)', color: 'rgba(26,24,21,0.48)' }}>
                          As Narad learns from this project, the strongest goal, decision, and feature signals will show up here.
                        </div>
                      ) : (
                        signalCards.map(card => (
                          <article
                            key={card.id}
                            style={{
                              padding: '14px 15px',
                              borderRadius: 18,
                              background: 'rgba(252,250,242,0.84)',
                              border: '1px solid rgba(26,24,21,0.08)',
                            }}
                          >
                            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                              <span
                                style={{
                                  fontSize: 10,
                                  fontWeight: 700,
                                  letterSpacing: '0.12em',
                                  textTransform: 'uppercase',
                                  color: card.color,
                                }}
                              >
                                {card.label}
                              </span>
                              <span style={{ marginLeft: 'auto', fontSize: 10, color: 'rgba(26,24,21,0.38)' }}>{card.avatar}</span>
                            </div>
                            <div style={{ fontSize: 12, lineHeight: 1.58, color: 'var(--kajal)' }}>{truncate(card.task, 150)}</div>
                            {card.summary && (
                              <div style={{ marginTop: 8, fontSize: 11, lineHeight: 1.52, color: 'rgba(26,24,21,0.52)' }}>
                                {truncate(card.summary, 130)}
                              </div>
                            )}
                          </article>
                        ))
                      )}
                    </div>
                  </section>
                </div>
              </div>
            </div>
          </>
        )}
      </section>
    </div>
  )
}
