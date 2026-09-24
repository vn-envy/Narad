/**
 * Kriya tasks: multi-step errands that run on the Mac by themselves.
 *
 * The chat receives `task_started` when an avatar starts one; the card and the
 * task screen then poll `/tasks/{id}` and the live frame. Everything here is
 * profile-scoped on the server: another person's task id is simply not found.
 */
import { apiFetch, apiJson, type ApprovalProposal } from './api'

export type TaskStatus =
  | 'queued'
  | 'running'
  | 'waiting_approval'
  | 'waiting_help'
  | 'done'
  | 'failed'
  | 'cancelled'

export interface TaskEvent {
  id: number
  at: string
  kind: string
  step: number | null
  summary: string
}

export interface TaskHelp {
  kind: 'login' | 'captcha' | 'operator' | 'approval_expired' | string
  reason: string
  since?: string
  url?: string
}

export interface TaskResult {
  summary: string
  answer?: string
  reason?: string
  url?: string
  /** A phone task in verified mode: what Artemis's checker found. */
  verification?: { status: string; failed_checks?: string[]; findings?: string[] }
}

/** A banking or UPI app a phone task needs, not yet allowed for it. */
export interface BlockedApp {
  package: string
  name: string
}

export interface KriyaTask {
  id: string
  goal: string
  start_url?: string
  done_when?: string
  surface: string
  status: TaskStatus
  detail: string
  step: number
  max_steps: number
  proposal_id?: string | null
  help?: TaskHelp | null
  last_url?: string
  result?: TaskResult | null
  created_at?: string
  updated_at?: string
  finished_at?: string | null
  live?: boolean
  cancel_requested?: boolean
  events?: TaskEvent[]
  /** The Anumati proposal the task waits on (waiting_approval only). */
  approval?: ApprovalProposal
  /** Phone tasks: the phone's name and fast or verified mode. */
  device?: string | null
  mode?: string | null
}

export type TakeoverInput =
  | { kind: 'click'; x: number; y: number }
  | { kind: 'type'; text: string }
  | { kind: 'key'; key: 'Enter' | 'Tab' | 'Backspace' | 'Escape' }
  | { kind: 'scroll'; direction: 'up' | 'down' }
  | { kind: 'back' }

const ACTIVE = new Set<TaskStatus>(['queued', 'running', 'waiting_approval', 'waiting_help'])

export const TASK_ID_RE = /^tsk_[0-9a-f]{16}$/

export function isTaskActive(task: Pick<KriyaTask, 'status'>): boolean {
  return ACTIVE.has(task.status)
}

export function isTaskLive(task: Pick<KriyaTask, 'status'>): boolean {
  return task.status === 'running' || task.status === 'waiting_help' || task.status === 'waiting_approval'
}

export const TASK_STATUS_LABELS: Record<TaskStatus, string> = {
  queued: 'Waiting to start',
  running: 'Working',
  waiting_approval: 'Needs your OK',
  waiting_help: 'Needs your help',
  done: 'Done',
  failed: 'Could not finish',
  cancelled: 'Stopped',
}

export function fetchTask(id: string, signal?: AbortSignal): Promise<KriyaTask> {
  return apiJson<KriyaTask>(`/tasks/${encodeURIComponent(id)}`, { signal })
}

async function postTask(id: string, action: string, body?: unknown): Promise<Response> {
  const response = await apiFetch(`/tasks/${encodeURIComponent(id)}/${action}`, {
    method: 'POST',
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!response.ok) {
    let detail = ''
    try {
      detail = String((await response.json())?.detail ?? '')
    } catch {
      detail = ''
    }
    throw new Error(detail || `Narad answered ${response.status}`)
  }
  return response
}

export async function stopTask(id: string): Promise<KriyaTask> {
  return (await postTask(id, 'cancel')).json() as Promise<KriyaTask>
}

export async function continueTask(id: string): Promise<KriyaTask> {
  return (await postTask(id, 'resume')).json() as Promise<KriyaTask>
}

/** Forward a tap, typing, a key, a scroll or Back to the page (only while it waits for help). */
export async function takeoverTask(id: string, input: TakeoverInput): Promise<void> {
  await postTask(id, 'takeover', input)
}

/** The banking or UPI apps the waiting approval still needs allowed (phone tasks). */
export function blockedApps(task: KriyaTask): BlockedApp[] {
  const preview = task.approval?.preview as { blocked_apps?: BlockedApp[] } | undefined
  return Array.isArray(preview?.blocked_apps) ? preview.blocked_apps : []
}

/** Allow one banking or UPI app for this phone task: the approval is replaced by one that names it. */
export async function allowTaskApp(id: string, pkg: string): Promise<KriyaTask> {
  return (await postTask(id, 'allow-app', { package: pkg })).json() as Promise<KriyaTask>
}

/** Browser tasks can be helped on the live view; a phone or the Mac is used directly. */
export function canTakeOver(task: Pick<KriyaTask, 'surface'>): boolean {
  return task.surface === 'browser' || task.surface === 'cloud_browser'
}

/** The latest view (a page JPEG, the phone's screen, the Mac window as PNG), or null. Never cached. */
export async function fetchTaskFrame(id: string, signal?: AbortSignal): Promise<Blob | null> {
  const response = await apiFetch(`/tasks/${encodeURIComponent(id)}/frame`, { cache: 'no-store', signal })
  if (response.status !== 200) return null
  return response.blob()
}

/** Ask the app to open the task screen (the chat card, Activity and notifications do). */
export function openTaskScreen(id: string): void {
  window.dispatchEvent(new CustomEvent('narad:open-task', { detail: { id } }))
}
