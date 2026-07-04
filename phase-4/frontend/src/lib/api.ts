function inferLocalApiBase(): string {
  if (typeof window === 'undefined') return ''
  const host = window.location.hostname
  const port = window.location.port
  const isLocalHost = host === 'localhost' || host === '127.0.0.1'
  if (!isLocalHost) return ''
  if (port === '8010') return ''
  return `${window.location.protocol}//${host}:8010`
}

export const API_BASE = ((import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')) || inferLocalApiBase()

function isAbsoluteUrl(path: string): boolean {
  return /^https?:\/\//i.test(path)
}

export function apiPath(path: string): string {
  if (isAbsoluteUrl(path)) return path
  if (!path.startsWith('/')) return apiPath(`/${path}`)
  return API_BASE ? `${API_BASE}${path}` : path
}

export function apiUrl(path: string, params?: Record<string, string | number | boolean | null | undefined>): string {
  const url = new URL(apiPath(path), window.location.origin)
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      if (value === undefined || value === null || value === '') continue
      url.searchParams.set(key, String(value))
    }
  }
  return API_BASE ? `${API_BASE}${url.pathname}${url.search}` : `${url.pathname}${url.search}`
}

export function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  return fetch(apiPath(path), init)
}

export async function apiJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch(path, init)
  if (!response.ok) {
    throw new Error(`API ${path} failed with ${response.status}`)
  }
  return response.json() as Promise<T>
}

export interface RuntimeIssue {
  level: string
  code: string
  message: string
}

export interface CapabilityFlag {
  available: boolean
  reason?: string | null
  kind?: string
}

export interface ContextPolicyProfile {
  model: string
  provider: string
  max_context_tokens: number
  hard_input_budget_tokens: number
  soft_target_tokens: number
  reserved_output_tokens: number
  supports_prompt_cache: boolean
  supports_native_compaction: boolean
  supports_provider_token_count: boolean
  larger_window_fallbacks: string[]
}

export interface ContextPolicy {
  overflow_policy: string
  fidelity_policy: string
  profiles: Record<string, ContextPolicyProfile>
  fallback_graph: Record<string, string[]>
}

export interface MemoryTierRule {
  namespace: string
  default_tier: string
  archive_tier?: string | null
  archive_after_days?: number | null
  exact_required?: boolean
  description?: string
}

export interface MemoryTierPolicy {
  local_first: boolean
  default_strategy: string
  exact_namespaces: string[]
  rules: Record<string, MemoryTierRule>
}

export interface MemoryTierDiagnostics {
  user_id: string
  backend: {
    turbovec_available: boolean
    numpy_available: boolean
    default_mode: string
  }
  policy: MemoryTierPolicy
  namespaces: Record<string, Record<string, {
    record_count: number
    embedding_model: string
  }>>
}

export interface AgentCapability {
  name: string
  description: string
  disciplines: string[]
  discipline: string
  tool_families: string[]
  degraded_tool_families: string[]
  ui: {
    deva: string
    abbrev: string
    color: string
    rgb: string
  }
}

export interface RuntimeCapabilities {
  status: string
  build: {
    phase: string
    label: string
    runtime_mode: string
  }
  architecture: {
    canonical_agent_count: number
    model: string
    cultural_mode: string
    agent_names: string[]
  }
  agents: AgentCapability[]
  providers: Record<string, CapabilityFlag>
  tool_families: Record<string, CapabilityFlag>
  local_ready: {
    frontend_transport_agnostic: boolean
    local_model_runtime: boolean
    desktop_packaging: boolean
  }
  startup_checks: Array<{
    name: string
    ok: boolean
    reason?: string | null
  }>
  issues: RuntimeIssue[]
  issue_count: number
  degraded_capability_count: number
  context_policy?: ContextPolicy
  memory_tiers?: MemoryTierPolicy
}

export interface LearningArtifact {
  artifact_id: string
  workspace_id: string
  topic: string
  artifact_type: 'flashcards' | 'concept_map'
  version: number
  status: string
  created_at: string
  updated_at: string
  record_ids: string[]
  doc: {
    cards?: Array<{ id: string; front: string; back: string; tags?: string[] }>
    nodes?: Array<{ id: string; label: string; note: string }>
    edges?: Array<{ source: string; target: string; label?: string }>
  }
}

export interface LearningRecord {
  record_id: string
  title: string
  summary: string
  body: string
  created_at: string
  type: string
  session_id?: string | null
  tags: string[]
  path: string
}

export interface LearningWorkspace {
  workspace_id: string
  user_id: string
  topic: string
  topic_key: string
  created_at: string
  updated_at: string
  status: string
  record_count: number
  resource_count: number
  glossary_term_count: number
  latest_record_id?: string | null
  last_session_id?: string | null
  mission?: string
  glossary?: string
  resources?: string
  records?: LearningRecord[]
}

export interface RuntimeHealth {
  status: string
  agent: string
  phase: string
  model: string
  architecture: {
    model: string
    canonical_agent_count: number
    agent_names: string[]
  }
  runtime: {
    mode: string
    local_ready: boolean
  }
  issue_count: number
}

export interface ArchitectureScorecard {
  legacy_direct_memory_imports: number
  smriti_core_imports: number
  episode_store_enabled: boolean
  swapna_enabled: boolean
  karma_mutation_log_enabled: boolean
  baseline_test_files: Record<string, string>
}

export interface KarmaMutation {
  id: string
  ts: string
  action: string
  entity_type: string
  entity_id: string
  actor: string
  detail: string
  policy?: string | null
  provenance_ids?: string[]
  metadata?: Record<string, unknown>
}

export interface SwapnaInboxItem {
  id: string
  ts: string
  user_id: string
  project_id: string
  apply: boolean
  source_episode_ids: string[]
  suggestions: {
    facts: Array<Record<string, unknown>>
    scenarios: Array<Record<string, unknown>>
    candidate_keywords: string[]
  }
}

export interface EvolutionAgentPoint {
  agent: string
  daily: Record<string, number>
  cumulative: Record<string, number>
}

export interface EvolutionTimelineEntry {
  date: string
  agents: EvolutionAgentPoint[]
}

export interface EvolutionAgentSummary {
  name: string
  discipline: string
  totals: Record<string, number>
  tool_usage: Array<{ tool: string; count: number }>
  models: Array<{ model: string; count: number }>
  memory: {
    episodes: number
    commitments: number
    reflections: number
    swapna_touchpoints: number
  }
  learning: {
    sutras_promoted: number
    sutras_active: number
    sutras_reverted: number
    sankalpa_updates: number
  }
  behavior: {
    sessions: number
    avg_latency_ms: number
    total_tokens: number
    degraded_events: number
  }
  runtime: {
    models_seen: string[]
  }
}

export interface EvolutionHistory {
  generated_at: string
  window_days: number
  categories: string[]
  config: {
    tapas_promote_threshold: number
    sutra_cooldown_hours: number
    tapas_judge_model: string
  }
  agents: EvolutionAgentSummary[]
  timeline: EvolutionTimelineEntry[]
  recent_changes: Array<{
    ts: string
    agent: string
    category: string
    title: string
    detail: string
  }>
}

export interface HarnessSessionRecord {
  session_id: string
  user_id?: string
  title: string
  created_at: string
  updated_at: string
  turn_count: number
  restorable: boolean
  archived: boolean
  archived_at?: string | null
  parent_session_id?: string | null
  lineage_root_id?: string | null
  source?: string
  last_user_query?: string
  last_assistant_preview?: string
  thread_summary?: string
  restored_after_reset?: boolean
  last_trace_session_id?: string | null
  avatars?: string[]
  karya?: {
    total?: number
    done_count?: number
    blocked_count?: number
    active_titles?: string[]
  } | null
  continued_from_sessions?: string[]
  compacted_at?: string | null
}

export interface HarnessContextPlaneStep {
  key: string
  label: string
  status: string
  detail: string
}

export interface HarnessContextBundle {
  session: HarnessSessionRecord
  context_order: HarnessContextPlaneStep[]
  thread_plane: {
    turn_count: number
    restorable: boolean
    summary: string
    recent_turns: Array<{
      role: string
      text: string
      ts?: string
    }>
  }
  working_plane: {
    avatars: string[]
    karya?: {
      total?: number
      done_count?: number
      blocked_count?: number
      active_titles?: string[]
    } | null
    latencies_ms?: Record<string, number>
    phase_transitions?: string[]
    last_trace_session_id?: string | null
    restored_after_reset?: boolean
    continued_from_sessions?: string[]
  }
  smriti_plane: {
    episode_count: number
    commitment_count: number
    durable_layers: string[]
    commitments: Array<{
      id: string
      kind: string
      content: string
      avatar?: string
      ts?: string
    }>
  }
  governance_plane: {
    runtime_status: string
    mutation_count: number
    recent_mutations: Array<{
      id: string
      ts: string
      action: string
      detail: string
      actor?: string
    }>
    swapna_pending: number
    dharma_guarded: boolean
  }
  rehydration_preview?: string
}

export interface HarnessOverview {
  generated_at: string
  user_id: string
  selected_session_id?: string | null
  runtime: {
    status: string
    issue_count: number
    mode: string
  }
  summary: {
    session_count: number
    archived_count: number
    restorable_count: number
    forked_count: number
    swapna_pending: number
    mutation_count: number
    episode_count: number
    commitment_count: number
  }
  planes: {
    session: {
      label: string
      detail: string
      count: number
      active_session_id?: string | null
      forked_count?: number
    }
    working: {
      label: string
      detail: string
      count: number
      restored_count?: number
    }
    smriti: {
      label: string
      detail: string
      count: number
      commitment_count?: number
    }
    governance: {
      label: string
      detail: string
      count: number
      issue_count?: number
    }
  }
  sessions: HarnessSessionRecord[]
  context?: HarnessContextBundle | null
  scorecard?: ArchitectureScorecard | null
}

export interface ProjectListItem {
  id: string
  name: string
  workspace_root?: string | null
  workspace_label?: string | null
  status?: string
  project_status?: string
  created_at: string | null
  session_count: number
  active_session_id?: string | null
  last_activity_at?: string | null
}

export interface ProjectTask {
  task_id: string
  project_id: string
  workspace_root?: string | null
  source_session_id: string | null
  title: string
  description: string
  status: string
  priority: string
  owner: string | null
  kind: string
  blocked_by: string[]
  artifact_refs: Array<Record<string, unknown>>
  sort_order: number
  created_at: string
  updated_at: string
  completed_at: string | null
}

export interface ProjectWorkspace {
  project: {
    id: string
    name: string
    workspace_root?: string | null
    workspace_label?: string | null
    created_at: string | null
    updated_at: string | null
    status: string
    project_status?: string
    current_goal: string | null
    active_session_id: string | null
    session_count: number
    last_activity_at: string | null
  }
  active_session: {
    session_id: string
    ts: string | null
    query: string | null
    avatars: string[]
    total_ms: number | null
  } | null
  recent_sessions: Array<{
    session_id: string
    ts: string | null
    query: string | null
    avatars: string[]
    total_ms: number | null
  }>
  task_summary: {
    total: number
    by_status: Record<string, number>
    now: ProjectTask[]
    next: ProjectTask[]
    blocked: ProjectTask[]
    recent_done: ProjectTask[]
  }
  memory_anchors: Array<{
    entity: string
    preview: string
    size_chars: number
  }>
  avatars: string[]
}

export interface ProjectExecution {
  project_id: string
  project_name: string
  workspace_root?: string | null
  workspace_label?: string | null
  current_goal: string | null
  active_session: {
    session_id: string
    ts: string | null
    query: string | null
    avatars: string[]
    total_ms: number | null
  } | null
  now: ProjectTask[]
  next: ProjectTask[]
  blocked: ProjectTask[]
  recent_done: ProjectTask[]
  artifacts: Array<Record<string, unknown>>
  active_agents: string[]
  recent_events: Array<{
    ts: string | null
    event: string | null
    avatar: string | null
    task: string | null
  }>
}

export interface ProjectStateStore {
  projectId: string
  workspace: ProjectWorkspace | null
  execution: ProjectExecution | null
  tasks: ProjectTask[]
  loading: boolean
  refreshing: boolean
  lastLoadedAt: number | null
  error: string | null
}
