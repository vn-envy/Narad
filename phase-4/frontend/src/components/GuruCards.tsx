/**
 * Guru-mode chat cards (G7) — rendered inline in the chat stream when a
 * message carries a `guru` payload from the guided-mode state machine.
 *
 * - GuruAtomCard: concept name + progress, spoken narration, an animated
 *   artifact in a sandboxed iframe (sandbox="" — no scripts, CSS animation
 *   only), and the quiz (MCQ chips or free answer).
 * - Completion / exit / info cards for the loop's other states.
 */
import { useMemo, useState } from 'react'
import type { GuruPayload, GuidedStep, GuidedGrade, GuidedProgress } from '../hooks/useAvatara'
import { cn } from '@/lib/utils'
import { Check, ChevronRight, GraduationCap, Loader, SkipForward, Volume2, X } from 'lucide-react'

const KRISHNA = '#1d4ed8'
const KRISHNA_RGB = '29, 78, 216'

function ProgressPips({ progress }: { progress: GuidedProgress }) {
  const total = Math.max(progress.total, 1)
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="inline-flex gap-[3px]">
        {Array.from({ length: Math.min(total, 12) }).map((_, i) => (
          <span
            key={i}
            className="inline-block w-[6px] h-[6px] rounded-full"
            style={{
              background: i < progress.mastered
                ? KRISHNA
                : i < progress.mastered + progress.shaky
                ? 'var(--marigold, #d97b29)'
                : 'rgba(45,42,38,0.18)',
            }}
          />
        ))}
      </span>
      <span className="font-mono text-[10px]" style={{ color: 'rgba(45,42,38,0.55)' }}>
        {progress.mastered}/{progress.total} mastered
      </span>
    </span>
  )
}

function GuruFrame({ children, tone = 'default' }: { children: React.ReactNode; tone?: 'default' | 'celebrate' }) {
  return (
    <div
      className="folk-card folk-shadow rounded-[4px_16px_16px_16px] px-4 py-3.5 w-full"
      style={{
        borderLeft: `3px solid ${tone === 'celebrate' ? 'var(--marigold, #d97b29)' : KRISHNA}`,
        background: `linear-gradient(135deg, rgba(${KRISHNA_RGB}, 0.045), transparent 55%)`,
        color: 'var(--kajal)',
      }}
    >
      {children}
    </div>
  )
}

function GuruBadge() {
  return (
    <span
      className="text-chip px-2 py-px rounded organic-border inline-flex items-center gap-1"
      style={{
        color: KRISHNA,
        borderColor: `rgba(${KRISHNA_RGB}, 0.30)`,
        background: `rgba(${KRISHNA_RGB}, 0.08)`,
      }}
    >
      <GraduationCap size={10} />
      <span style={{ fontFamily: 'var(--font-deva)', fontSize: 10 }}>कृ</span>
      Krishna · teach
    </span>
  )
}

/** Animated artifact in a fully sandboxed iframe — no scripts can run. */
function ArtifactFrame({ html, name }: { html: string; name: string }) {
  const srcDoc = useMemo(
    () => `<!doctype html><html><head><meta charset="utf-8"><style>
      html,body{margin:0;padding:0;background:transparent;overflow:hidden}
    </style></head><body>${html}</body></html>`,
    [html],
  )
  if (!html) return null
  return (
    <iframe
      sandbox=""
      srcDoc={srcDoc}
      title={`Illustration — ${name}`}
      loading="lazy"
      className="w-full rounded mt-2.5"
      style={{
        border: '1px solid rgba(45,42,38,0.12)',
        background: 'var(--surface, rgba(45,42,38,0.03))',
        height: 240,
      }}
    />
  )
}

interface QuizProps {
  step: GuidedStep
  grade?: GuidedGrade
  answered?: boolean
  busy: boolean
  onAnswer: (answer?: string, choiceIndex?: number) => void
  onSkip: () => void
}

function QuizBlock({ step, grade, answered, busy, onAnswer, onSkip }: QuizProps) {
  const [draft, setDraft] = useState('')
  const quiz = step.quiz
  if (!quiz) return null

  const settled = Boolean(answered) || Boolean(grade?.correct)
  const isMcq = quiz.type === 'mcq'

  return (
    <div className="mt-3 pt-3" style={{ borderTop: '1px dashed rgba(45,42,38,0.15)' }}>
      <div className="flex items-center gap-1.5 mb-1.5">
        <span className="font-mono text-[10px] uppercase tracking-wider" style={{ color: KRISHNA }}>
          Check yourself
        </span>
      </div>
      <p className="text-[13px] leading-relaxed mb-2" style={{ fontFamily: 'var(--font-body)' }}>
        {quiz.question}
      </p>

      {isMcq ? (
        <div className="flex flex-col gap-1.5">
          {(quiz.options ?? []).map((option, index) => {
            const chosen = grade?.choiceIndex === index
            const showState = settled && chosen
            return (
              <button
                key={index}
                disabled={settled || busy}
                onClick={() => onAnswer(undefined, index)}
                className={cn(
                  'text-left text-[12.5px] px-3 py-2 rounded transition-all duration-150',
                  !settled && !busy && 'hover:scale-[1.01] active:scale-[0.99] cursor-pointer',
                )}
                style={{
                  fontFamily: 'var(--font-body)',
                  border: `1px solid ${showState
                    ? grade?.correct ? 'rgba(22,101,52,0.5)' : 'rgba(153,27,27,0.45)'
                    : 'rgba(45,42,38,0.15)'}`,
                  background: showState
                    ? grade?.correct ? 'rgba(22,101,52,0.08)' : 'rgba(153,27,27,0.07)'
                    : 'var(--surface, rgba(252,250,242,0.6))',
                  color: 'var(--kajal)',
                  opacity: settled && !chosen ? 0.55 : 1,
                }}
              >
                <span className="font-mono text-[10px] mr-2" style={{ color: KRISHNA }}>
                  {String.fromCharCode(65 + index)}
                </span>
                {option}
                {showState && (grade?.correct
                  ? <Check size={12} className="inline ml-1.5" style={{ color: 'rgb(22,101,52)' }} />
                  : <X size={12} className="inline ml-1.5" style={{ color: 'rgb(153,27,27)' }} />)}
              </button>
            )
          })}
        </div>
      ) : (
        !settled && (
          <div className="flex flex-col gap-1.5">
            <textarea
              value={draft}
              onChange={e => setDraft(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault()
                  if (draft.trim() && !busy) onAnswer(draft.trim())
                }
              }}
              placeholder="Answer in your own words — typed or dictated…"
              rows={2}
              disabled={busy}
              className="w-full text-[12.5px] px-3 py-2 rounded resize-none outline-none"
              style={{
                fontFamily: 'var(--font-body)',
                border: '1px solid rgba(45,42,38,0.18)',
                background: 'var(--surface, rgba(252,250,242,0.7))',
                color: 'var(--kajal)',
              }}
            />
            <div className="flex items-center gap-2">
              <button
                disabled={!draft.trim() || busy}
                onClick={() => onAnswer(draft.trim())}
                className="text-[11px] px-3 py-1.5 rounded font-medium transition-opacity disabled:opacity-40"
                style={{ background: KRISHNA, color: 'var(--paper, #fcfaf2)' }}
              >
                {busy ? <Loader size={11} className="inline animate-spin" /> : 'Submit answer'}
              </button>
            </div>
          </div>
        )
      )}

      {/* Verdict */}
      {grade && (
        <div
          className="mt-2 px-3 py-2 rounded text-[12.5px] leading-relaxed"
          style={{
            fontFamily: 'var(--font-body)',
            background: grade.correct ? 'rgba(22,101,52,0.07)' : 'rgba(153,27,27,0.06)',
            border: `1px solid ${grade.correct ? 'rgba(22,101,52,0.25)' : 'rgba(153,27,27,0.22)'}`,
            color: 'var(--kajal)',
          }}
        >
          <span className="font-medium">{grade.correct ? '✓ ' : ''}{grade.feedback}</span>
          {!grade.correct && grade.remediation && (
            <p className="mt-1 opacity-85">{grade.remediation}</p>
          )}
        </div>
      )}

      {/* Post-verdict actions: retry is implicit (free quiz stays open); skip moves on */}
      {!settled && (
        <button
          onClick={onSkip}
          disabled={busy}
          className="mt-2 inline-flex items-center gap-1 text-[10.5px] font-mono opacity-50 hover:opacity-90 transition-opacity"
          style={{ color: 'var(--kajal)' }}
        >
          <SkipForward size={10} />
          skip this one for now
        </button>
      )}
      {settled && !grade?.correct && (
        <button
          onClick={onSkip}
          disabled={busy}
          className="mt-2 inline-flex items-center gap-1 text-[11px] px-3 py-1.5 rounded font-medium"
          style={{ background: `rgba(${KRISHNA_RGB}, 0.1)`, color: KRISHNA, border: `1px solid rgba(${KRISHNA_RGB}, 0.3)` }}
        >
          Continue
          <ChevronRight size={12} />
        </button>
      )}
    </div>
  )
}

interface GuruMessageProps {
  payload: GuruPayload
  busy: boolean
  speaking: boolean
  onAnswer: (answer?: string, choiceIndex?: number) => void
  onSkip: () => void
  onReplayVoice: () => void
}

export function GuruMessage({ payload, busy, speaking, onAnswer, onSkip, onReplayVoice }: GuruMessageProps) {
  if (payload.kind === 'info') {
    return (
      <GuruFrame>
        <GuruBadge />
        <p className="text-[13px] leading-relaxed mt-2" style={{ fontFamily: 'var(--font-body)' }}>
          {payload.message}
        </p>
      </GuruFrame>
    )
  }

  if (payload.kind === 'complete' || payload.kind === 'exit') {
    return (
      <GuruFrame tone={payload.kind === 'complete' ? 'celebrate' : 'default'}>
        <div className="flex items-center justify-between gap-2 flex-wrap">
          <GuruBadge />
          {payload.progress && <ProgressPips progress={payload.progress} />}
        </div>
        <p className="text-[13.5px] leading-relaxed mt-2" style={{ fontFamily: 'var(--font-body)' }}>
          {payload.message}
        </p>
      </GuruFrame>
    )
  }

  const { step, grade, answered } = payload
  return (
    <GuruFrame>
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <GuruBadge />
        <ProgressPips progress={step.progress} />
      </div>

      <div className="flex items-baseline gap-2 mt-2">
        <h3 className="text-[15px] font-semibold" style={{ fontFamily: 'var(--font-display, inherit)' }}>
          {step.name}
        </h3>
        <button
          onClick={onReplayVoice}
          title={speaking ? 'Stop voice' : 'Hear it again'}
          className="opacity-45 hover:opacity-100 transition-opacity"
          style={{ color: speaking ? 'var(--marigold, #d97b29)' : KRISHNA }}
        >
          <Volume2 size={13} />
        </button>
      </div>

      <p className="text-[13px] leading-relaxed mt-1.5" style={{ fontFamily: 'var(--font-body)' }}>
        {step.narration}
      </p>

      <ArtifactFrame html={step.artifact_html ?? ''} name={step.name ?? 'concept'} />

      <QuizBlock
        step={step}
        grade={grade}
        answered={answered}
        busy={busy}
        onAnswer={onAnswer}
        onSkip={onSkip}
      />
    </GuruFrame>
  )
}
