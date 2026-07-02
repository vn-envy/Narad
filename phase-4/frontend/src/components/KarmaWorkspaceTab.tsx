import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { SessionInfo } from '@/hooks/useAvatara'
import { apiFetch, apiJson } from '@/lib/api'
import type { ProjectExecution, ProjectListItem, ProjectStateStore, ProjectTask, ProjectWorkspace } from '@/lib/api'
import { BacklogBoardPanel } from './BacklogBoardPanel'
import { ExecutionPanel } from './ExecutionPanel'
import { KarmaPanel } from './KarmaPanel'
import { ProjectHomePanel } from './ProjectHomePanel'

interface Props {
  userId: string
  currentSession: SessionInfo | null
  streaming: boolean
}

interface KarmaRuntimeEventDetail {
  type: string
  data: Record<string, unknown>
  ts: number
}

function truncate(text: string | null | undefined, limit = 120): string {
  const value = (text ?? '').trim()
  if (!value) return '—'
  return value.length > limit ? `${value.slice(0, limit - 1)}…` : value
}

function buildTaskSummary(tasks: ProjectTask[]) {
  const byStatus: Record<string, number> = {}
  for (const task of tasks) {
    byStatus[task.status] = (byStatus[task.status] ?? 0) + 1
  }
  return {
    total: tasks.length,
    by_status: byStatus,
    now: tasks.filter(task => task.status === 'in_progress' || task.status === 'review').slice(0, 5),
    next: tasks.filter(task => task.status === 'todo').slice(0, 5),
    blocked: tasks.filter(task => task.status === 'blocked').slice(0, 5),
    recent_done: tasks.filter(task => task.status === 'done').slice(0, 5),
  }
}

function rebuildStoreState(
  previous: ProjectStateStore,
  nextTasks: ProjectTask[],
  changedTask?: ProjectTask,
): ProjectStateStore {
  const summary = buildTaskSummary(nextTasks)
  const workspace = previous.workspace
    ? {
        ...previous.workspace,
        task_summary: summary,
        project: {
          ...previous.workspace.project,
          current_goal:
            nextTasks.find(task => task.kind === 'goal')?.title
            ?? summary.now[0]?.title
            ?? summary.next[0]?.title
            ?? previous.workspace.project.current_goal,
        },
      }
    : null
  const execution = previous.execution
    ? {
        ...previous.execution,
        current_goal:
          workspace?.project.current_goal
          ?? previous.execution.current_goal,
        now: summary.now,
        next: summary.next,
        blocked: summary.blocked,
        recent_done: summary.recent_done,
        artifacts: [
          ...summary.now.flatMap(task => task.artifact_refs ?? []),
          ...summary.recent_done.flatMap(task => task.artifact_refs ?? []),
        ].slice(0, 8),
      }
    : null

  const lastLoadedAt = changedTask ? Date.now() : previous.lastLoadedAt

  return {
    ...previous,
    workspace,
    execution,
    tasks: nextTasks,
    refreshing: false,
    loading: false,
    error: null,
    lastLoadedAt,
  }
}

function mergeTask(tasks: ProjectTask[], updated: ProjectTask): ProjectTask[] {
  const exists = tasks.some(task => task.task_id === updated.task_id)
  const next = exists
    ? tasks.map(task => task.task_id === updated.task_id ? updated : task)
    : [updated, ...tasks]
  return next.sort((left, right) => (right.updated_at || '').localeCompare(left.updated_at || ''))
}

function eventTouchesProject(
  detail: KarmaRuntimeEventDetail,
  selectedProjectId: string,
  workspace: ProjectWorkspace | null,
  currentSessionId: string | null,
): boolean {
  const data = detail.data ?? {}
  const projectId = typeof data.project_id === 'string' ? data.project_id : null
  const sessionId = typeof data.session_id === 'string' ? data.session_id : null
  if (projectId && projectId === selectedProjectId) return true
  if (!workspace) return currentSessionId != null && sessionId === currentSessionId
  if (sessionId && workspace.project.active_session_id === sessionId) return true
  if (sessionId && workspace.recent_sessions.some(session => session.session_id === sessionId)) return true
  if (currentSessionId && sessionId === currentSessionId && workspace.project.active_session_id === currentSessionId) return true
  return false
}

export function KarmaWorkspaceTab({ userId, currentSession, streaming }: Props) {
  const [projects, setProjects] = useState<ProjectListItem[]>([])
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null)
  const [projectSearch, setProjectSearch] = useState('')
  const [loadingProjects, setLoadingProjects] = useState(false)
  const [projectState, setProjectState] = useState<Record<string, ProjectStateStore>>({})
  const refreshTimerRef = useRef<number | null>(null)
  const projectsInFlightRef = useRef(false)
  const surfaceInFlightRef = useRef<Record<string, boolean>>({})
  const lastProjectsLoadRef = useRef(0)
  const lastSurfaceLoadRef = useRef<Record<string, number>>({})
  const previousStreamingRef = useRef(streaming)

  const selectedState = selectedProjectId ? projectState[selectedProjectId] ?? null : null

  const filteredProjects = useMemo(() => {
    const query = projectSearch.trim().toLowerCase()
    if (!query) return projects
    return projects.filter(project => {
      return (
        project.name.toLowerCase().includes(query)
        || String(project.workspace_label ?? '').toLowerCase().includes(query)
        || String(project.workspace_root ?? '').toLowerCase().includes(query)
      )
    })
  }, [projectSearch, projects])

  const loadProjects = useCallback(async (preferredSessionId?: string | null, force = false) => {
    const now = Date.now()
    if (!force && projectsInFlightRef.current) return
    if (!force && now - lastProjectsLoadRef.current < 1200) return
    projectsInFlightRef.current = true
    lastProjectsLoadRef.current = now
    setLoadingProjects(true)
    try {
      const data = await apiJson<{ projects?: ProjectListItem[] }>(`/projects/${userId}`)
      const nextProjects = Array.isArray(data.projects) ? data.projects : []
      setProjects(nextProjects)
      setSelectedProjectId(current => {
        if (preferredSessionId) {
          const matched = nextProjects.find(project => project.active_session_id === preferredSessionId)
          if (matched) return matched.id
        }
        if (current && nextProjects.some(project => project.id === current)) return current
        return nextProjects[0]?.id ?? null
      })
    } catch {
      // Keep the last good project list during transient backend refresh failures.
    } finally {
      projectsInFlightRef.current = false
      setLoadingProjects(false)
    }
  }, [userId])

  const loadProjectSurfaces = useCallback(async (projectId: string, mode: 'load' | 'refresh' = 'refresh', force = false) => {
    const now = Date.now()
    if (!force && surfaceInFlightRef.current[projectId]) return
    if (!force && mode === 'refresh' && now - (lastSurfaceLoadRef.current[projectId] ?? 0) < 1200) return
    surfaceInFlightRef.current[projectId] = true
    lastSurfaceLoadRef.current[projectId] = now
    setProjectState(current => ({
      ...current,
      [projectId]: {
        projectId,
        workspace: current[projectId]?.workspace ?? null,
        execution: current[projectId]?.execution ?? null,
        tasks: current[projectId]?.tasks ?? [],
        loading: mode === 'load',
        refreshing: mode !== 'load',
        lastLoadedAt: current[projectId]?.lastLoadedAt ?? null,
        error: null,
      },
    }))
    try {
      const [workspace, execution, taskPayload] = await Promise.all([
        apiJson<ProjectWorkspace>(`/projects/${userId}/${projectId}/workspace`),
        apiJson<ProjectExecution>(`/projects/${userId}/${projectId}/execution`),
        apiJson<{ tasks?: ProjectTask[] }>(`/projects/${userId}/${projectId}/tasks`),
      ])
      const tasks = Array.isArray(taskPayload.tasks) ? taskPayload.tasks : []
      setProjectState(current => ({
        ...current,
        [projectId]: {
          projectId,
          workspace,
          execution,
          tasks,
          loading: false,
          refreshing: false,
          lastLoadedAt: Date.now(),
          error: null,
        },
      }))
    } catch (error) {
      setProjectState(current => ({
        ...current,
        [projectId]: {
          projectId,
          workspace: current[projectId]?.workspace ?? null,
          execution: current[projectId]?.execution ?? null,
          tasks: current[projectId]?.tasks ?? [],
          loading: false,
          refreshing: false,
          lastLoadedAt: current[projectId]?.lastLoadedAt ?? null,
          error:
            current[projectId]?.workspace || current[projectId]?.execution || (current[projectId]?.tasks?.length ?? 0) > 0
              ? null
              : error instanceof Error
                ? error.message
                : 'Unable to load project state',
        },
      }))
    } finally {
      surfaceInFlightRef.current[projectId] = false
    }
  }, [userId])

  const scheduleRefresh = useCallback((projectId: string, delayMs = 350) => {
    if (refreshTimerRef.current) {
      window.clearTimeout(refreshTimerRef.current)
    }
    refreshTimerRef.current = window.setTimeout(() => {
      loadProjectSurfaces(projectId, 'refresh')
      refreshTimerRef.current = null
    }, delayMs)
  }, [loadProjectSurfaces])

  useEffect(() => {
    loadProjects(currentSession?.sessionId ?? null)
  }, [currentSession?.sessionId, loadProjects])

  useEffect(() => {
    if (!selectedProjectId) return
    const cached = projectState[selectedProjectId]
    if (!cached || (!cached.workspace && !cached.loading)) {
      loadProjectSurfaces(selectedProjectId, 'load')
    }
  }, [loadProjectSurfaces, projectState, selectedProjectId])

  useEffect(() => {
    function onFocus() {
      if (selectedProjectId) {
        loadProjectSurfaces(selectedProjectId, 'refresh', true)
      }
    }
    window.addEventListener('focus', onFocus)
    return () => window.removeEventListener('focus', onFocus)
  }, [loadProjectSurfaces, selectedProjectId])

  useEffect(() => {
    if (!selectedProjectId) return
    const liveSessionId = selectedState?.workspace?.project.active_session_id ?? currentSession?.sessionId ?? null
    if (!liveSessionId) return
    const interval = window.setInterval(() => {
      if (!document.hidden) {
        loadProjectSurfaces(selectedProjectId, 'refresh')
      }
    }, 15000)
    return () => window.clearInterval(interval)
  }, [currentSession?.sessionId, loadProjectSurfaces, selectedProjectId, selectedState?.workspace?.project.active_session_id, streaming])

  useEffect(() => {
    function onKarmaRuntimeEvent(rawEvent: Event) {
      const detail = (rawEvent as CustomEvent<KarmaRuntimeEventDetail>).detail
      if (!detail || !selectedProjectId) return
      const currentSessionId = currentSession?.sessionId ?? null
      const workspace = projectState[selectedProjectId]?.workspace ?? null
      if (!eventTouchesProject(detail, selectedProjectId, workspace, currentSessionId)) return
      scheduleRefresh(selectedProjectId)
      if (detail.type === 'done' || detail.type === 'project_state_changed') {
        loadProjects(currentSessionId)
      }
    }
    window.addEventListener('narad:karma-event', onKarmaRuntimeEvent as EventListener)
    return () => window.removeEventListener('narad:karma-event', onKarmaRuntimeEvent as EventListener)
  }, [currentSession?.sessionId, loadProjects, projectState, scheduleRefresh, selectedProjectId])

  useEffect(() => {
    if (previousStreamingRef.current && !streaming && selectedProjectId) {
      scheduleRefresh(selectedProjectId, 120)
    }
    previousStreamingRef.current = streaming
  }, [scheduleRefresh, selectedProjectId, streaming])

  const handleTaskStatusChange = useCallback(async (taskId: string, action: 'resume' | 'block' | 'complete') => {
    if (!selectedProjectId || !selectedState) return
    try {
      const response = await apiJson<{ task?: ProjectTask }>(`/tasks/${taskId}/${action}`, { method: 'POST' })
      const updatedTask = response.task
      if (updatedTask) {
        setProjectState(current => {
          const base = current[selectedProjectId]
          if (!base) return current
          const nextTasks = mergeTask(base.tasks, updatedTask)
          return {
            ...current,
            [selectedProjectId]: rebuildStoreState(base, nextTasks, updatedTask),
          }
        })
      }
      scheduleRefresh(selectedProjectId, 180)
    } catch {
      scheduleRefresh(selectedProjectId, 80)
    }
  }, [scheduleRefresh, selectedProjectId, selectedState])

  const handleTaskCreate = useCallback(async (draft: {
    title: string
    description: string
    owner: string | null
    priority: string
    kind: string
  }) => {
    if (!selectedProjectId) return
    const response = await apiJson<{ task?: ProjectTask }>(`/projects/${userId}/${selectedProjectId}/tasks`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        ...draft,
        source_session_id: currentSession?.sessionId ?? selectedState?.workspace?.project.active_session_id ?? null,
      }),
    })
    const createdTask = response.task
    if (createdTask) {
      setProjectState(current => {
        const base = current[selectedProjectId]
        if (!base) return current
        const nextTasks = mergeTask(base.tasks, createdTask)
        return {
          ...current,
          [selectedProjectId]: rebuildStoreState(base, nextTasks, createdTask),
        }
      })
    }
    scheduleRefresh(selectedProjectId, 180)
  }, [currentSession?.sessionId, scheduleRefresh, selectedProjectId, selectedState?.workspace?.project.active_session_id, userId])

  const handleTaskPatch = useCallback(async (taskId: string, patch: Partial<ProjectTask>) => {
    if (!selectedProjectId) return
    const payload = {
      title: patch.title,
      description: patch.description,
      status: patch.status,
      priority: patch.priority,
      owner: patch.owner,
      kind: patch.kind,
      blocked_by: patch.blocked_by,
      artifact_refs: patch.artifact_refs,
      sort_order: patch.sort_order,
    }
    const response = await apiJson<{ task?: ProjectTask }>(`/tasks/${taskId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    const updatedTask = response.task
    if (updatedTask) {
      setProjectState(current => {
        const base = current[selectedProjectId]
        if (!base) return current
        const nextTasks = mergeTask(base.tasks, updatedTask)
        return {
          ...current,
          [selectedProjectId]: rebuildStoreState(base, nextTasks, updatedTask),
        }
      })
    }
    scheduleRefresh(selectedProjectId, 180)
  }, [scheduleRefresh, selectedProjectId])

  const currentFocus = selectedState?.execution?.now[0]?.title
    ?? selectedState?.execution?.next[0]?.title
    ?? selectedState?.workspace?.project.current_goal
    ?? selectedState?.workspace?.active_session?.query
    ?? null

  const backlogCount = selectedState?.tasks.filter(task => task.status !== 'done').length ?? 0
  const blockedCount = selectedState?.execution?.blocked.length ?? 0

  return (
    <div
      style={{
        height: '100%',
        minHeight: 0,
        overflowX: 'hidden',
        overflowY: 'auto',
        WebkitOverflowScrolling: 'touch',
        touchAction: 'pan-y',
        padding: 18,
      }}
    >
      <div style={{ padding: '14px 16px', borderRadius: 18, border: '1px solid rgba(26,24,21,0.08)', background: 'linear-gradient(135deg, rgba(252,250,242,0.96) 0%, rgba(244,239,226,0.92) 100%)' }}>
        <div style={{ display: 'grid', gap: 12, gridTemplateColumns: 'repeat(4, minmax(0, 1fr))' }}>
          <SummaryTile
            label="Active project"
            value={selectedState?.workspace?.project.name ?? 'No project selected'}
            hint={selectedState?.workspace?.project.workspace_label ?? selectedState?.workspace?.project.workspace_root ?? 'Choose a project to load its workspace state.'}
          />
          <SummaryTile
            label="Current focus"
            value={selectedState?.execution?.now.length ? `${selectedState.execution.now.length} live` : 'Execution ready'}
            hint={truncate(currentFocus, 110)}
          />
          <SummaryTile
            label="Blockers"
            value={String(blockedCount)}
            hint={blockedCount > 0 ? 'Blocked work needs intervention.' : 'No explicit blockers right now.'}
          />
          <SummaryTile
            label="Backlog health"
            value={String(backlogCount)}
            hint={`${selectedState?.tasks.filter(task => task.status === 'done').length ?? 0} done · ${selectedState?.tasks.filter(task => task.status === 'todo').length ?? 0} ready next`}
          />
        </div>
      </div>

      <div style={{ display: 'grid', gap: 16, marginTop: 16 }} className="xl:grid-cols-[320px_minmax(0,1fr)]">
        <section style={{ minHeight: 0, overflow: 'hidden', borderRadius: 22, border: '1px solid rgba(26,24,21,0.08)', background: 'rgba(252,250,242,0.82)' }}>
          <ProjectHomePanel
            projects={filteredProjects}
            selectedProjectId={selectedProjectId}
            workspace={selectedState?.workspace ?? null}
            search={projectSearch}
            loadingProjects={loadingProjects}
            loadingWorkspace={Boolean(selectedState?.loading || selectedState?.refreshing)}
            onSearchChange={setProjectSearch}
            onSelectProject={setSelectedProjectId}
            onRefreshProjects={() => loadProjects(currentSession?.sessionId ?? null, true)}
            onRefreshWorkspace={() => selectedProjectId ? loadProjectSurfaces(selectedProjectId, 'refresh', true) : Promise.resolve()}
          />
        </section>

        <div style={{ display: 'grid', gap: 16, minWidth: 0 }}>
          {selectedState?.error && (
            <div style={{ padding: '12px 14px', borderRadius: 16, border: '1px solid rgba(154,52,18,0.14)', background: 'rgba(154,52,18,0.06)', color: 'var(--kesari)', fontSize: 13 }}>
              {selectedState.error}
            </div>
          )}

          <section style={{ minHeight: 0, overflow: 'hidden', borderRadius: 22, border: '1px solid rgba(26,24,21,0.08)', background: 'rgba(252,250,242,0.82)' }}>
            <ExecutionPanel execution={selectedState?.execution ?? null} onTaskStatusChange={handleTaskStatusChange} />
          </section>

          <section style={{ minHeight: 0, overflow: 'hidden', borderRadius: 22, border: '1px solid rgba(26,24,21,0.08)', background: 'rgba(252,250,242,0.82)' }}>
            <BacklogBoardPanel
              tasks={selectedState?.tasks ?? []}
              activeSessionId={selectedState?.workspace?.project.active_session_id ?? currentSession?.sessionId ?? null}
              onTaskStatusChange={handleTaskStatusChange}
              onTaskCreate={handleTaskCreate}
              onTaskPatch={handleTaskPatch}
            />
          </section>

          <section style={{ minHeight: 0, overflow: 'hidden', borderRadius: 22, border: '1px solid rgba(26,24,21,0.08)', background: 'rgba(252,250,242,0.82)' }}>
            <KarmaPanel userId={userId} compact />
          </section>
        </div>
      </div>
    </div>
  )
}

function SummaryTile({ label, value, hint }: { label: string; value: string; hint: string }) {
  return (
    <div style={{ padding: '14px 15px', borderRadius: 16, border: '1px solid rgba(26,24,21,0.08)', background: 'rgba(252,250,242,0.84)' }}>
      <div style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.14em', color: 'rgba(26,24,21,0.42)' }}>{label}</div>
      <div style={{ marginTop: 6, fontSize: 18, fontWeight: 700, color: 'var(--kajal)' }}>{value}</div>
      <div style={{ marginTop: 3, fontSize: 12, color: 'rgba(26,24,21,0.52)' }}>{hint}</div>
    </div>
  )
}
