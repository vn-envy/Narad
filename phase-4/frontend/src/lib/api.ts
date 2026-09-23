/** Ports used by `npm run dev` (Vite). Anywhere else — the backend serving
 *  its own dist/, a tailscale hostname, the installed PWA — is same-origin. */
const VITE_DEV_PORTS = new Set(['5173', '5174'])
const DEV_BACKEND_PORT = '8000'

function inferLocalApiBase(): string {
  if (typeof window === 'undefined') return ''
  const { hostname, port, protocol } = window.location
  if (VITE_DEV_PORTS.has(port)) {
    return `${protocol}//${hostname}:${DEV_BACKEND_PORT}`
  }
  return ''
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

const PROFILE_SESSION_KEY = 'narad_profile_session'

export interface FamilyProfile {
  user_id: string
  display_name: string
  initial: string
  color: string
  has_pin: boolean
  is_owner: boolean
  created_at?: string | null
  last_active_at?: string | null
}

export interface FamilyProfileSession {
  profile: FamilyProfile
  token: string
  expires_at: number
}

export interface FamilyProfileInvite {
  code: string
  expires_at: number
}

// <img>/<video>/new-tab loads of /media cannot send the bearer header, so the
// server mirrors the session into an HttpOnly cookie that only /media reads.
// `undefined` = not synced yet this page load (an older cookie may linger).
let mediaSessionToken: string | null | undefined

function syncMediaSession(token: string | null): void {
  if (token === mediaSessionToken) return
  mediaSessionToken = token
  fetch(apiPath('/profiles/media-session'), {
    method: token ? 'POST' : 'DELETE',
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
  }).catch(() => { mediaSessionToken = undefined })
}

export function getProfileSession(): FamilyProfileSession | null {
  try {
    const raw = localStorage.getItem(PROFILE_SESSION_KEY) || sessionStorage.getItem(PROFILE_SESSION_KEY)
    if (!raw) return null
    const session = JSON.parse(raw) as FamilyProfileSession
    if (!session?.token || !session.profile?.user_id || session.expires_at * 1000 <= Date.now()) {
      localStorage.removeItem(PROFILE_SESSION_KEY)
      sessionStorage.removeItem(PROFILE_SESSION_KEY)
      syncMediaSession(null)
      return null
    }
    syncMediaSession(session.token)
    return session
  } catch {
    return null
  }
}

export function setProfileSession(session: FamilyProfileSession): void {
  const value = JSON.stringify(session)
  try {
    localStorage.setItem(PROFILE_SESSION_KEY, value)
  } catch {
    sessionStorage.setItem(PROFILE_SESSION_KEY, value)
  }
  syncMediaSession(session.token)
}

export function clearProfileSession(): void {
  try { localStorage.removeItem(PROFILE_SESSION_KEY) } catch { /* storage is optional */ }
  try { sessionStorage.removeItem(PROFILE_SESSION_KEY) } catch { /* storage is optional */ }
  syncMediaSession(null)
}

export function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  const session = getProfileSession()
  const headers = new Headers(init?.headers)
  if (session?.token) {
    headers.set('Authorization', `Bearer ${session.token}`)
    headers.set('X-Narad-Profile-ID', session.profile.user_id)
  }
  return fetch(apiPath(path), { ...init, headers })
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

export interface OnboardingReadiness {
  model_ready: boolean
  research_ready: boolean
  connected_model_providers: string[]
  connected_search_providers: string[]
  connected_subscriptions: string[]
  local_model_ready: boolean
  local_model: LocalModelStatus
  google_workspace: GoogleWorkspaceStatus
  jev: JevStatus
  phone: InteractionRuntimeStatus
  desktop: DesktopRuntimeStatus
  interaction_grants: InteractionTarget[]
}

export interface GoogleWorkspaceStatus {
  configured: boolean
  connected: boolean
  can_configure?: boolean
  user_id?: string
  services: Record<string, { read: boolean; write: boolean }>
  photos_access?: string
  reason?: string | null
}

export interface JevStatus {
  available: boolean
  configured: boolean
  enabled?: boolean
  reason?: string | null
}

export interface InteractionTarget {
  target_id: string
  kind: 'browser_skill' | 'artemis' | 'cua'
  external_id: string
  label: string
}

export interface InteractionRuntimeStatus {
  available: boolean
  ready: boolean
  configured?: boolean
  reason?: string | null
  devices?: Array<Record<string, unknown>>
}

export interface DesktopRuntimeStatus {
  available: boolean
  enabled: boolean
  selected_provider?: string
  reason?: string | null
  adapters?: Record<string, {
    available?: boolean
    ready?: boolean
    reason?: string | null
    targets?: Array<{ id: string; label: string }>
  }>
}

export interface LocalModelStatus {
  available: boolean
  ready: boolean
  runtime_installed: boolean
  reachable: boolean
  managed: boolean
  local_host: boolean
  url: string
  version?: string | null
  model: string
  model_tag: string
  model_installed: boolean
  model_size: 'E2B' | 'E4B' | string
  optimized_variant: 'mlx' | 'qat-q4' | string
  download_gb: number
  upgrade_threshold_gb: number
  ram_gb: number
  memory_constrained: boolean
  residency: 'on-demand' | 'warm' | string
  keep_alive: string
  configured_context_tokens: number
  max_context_tokens: number
  no_api_key: boolean
  supports: Record<string, boolean>
  install: {
    state: 'idle' | 'running' | 'complete' | 'error' | string
    progress: number
    status: string
    error?: string | null
  }
  reason?: string | null
}

export interface OnboardingStatus {
  schema_version: number
  user_id: string
  completed: boolean
  needs_onboarding: boolean
  skipped: boolean
  display_name: string
  completed_at?: string | null
  updated_at?: string | null
  readiness: OnboardingReadiness
}

export interface ProviderConnection {
  provider: string
  label: string
  key_page: string
  connected: boolean
  hint?: string
}

export interface ProviderSubscription {
  provider: string
  label: string
  signed_in: boolean
  available: boolean
  detail: string
}

export interface ConnectionsPayload {
  connections: ProviderConnection[]
  subscriptions: ProviderSubscription[]
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

export interface WorkflowTask {
  task_id: string
  workflow_run_id: string
  workflow_stage_id: string
  title: string
  description: string
  status: string
  owner: string | null
  kind: string
}

export interface WorkflowIntakeField {
  key: string
  label: string
  kind: 'text' | 'textarea' | 'select' | 'number' | 'boolean' | 'time'
  required: boolean
  placeholder?: string
  default?: unknown
  options: string[]
  help?: string
}

export interface WorkflowStage {
  id: string
  title: string
  owner: string
  kind: string
  purpose: string
  tools: string[]
  requires_confirmation: boolean
  confirmation_action?: string | null
  status?: string
  output?: {
    status?: string
    summary?: string
    artifacts?: Array<Record<string, unknown>>
    citations?: Array<Record<string, unknown>>
  } | null
}

export interface WorkflowReadiness {
  status: 'ready' | 'limited' | 'unavailable'
  missing_required: string[]
  missing_optional: string[]
  capabilities: Record<string, boolean>
}

export interface WorkflowDefinition {
  id: string
  version: number
  title: string
  eyebrow: string
  description: string
  accent: string
  owner: string
  required_capabilities: string[]
  optional_capabilities: string[]
  intake: WorkflowIntakeField[]
  stages: WorkflowStage[]
  schedule_templates: Array<Record<string, unknown>>
  feedback_routes: Record<string, string>
  readiness: WorkflowReadiness
}

export interface WorkflowSchedule {
  schedule_id: string
  run_id: string
  user_id: string
  title: string
  cadence: string
  timezone: string
  time_of_day?: string | null
  weekdays: number[]
  day_of_month?: number | null
  next_run_at?: string | null
  last_run_at?: string | null
  enabled: boolean
  payload: Record<string, unknown>
}

export interface WorkflowNextAction {
  kind: 'chat' | 'request_confirmation' | 'approve' | 'resume' | 'complete' | 'none'
  label: string
  prompt: string
}

export interface WorkflowRun {
  run_id: string
  workflow_id: string
  workflow_version: number
  user_id: string
  title: string
  status: string
  current_stage_id?: string | null
  session_id?: string | null
  inputs: Record<string, unknown>
  state: {
    cycle?: number
    completed_stage_ids?: string[]
    stage_outputs?: Record<string, Record<string, unknown>>
    artifacts?: Array<Record<string, unknown>>
    citations?: Array<Record<string, unknown>>
    feedback?: Array<Record<string, unknown>>
    confirmation?: {
      stage_id?: string
      action?: string
      summary?: string
      status?: string
      details?: Record<string, unknown>
    } | null
    learning_workspace_id?: string | null
  }
  created_at: string
  updated_at: string
  completed_at?: string | null
  definition: {
    id: string
    title: string
    description: string
    accent: string
    owner: string
  }
  current_stage?: WorkflowStage | null
  stages: WorkflowStage[]
  progress_percent: number
  next_action: WorkflowNextAction
  schedules: WorkflowSchedule[]
  tasks: WorkflowTask[]
  readiness: WorkflowReadiness
  events?: Array<{
    event_id: string
    event_type: string
    stage_id?: string | null
    payload: Record<string, unknown>
    created_at: string
  }>
}
