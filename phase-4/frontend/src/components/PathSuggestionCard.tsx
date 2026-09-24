/**
 * Path suggestion card: the chat recognised one of the six guided paths
 * (the `path_suggestion` stream event) and offers it, under the answer.
 *
 * Nothing starts on its own. "Start" creates the run bound to this chat
 * thread (intake continues here, one or two questions at a time); "Continue
 * here" moves an open run of that path into this thread; "Not now" only hides
 * the card (the server does not offer the same path in this thread again).
 */
import { useState } from 'react'
import { ArrowRight, Loader, Route, X } from 'lucide-react'
import { apiFetch, apiUrl } from '@/lib/api'
import { OPEN_URL_EVENT } from '@/lib/pwa'

export interface PathSuggestion {
  workflow_id: string
  title: string
  eyebrow?: string
  accent?: string
  session_id: string
  action: 'start' | 'resume'
  run_id?: string
  run_title?: string
  stage_title?: string
  progress_percent?: number
  first_questions?: string[]
}

type CardState =
  | { kind: 'offer' }
  | { kind: 'busy' }
  | { kind: 'dismissed' }
  | { kind: 'done'; runId: string; questions: string[]; stage: string }
  | { kind: 'error'; message: string }

interface StartedRun {
  run_id: string
  current_stage?: { title?: string } | null
  intake_questions?: Array<{ question: string }>
  next_action?: { questions?: string[] }
}

async function postJson<T>(path: string, userId: string, body: unknown): Promise<T> {
  const response = await apiFetch(apiUrl(path, { user_id: userId }), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  const data = await response.json().catch(() => ({})) as T & { detail?: string }
  if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`)
  return data
}

export function PathSuggestionCard({ suggestion, userId }: { suggestion: PathSuggestion; userId: string }) {
  const [state, setState] = useState<CardState>({ kind: 'offer' })
  const accent = suggestion.accent || 'var(--tulsi)'
  const resume = suggestion.action === 'resume' && Boolean(suggestion.run_id)

  const accept = async () => {
    setState({ kind: 'busy' })
    try {
      const { run } = resume
        ? await postJson<{ run: StartedRun }>(`/workflow-runs/${suggestion.run_id}/actions`, userId, {
          action: 'bind', payload: { session_id: suggestion.session_id },
        })
        : await postJson<{ run: StartedRun }>(`/workflows/${suggestion.workflow_id}/runs`, userId, {
          inputs: {}, partial: true, session_id: suggestion.session_id,
        })
      const questions = run.intake_questions?.length
        ? run.intake_questions.map(item => item.question)
        : run.next_action?.questions ?? []
      setState({ kind: 'done', runId: run.run_id, questions, stage: run.current_stage?.title ?? '' })
      window.dispatchEvent(new CustomEvent('narad:workflow-event', {
        detail: { type: 'workflow_updated', data: { workflow_run_id: run.run_id }, ts: Date.now() },
      }))
    } catch (cause) {
      setState({ kind: 'error', message: cause instanceof Error ? cause.message : 'The path could not be started.' })
    }
  }

  if (state.kind === 'dismissed') return null

  return (
    <div
      className="folk-card rounded-[4px_16px_16px_16px] px-4 py-3 w-full"
      style={{ borderLeft: `3px solid ${accent}`, color: 'var(--kajal)' }}
      aria-label={`Suggested path: ${suggestion.title}`}
    >
      <div className="flex items-center gap-2">
        <span className="inline-flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-wider" style={{ color: accent }}>
          <Route size={12} /> {suggestion.title} path
        </span>
        {state.kind === 'offer' && (
          <button
            type="button"
            onClick={() => setState({ kind: 'dismissed' })}
            aria-label="Not now"
            className="ml-auto inline-flex items-center justify-center rounded-full"
            style={{ width: 28, height: 28, border: 0, background: 'transparent', color: 'var(--ink-55)' }}
          >
            <X size={14} />
          </button>
        )}
      </div>

      {state.kind === 'done' ? (
        <div className="mt-1.5">
          <p className="text-[13.5px] leading-snug font-semibold">
            {resume ? 'Continuing here' : 'Started here'}{state.stage ? `: ${state.stage}` : ''}
          </p>
          {state.questions.length > 0 && (
            <ul className="mt-1 text-[12.5px] leading-snug" style={{ color: 'var(--ink-70)' }}>
              {state.questions.map(question => <li key={question}>{question}</li>)}
            </ul>
          )}
          <p className="mt-1 text-[11.5px]" style={{ color: 'var(--ink-55)' }}>Answer here in the chat.</p>
          <button
            type="button"
            onClick={() => window.dispatchEvent(new CustomEvent(OPEN_URL_EVENT, { detail: { url: `/?path=${state.runId}` } }))}
            className="mt-2 inline-flex items-center gap-1 text-[12px] font-semibold"
            style={{ border: 0, background: 'transparent', color: accent, padding: 0 }}
          >
            See it in Paths <ArrowRight size={13} />
          </button>
        </div>
      ) : (
        <>
          <p className="mt-1.5 text-[13.5px] leading-snug font-semibold">
            {resume
              ? `Continue your ${suggestion.run_title || suggestion.title} path here?`
              : `Make this a ${suggestion.title} path?`}
          </p>
          <p className="mt-0.5 text-[12px] leading-snug" style={{ color: 'var(--ink-70)' }}>
            {resume
              ? `${suggestion.progress_percent ?? 0}% done${suggestion.stage_title ? `, next: ${suggestion.stage_title}` : ''}.`
              : suggestion.eyebrow || 'Narad keeps each step, and a step is done only when it can be checked.'}
          </p>
          <div className="mt-2.5 flex gap-2">
            <button
              type="button"
              onClick={() => setState({ kind: 'dismissed' })}
              disabled={state.kind === 'busy'}
              className="flex-1 rounded-[10px] text-[13px] font-semibold"
              style={{ minHeight: 44, border: '1px solid var(--ink-12)', background: 'var(--surface)', color: 'var(--kajal)' }}
            >
              Not now
            </button>
            <button
              type="button"
              onClick={() => void accept()}
              disabled={state.kind === 'busy'}
              className="flex-[1.6] rounded-[10px] text-[13.5px] font-bold inline-flex items-center justify-center gap-1.5"
              style={{ minHeight: 44, border: 0, background: accent, color: '#fff' }}
            >
              {state.kind === 'busy' ? <Loader size={15} className="animate-spin" /> : <ArrowRight size={15} />}
              {resume ? 'Continue here' : 'Start'}
            </button>
          </div>
          {state.kind === 'error' && (
            <p className="mt-2 text-[12px]" role="alert" style={{ color: 'var(--kesari)' }}>{state.message}</p>
          )}
        </>
      )}
    </div>
  )
}
