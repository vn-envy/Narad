import type { CSSProperties } from 'react'
import { FolderOpen, RefreshCw, Search } from 'lucide-react'
import type { ProjectListItem, ProjectWorkspace } from '@/lib/api'

function formatDateTime(value: string | null | undefined): string {
  if (!value) return '—'
  try {
    return new Date(value).toLocaleString('en-US', {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return value
  }
}

function truncate(text: string | null | undefined, limit = 160): string {
  const value = (text ?? '').trim()
  if (!value) return '—'
  return value.length > limit ? `${value.slice(0, limit - 1)}…` : value
}

interface Props {
  projects: ProjectListItem[]
  selectedProjectId: string | null
  workspace: ProjectWorkspace | null
  search: string
  loadingProjects: boolean
  loadingWorkspace: boolean
  onSearchChange: (value: string) => void
  onSelectProject: (projectId: string) => void
  onRefreshProjects: () => void
  onRefreshWorkspace: () => void
}

export function ProjectHomePanel({
  projects,
  selectedProjectId,
  workspace,
  search,
  loadingProjects,
  loadingWorkspace,
  onSearchChange,
  onSelectProject,
  onRefreshProjects,
  onRefreshWorkspace,
}: Props) {
  return (
    <div className="flex h-full min-h-0 overflow-hidden" style={{ background: 'linear-gradient(180deg, rgba(252,250,242,0.98) 0%, rgba(245,240,227,0.96) 100%)' }}>
      <aside
        style={{
          width: 300,
          borderRight: '1px solid rgba(26,24,21,0.08)',
          display: 'flex',
          flexDirection: 'column',
          minHeight: 0,
          background: 'rgba(252,250,242,0.68)',
        }}
      >
        <div style={{ padding: '18px 18px 14px', borderBottom: '1px solid rgba(26,24,21,0.08)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <div>
              <div style={{ fontSize: 10, letterSpacing: '0.16em', textTransform: 'uppercase', color: 'rgba(26,24,21,0.42)' }}>Workspace projects</div>
              <div style={{ marginTop: 4, fontSize: 22, fontWeight: 700, color: 'var(--kajal)' }}>Project Home</div>
            </div>
            <button
              type="button"
              onClick={onRefreshProjects}
              title="Refresh projects"
              style={iconButtonStyle}
            >
              <RefreshCw size={14} className={loadingProjects ? 'animate-spin' : ''} />
            </button>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 12, padding: '10px 12px', borderRadius: 14, border: '1px solid rgba(26,24,21,0.08)', background: 'rgba(243,239,225,0.9)' }}>
            <Search size={14} style={{ color: 'rgba(26,24,21,0.35)' }} />
            <input
              value={search}
              onChange={event => onSearchChange(event.target.value)}
              placeholder="Search projects"
              style={{ flex: 1, border: 'none', outline: 'none', background: 'transparent', fontSize: 13, color: 'var(--kajal)' }}
            />
          </div>
        </div>

        <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: 10 }}>
          {loadingProjects ? (
            <div style={{ padding: 12, color: 'rgba(26,24,21,0.46)', fontSize: 13 }}>Loading projects…</div>
          ) : projects.length === 0 ? (
            <div style={{ padding: 18, textAlign: 'center', color: 'rgba(26,24,21,0.46)' }}>
              <FolderOpen size={18} style={{ margin: '0 auto 8px' }} />
              No projects yet.
            </div>
          ) : (
            projects.map(project => {
              const selected = project.id === selectedProjectId
              return (
                <button
                  key={project.id}
                  type="button"
                  onClick={() => onSelectProject(project.id)}
                  style={{
                    width: '100%',
                    textAlign: 'left',
                    marginBottom: 8,
                    padding: '13px 14px',
                    borderRadius: 16,
                    border: selected ? '1px solid rgba(194,65,12,0.24)' : '1px solid rgba(26,24,21,0.08)',
                    background: selected ? 'linear-gradient(135deg, rgba(194,65,12,0.08), rgba(252,250,242,0.94))' : 'rgba(252,250,242,0.84)',
                    cursor: 'pointer',
                  }}
                >
                  <div style={{ fontSize: 14, fontWeight: 700, color: selected ? 'var(--marigold)' : 'var(--kajal)' }}>{project.name}</div>
                  <div style={{ marginTop: 6, fontSize: 11, color: 'rgba(26,24,21,0.46)' }}>
                    {(project.workspace_label || project.workspace_root || 'workspace').toString()}
                  </div>
                  <div style={{ marginTop: 6, display: 'flex', gap: 10, fontSize: 11, color: 'rgba(26,24,21,0.46)' }}>
                    <span>{project.session_count} sessions</span>
                    <span>{formatDateTime(project.last_activity_at ?? project.created_at)}</span>
                  </div>
                </button>
              )
            })
          )}
        </div>
      </aside>

      <section style={{ flex: 1, minWidth: 0, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
        {!workspace ? (
          <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 26 }}>
            <div style={{ maxWidth: 520, padding: 24, borderRadius: 20, border: '1px solid rgba(26,24,21,0.08)', background: 'rgba(252,250,242,0.88)', textAlign: 'center' }}>
              <FolderOpen size={24} style={{ margin: '0 auto 10px', color: 'var(--marigold)' }} />
              <div style={{ fontSize: 22, fontWeight: 700, color: 'var(--kajal)' }}>Pick a project to resume real work</div>
              <p style={{ marginTop: 10, fontSize: 14, lineHeight: 1.6, color: 'rgba(26,24,21,0.56)' }}>
                Karma now treats a project as a workspace and execution hub, not as a wall of memory cards.
              </p>
            </div>
          </div>
        ) : (
          <>
            <div style={{ padding: '18px 20px 14px', borderBottom: '1px solid rgba(26,24,21,0.08)' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                <div>
                  <div style={{ fontSize: 10, letterSpacing: '0.16em', textTransform: 'uppercase', color: 'rgba(26,24,21,0.42)' }}>Selected workspace</div>
                  <div style={{ marginTop: 4, fontSize: 24, fontWeight: 700, color: 'var(--kajal)' }}>{workspace.project.name}</div>
                </div>
                <button type="button" onClick={onRefreshWorkspace} style={refreshButtonStyle}>
                  <RefreshCw size={13} className={loadingWorkspace ? 'animate-spin' : ''} />
                  Refresh
                </button>
              </div>
              <div style={{ marginTop: 8, fontSize: 13, color: 'rgba(26,24,21,0.5)' }}>
                {(workspace.project.workspace_root || workspace.project.workspace_label || 'workspace').toString()}
              </div>
            </div>

            <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: 20, display: 'grid', gap: 16 }}>
              <section style={cardStyle}>
                <div style={sectionLabelStyle}>Current goal</div>
                <div style={{ marginTop: 8, fontSize: 18, fontWeight: 700, color: 'var(--kajal)' }}>
                  {truncate(workspace.project.current_goal, 180)}
                </div>
                <div style={{ marginTop: 10, fontSize: 13, lineHeight: 1.6, color: 'rgba(26,24,21,0.56)' }}>
                  {workspace.active_session?.query
                    ? truncate(workspace.active_session.query, 220)
                    : 'No active execution thread is linked right now.'}
                </div>
              </section>

              <div style={{ display: 'grid', gap: 14, gridTemplateColumns: 'repeat(2, minmax(0, 1fr))' }}>
                <section style={cardStyle}>
                  <div style={sectionLabelStyle}>Session state</div>
                  <div style={{ display: 'grid', gap: 8, marginTop: 10, fontSize: 13, color: 'rgba(26,24,21,0.6)' }}>
                    <div><strong style={{ color: 'var(--kajal)' }}>Active:</strong> {workspace.project.active_session_id?.slice(0, 8) ?? '—'}</div>
                    <div><strong style={{ color: 'var(--kajal)' }}>Last activity:</strong> {formatDateTime(workspace.project.last_activity_at)}</div>
                    <div><strong style={{ color: 'var(--kajal)' }}>Sessions:</strong> {workspace.project.session_count}</div>
                  </div>
                </section>

                <section style={cardStyle}>
                  <div style={sectionLabelStyle}>Task shape</div>
                  <div style={{ display: 'grid', gap: 8, marginTop: 10, fontSize: 13, color: 'rgba(26,24,21,0.6)' }}>
                    <div><strong style={{ color: 'var(--kajal)' }}>Live now:</strong> {workspace.task_summary.now.length}</div>
                    <div><strong style={{ color: 'var(--kajal)' }}>Blocked:</strong> {workspace.task_summary.blocked.length}</div>
                    <div><strong style={{ color: 'var(--kajal)' }}>Done recently:</strong> {workspace.task_summary.recent_done.length}</div>
                  </div>
                </section>
              </div>

              <section style={cardStyle}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <div style={sectionLabelStyle}>Recent sessions</div>
                  <div style={{ marginLeft: 'auto', fontSize: 11, color: 'rgba(26,24,21,0.42)' }}>{workspace.recent_sessions.length} linked</div>
                </div>
                <div style={{ display: 'grid', gap: 10, marginTop: 12 }}>
                  {workspace.recent_sessions.length === 0 ? (
                    <div style={emptyStyle}>No linked sessions yet.</div>
                  ) : (
                    workspace.recent_sessions.slice(0, 5).map(session => (
                      <article key={session.session_id} style={subCardStyle}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                          <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--kajal)' }}>{truncate(session.query, 100)}</div>
                          <div style={{ marginLeft: 'auto', fontSize: 11, color: 'rgba(26,24,21,0.4)' }}>{formatDateTime(session.ts)}</div>
                        </div>
                        <div style={{ marginTop: 6, fontSize: 11, color: 'rgba(26,24,21,0.46)' }}>
                          {session.session_id.slice(0, 8)} · {session.avatars.join(' · ') || 'No avatar summary'}
                        </div>
                      </article>
                    ))
                  )}
                </div>
              </section>

              <section style={cardStyle}>
                <div style={sectionLabelStyle}>Memory anchors</div>
                <div style={{ display: 'grid', gap: 10, marginTop: 12 }}>
                  {workspace.memory_anchors.length === 0 ? (
                    <div style={emptyStyle}>No memory anchors yet.</div>
                  ) : (
                    workspace.memory_anchors.slice(0, 4).map(anchor => (
                      <article key={anchor.entity} style={subCardStyle}>
                        <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--kajal)' }}>{anchor.entity}</div>
                        <div style={{ marginTop: 6, fontSize: 12, lineHeight: 1.55, color: 'rgba(26,24,21,0.56)' }}>
                          {truncate(anchor.preview, 180)}
                        </div>
                      </article>
                    ))
                  )}
                </div>
              </section>
            </div>
          </>
        )}
      </section>
    </div>
  )
}

const iconButtonStyle: CSSProperties = {
  marginLeft: 'auto',
  width: 32,
  height: 32,
  borderRadius: 10,
  border: '1px solid rgba(26,24,21,0.08)',
  background: 'rgba(252,250,242,0.84)',
  color: 'rgba(26,24,21,0.56)',
  cursor: 'pointer',
}

const refreshButtonStyle: CSSProperties = {
  marginLeft: 'auto',
  display: 'inline-flex',
  alignItems: 'center',
  gap: 8,
  padding: '9px 12px',
  borderRadius: 12,
  border: '1px solid rgba(26,24,21,0.08)',
  background: 'rgba(252,250,242,0.84)',
  color: 'rgba(26,24,21,0.62)',
  cursor: 'pointer',
  fontSize: 12,
  fontWeight: 700,
}

const sectionLabelStyle: CSSProperties = {
  fontSize: 11,
  letterSpacing: '0.14em',
  textTransform: 'uppercase',
  color: 'rgba(26,24,21,0.44)',
  fontWeight: 700,
}

const cardStyle: CSSProperties = {
  padding: 16,
  borderRadius: 18,
  border: '1px solid rgba(26,24,21,0.08)',
  background: 'rgba(252,250,242,0.86)',
}

const subCardStyle: CSSProperties = {
  padding: '12px 13px',
  borderRadius: 14,
  border: '1px solid rgba(26,24,21,0.08)',
  background: 'rgba(252,250,242,0.94)',
}

const emptyStyle: CSSProperties = {
  padding: 14,
  borderRadius: 14,
  border: '1px dashed rgba(26,24,21,0.12)',
  color: 'rgba(26,24,21,0.38)',
  fontSize: 12,
}
