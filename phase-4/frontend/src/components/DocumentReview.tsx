/**
 * Document review: each value Narad read from a photo or PDF, next to the
 * crop of the page it came from. The person ticks, edits or drops each one and
 * saves; only ticked values reach their health or finance records.
 *
 * - DocumentReviewHost: mounted in the chat. Shows a card when extract_fields
 *   finishes (the `document_review` SSE event) and opens the screen for a
 *   card or for the deep link /?review=<id>.
 * - DocumentReviewScreen: phone-first, one value per row, big tap targets.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Check, ChevronLeft, FileText, Loader, X } from 'lucide-react'
import { toast } from 'sonner'
import {
  DOC_TYPE_NAMES,
  DOCUMENT_REVIEW_EVENT,
  REVIEW_ID_RE,
  discardReview,
  escalateReview,
  fetchCropUrl,
  fetchReview,
  saveReview,
  type DocumentReview,
  type DocumentReviewNotice,
  type ItemDecision,
  type ReviewItem,
  type SaveOptions,
} from '@/lib/document-review'

const INK_55 = 'var(--ink-55)'
const INK_70 = 'var(--ink-70)'
const LINE = 'var(--line)'

const ISSUE_TEXT: Record<string, string> = {
  low_confidence: 'Hard to read on the photo',
  unclear: 'Unclear on the page',
  handwritten: 'Handwritten',
  label_differs: 'The name differs a little from the page',
  differs_from_ocr: 'Differs from what this Mac read',
  type_unsure: 'Not sure if this is money in or out',
  date_unclear: 'Date is unclear',
  no_crop: 'No crop for this value',
  dropped_reference_range: 'Range not found on the page',
  dropped_balance: 'Balance not found on the page',
}

const FLAG_TEXT: Record<string, string> = {
  high: 'Above the range printed on the report',
  low: 'Below the range printed on the report',
  normal: 'Within the range printed on the report',
}

function readDeepLink(): string | null {
  try {
    const id = new URLSearchParams(window.location.search).get('review') ?? ''
    return REVIEW_ID_RE.test(id) ? id : null
  } catch {
    return null
  }
}

function clearDeepLink(): void {
  try {
    const url = new URL(window.location.href)
    if (!url.searchParams.has('review')) return
    url.searchParams.delete('review')
    window.history.replaceState(window.history.state, '', `${url.pathname}${url.search}${url.hash}`)
  } catch { /* history is optional */ }
}

function formatDate(iso: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || '')
  if (!match) return iso
  const date = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]))
  return date.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })
}

function detailLine(item: ReviewItem): string {
  const d = item.details ?? {}
  if (item.kind === 'transaction') {
    return [formatDate(d.date ?? ''), d.type === 'credit' ? 'Money in' : 'Money out',
      d.balance ? `balance ${d.balance}` : ''].filter(Boolean).join(' · ')
  }
  if (item.kind === 'medicine') {
    return [d.dose, d.frequency, d.duration, d.instructions].filter(Boolean).join(' · ')
  }
  if (item.kind === 'event') return [d.time, d.location, d.action].filter(Boolean).join(' · ')
  if (item.kind === 'lab' && item.reference_range) return `Range on report: ${item.reference_range}`
  return ''
}

function count(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`
}

function notSavedReason(item: ReviewItem): string {
  if (item.kind === 'transaction') return 'Money in is shown for reference; your records track spending.'
  if (item.kind === 'event') return 'The date could not be read, so it cannot be saved.'
  return 'Shown for reference.'
}

// ── Crop ──────────────────────────────────────────────────────────────────────

function CropImage({ path, label }: { path: string; label: string }) {
  const [url, setUrl] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)
  const [visible, setVisible] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const node = ref.current
    if (!node || typeof IntersectionObserver === 'undefined') {
      setVisible(true)
      return
    }
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) {
        setVisible(true)
        observer.disconnect()
      }
    }, { rootMargin: '400px' })
    observer.observe(node)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    if (!visible) return
    const controller = new AbortController()
    let objectUrl: string | null = null
    fetchCropUrl(path, controller.signal)
      .then(next => { objectUrl = next; setUrl(next) })
      .catch(() => { if (!controller.signal.aborted) setFailed(true) })
    return () => {
      controller.abort()
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [path, visible])

  return (
    <div
      ref={ref}
      className="w-full overflow-hidden rounded-lg flex items-center justify-center"
      style={{ minHeight: url ? undefined : 56, background: url ? '#fff' : 'var(--ink-05)', border: `1px solid ${LINE}` }}
    >
      {url ? (
        <img src={url} alt={`Where "${label}" appears on the page`} className="block w-full h-auto" />
      ) : (
        <span className="text-[13px] py-4" role="status" style={{ color: INK_55 }}>
          {failed ? 'The crop could not be shown.' : 'Loading the crop…'}
        </span>
      )}
    </div>
  )
}

// ── One value ─────────────────────────────────────────────────────────────────

interface Draft {
  checked: boolean
  label: string
  value: string
  unit: string
  reference_range: string
  details: Record<string, string>
}

function draftFor(item: ReviewItem): Draft {
  const shown = { ...item, ...(item.confirmed_as ?? {}) }
  return {
    checked: item.saveable && item.default_checked,
    label: shown.label ?? '',
    value: shown.value ?? '',
    unit: shown.unit ?? '',
    reference_range: shown.reference_range ?? '',
    details: { ...(shown.details ?? {}) },
  }
}

const DETAIL_FIELDS: Partial<Record<ReviewItem['kind'], Array<[string, string]>>> = {
  medicine: [['dose', 'Dose'], ['frequency', 'How often'], ['duration', 'For how long'], ['instructions', 'Instructions']],
  event: [['time', 'Time'], ['location', 'Place'], ['action', 'What to do']],
  transaction: [['date', 'Date (YYYY-MM-DD)']],
}

function Field({ label, value, onChange, inputMode }: {
  label: string
  value: string
  onChange: (next: string) => void
  inputMode?: 'decimal' | 'text'
}) {
  return (
    <label className="flex flex-col gap-1 text-[13.5px] font-medium" style={{ color: INK_70 }}>
      {label}
      <input
        value={value}
        inputMode={inputMode}
        onChange={event => onChange(event.target.value)}
        className="n-field outline-none"
      />
    </label>
  )
}

function ReviewRow({ item, draft, readOnly, escalatedBy, onChange }: {
  item: ReviewItem
  draft: Draft
  readOnly: boolean
  escalatedBy?: string
  onChange: (next: Draft) => void
}) {
  const [editing, setEditing] = useState(false)
  const issues = item.issues
    .map(issue => issue === 'read_from_image' ? `Read from the photo by ${escalatedBy ?? 'a cloud model'}` : ISSUE_TEXT[issue])
    .filter(Boolean)
  const needsLook = item.saveable && !item.default_checked
  const valueLabel = item.kind === 'event' ? 'Date (YYYY-MM-DD)' : item.kind === 'medicine' ? 'Strength' : 'Value'
  const saved = item.decision === 'confirmed'
  const detail = detailLine({ ...item, ...draft, details: draft.details })

  return (
    <li className="py-4" style={{ borderTop: `1px solid ${LINE}` }}>
      <CropImage path={item.crop_url} label={item.label} />
      <div className="flex items-start gap-3 mt-3">
        {item.saveable && !readOnly ? (
          <button
            type="button"
            role="checkbox"
            aria-checked={draft.checked}
            aria-label={draft.checked ? `Keep ${draft.label}` : `Leave out ${draft.label}`}
            onClick={() => onChange({ ...draft, checked: !draft.checked })}
            className="flex-shrink-0 flex items-center justify-center rounded-lg transition-colors"
            style={{
              width: 44, height: 44,
              border: `2px solid ${draft.checked ? 'var(--tulsi)' : 'var(--ink-40)'}`,
              background: draft.checked ? 'var(--tulsi)' : 'var(--field)',
              color: '#fff',
            }}
          >
            {draft.checked && <Check size={22} strokeWidth={3} />}
          </button>
        ) : (
          <span className="flex-shrink-0 flex items-center justify-center" style={{ width: 44, height: 44, color: saved ? 'var(--tulsi)' : INK_55 }}>
            {saved ? <Check size={20} /> : <span className="text-[18px]">·</span>}
          </span>
        )}
        <div className="min-w-0 flex-1">
          <div className="text-[14.5px] leading-snug" style={{ color: INK_70 }}>{draft.label || 'Untitled'}</div>
          <div className="text-[20px] font-semibold leading-tight break-words" style={{ color: 'var(--kajal)' }}>
            {item.kind === 'event' ? formatDate(draft.value) : draft.value}
            {draft.unit && <span className="text-[15px] font-normal ml-1.5" style={{ color: INK_70 }}>{draft.unit}</span>}
          </div>
          {detail && <div className="text-[13.5px] mt-0.5" style={{ color: INK_70 }}>{detail}</div>}
          {item.kind === 'lab' && item.flag && FLAG_TEXT[item.flag] && (
            <div className="text-[13.5px] mt-1" style={{ color: item.flag === 'normal' ? INK_70 : 'var(--kesari)' }}>
              {FLAG_TEXT[item.flag]}
            </div>
          )}
          {!item.saveable && <div className="text-[13.5px] mt-1" style={{ color: INK_70 }}>{notSavedReason(item)}</div>}
          {readOnly && item.saveable && (
            <div className="text-[13.5px] mt-1" style={{ color: saved ? 'var(--tulsi)' : INK_70 }}>
              {saved ? 'Confirmed' : 'Left out'}
            </div>
          )}
          {!readOnly && item.saveable && (
            <div className="text-[13.5px] mt-1" style={{ color: needsLook ? 'var(--haldi-deep)' : INK_70 }}>
              {needsLook ? `Please check: ${issues.join('; ') || 'less sure about this one'}` : issues.join('; ') || 'Looks clear'}
            </div>
          )}
          {!readOnly && item.saveable && (
            <button
              type="button"
              onClick={() => setEditing(open => !open)}
              aria-expanded={editing}
              className="mt-1 -ml-2 px-2 text-[14px] font-semibold underline underline-offset-2"
              style={{ color: 'var(--sindoor)', minHeight: 44, minWidth: 44 }}
            >
              {editing ? 'Done editing' : 'Edit'}
            </button>
          )}
          {editing && !readOnly && (
            <div className="mt-2 grid gap-2">
              <Field label="Name" value={draft.label} onChange={label => onChange({ ...draft, label })} />
              <Field
                label={valueLabel}
                value={draft.value}
                inputMode={item.kind === 'lab' || item.kind === 'transaction' ? 'decimal' : 'text'}
                onChange={value => onChange({ ...draft, value, checked: true })}
              />
              {(item.kind === 'lab' || item.kind === 'field') && (
                <Field label="Unit" value={draft.unit} onChange={unit => onChange({ ...draft, unit })} />
              )}
              {item.kind === 'lab' && (
                <Field label="Range on report" value={draft.reference_range}
                  onChange={reference_range => onChange({ ...draft, reference_range })} />
              )}
              {(DETAIL_FIELDS[item.kind] ?? []).map(([key, text]) => (
                <Field key={key} label={text} value={draft.details[key] ?? ''}
                  onChange={next => onChange({ ...draft, details: { ...draft.details, [key]: next } })} />
              ))}
            </div>
          )}
        </div>
      </div>
    </li>
  )
}

// ── The screen ────────────────────────────────────────────────────────────────

function OptionToggle({ checked, onChange, title, hint }: {
  checked: boolean
  onChange: (next: boolean) => void
  title: string
  hint: string
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className="w-full flex items-center gap-3 text-left rounded-xl px-3 py-2.5"
      style={{ minHeight: 56, border: `1px solid ${LINE}`, background: 'var(--surface-raised)' }}
    >
      <span
        aria-hidden="true"
        className="flex-shrink-0 flex items-center justify-center rounded-md"
        style={{ width: 28, height: 28, border: `2px solid ${checked ? 'var(--tulsi)' : 'var(--ink-40)'}`,
          background: checked ? 'var(--tulsi)' : 'var(--field)', color: '#fff' }}
      >
        {checked && <Check size={16} strokeWidth={3} />}
      </span>
      <span className="min-w-0">
        <span className="block text-[15px]" style={{ color: 'var(--kajal)' }}>{title}</span>
        <span className="block text-[13.5px]" style={{ color: INK_70 }}>{hint}</span>
      </span>
    </button>
  )
}

export function DocumentReviewScreen({ reviewId, onClose, onSaved }: {
  reviewId: string
  onClose: () => void
  onSaved?: (reviewId: string) => void
}) {
  const [review, setReview] = useState<DocumentReview | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [drafts, setDrafts] = useState<Record<string, Draft>>({})
  const [doc, setDoc] = useState<Record<string, string>>({})
  const [options, setOptions] = useState<SaveOptions>({ medication_reminders: false, calendar_events: false, reminders: false })
  const [busy, setBusy] = useState<'' | 'save' | 'escalate' | 'discard'>('')
  const [askConsent, setAskConsent] = useState(false)
  const [result, setResult] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const next = await fetchReview(reviewId)
      setReview(next)
      setDrafts(Object.fromEntries(next.items.map(item => [item.id, draftFor(item)])))
      setDoc({
        test_date: next.document.test_date ?? '',
        account: next.document.account ?? next.document.bank ?? '',
      })
      setError(null)
    } catch (err) {
      setError(err instanceof Error && err.message !== 'Not Found' ? err.message : 'This review is not available.')
    }
  }, [reviewId])

  useEffect(() => { void load() }, [load])

  useEffect(() => {
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => {
      document.body.style.overflow = previous
      window.removeEventListener('keydown', onKey)
    }
  }, [onClose])

  const kinds = useMemo(() => new Set(review?.items.map(item => item.kind) ?? []), [review])
  const readOnly = review?.status !== 'pending'
  const tickedCount = review?.items.filter(item => item.saveable && drafts[item.id]?.checked).length ?? 0
  const needsLook = review?.items.filter(item => item.saveable && !item.default_checked).length ?? 0
  const escalation = review?.escalation
  const title = DOC_TYPE_NAMES[review?.doc_type ?? ''] ?? 'Document'

  const save = async () => {
    if (!review) return
    const items: ItemDecision[] = review.items.map(item => {
      const draft = drafts[item.id]
      if (!draft?.checked || !item.saveable) return { id: item.id, action: 'drop' }
      return {
        id: item.id,
        action: 'confirm',
        label: draft.label,
        value: draft.value,
        unit: draft.unit,
        reference_range: draft.reference_range,
        details: draft.details,
      }
    })
    const documentFields: Record<string, string> = {}
    if (kinds.has('lab')) documentFields.test_date = doc.test_date ?? ''
    if (kinds.has('transaction')) documentFields.account = doc.account ?? ''
    setBusy('save')
    try {
      const saved = await saveReview(review.review_id, items, documentFields, options)
      if (saved.status === 'needs_input') {
        toast.warning(saved.message ?? 'Something is missing.')
        return
      }
      setResult(saved.message ?? 'Saved.')
      onSaved?.(review.review_id)
      await load()
    } catch (err) {
      toast.error('Could not save', { description: err instanceof Error ? err.message : undefined })
    } finally {
      setBusy('')
    }
  }

  const escalate = async () => {
    if (!review || !escalation?.pages?.length) return
    setBusy('escalate')
    try {
      const outcome = await escalateReview(review.review_id, escalation.pages)
      if (outcome.status !== 'ok') {
        toast.warning(outcome.message ?? 'The page was not sent.')
      } else {
        toast(outcome.message ?? 'Read again.')
        await load()
      }
    } catch (err) {
      toast.error('Could not read the page again', { description: err instanceof Error ? err.message : undefined })
    } finally {
      setBusy('')
      setAskConsent(false)
    }
  }

  const discard = async () => {
    if (!review) return
    setBusy('discard')
    try {
      await discardReview(review.review_id)
      toast('Discarded. Nothing was saved.')
      onClose()
    } catch (err) {
      toast.error('Could not discard', { description: err instanceof Error ? err.message : undefined })
    } finally {
      setBusy('')
    }
  }

  return (
    <div
      className="fixed inset-0 flex justify-center"
      style={{ zIndex: 60, background: 'rgba(33,31,28,0.42)' }}
      role="dialog"
      aria-modal="true"
      aria-label={`${title} review`}
    >
      <div
        className="flex flex-col w-full h-full sm:max-w-[560px] sm:my-6 sm:h-auto sm:rounded-2xl overflow-hidden"
        style={{ background: 'var(--paper)', maxHeight: '100dvh' }}
      >
        <header
          className="flex items-center gap-2 px-2 flex-shrink-0"
          style={{ minHeight: 56, paddingTop: 'env(safe-area-inset-top)', borderBottom: `1px solid ${LINE}` }}
        >
          <button type="button" onClick={onClose} aria-label="Back to chat"
            className="flex items-center justify-center rounded-full" style={{ width: 44, height: 44, color: 'var(--kajal)' }}>
            <ChevronLeft size={22} />
          </button>
          <div className="min-w-0 flex-1">
            <h1 className="text-[16.5px] font-semibold truncate" style={{ color: 'var(--kajal)' }}>{title}</h1>
            {review && (
              <div className="text-[13px] truncate" style={{ color: INK_55 }}>
                {review.source_name} · {review.items.length} value{review.items.length === 1 ? '' : 's'}
              </div>
            )}
          </div>
          <button type="button" onClick={onClose} aria-label="Close"
            className="flex items-center justify-center rounded-full" style={{ width: 44, height: 44, color: INK_55 }}>
            <X size={20} />
          </button>
        </header>

        <div className="flex-1 min-h-0 overflow-y-auto px-4 pb-6" style={{ overscrollBehavior: 'contain' }}>
          {!review && !error && (
            <div role="status" className="flex items-center justify-center gap-2 py-16 text-[14.5px]" style={{ color: INK_55 }}>
              <Loader size={16} className="animate-spin" aria-hidden="true" /> Opening the review
            </div>
          )}
          {error && <p role="alert" className="py-16 text-center text-[15px]" style={{ color: INK_70 }}>{error}</p>}

          {review && (
            <>
              <p className="text-[14.5px] leading-relaxed mt-4" style={{ color: INK_70 }}>
                {readOnly
                  ? `Saved ${review.saved?.at ? formatDate(review.saved.at.slice(0, 10)) : ''}. Ticked values are in your records.`
                  : 'Check each value against the photo. Only ticked values are saved.'}
                {!readOnly && needsLook > 0 && ` ${count(needsLook, 'needs a closer look and starts', 'need a closer look and start')} unticked.`}
                {review.rejected_count > 0 && ` ${count(review.rejected_count, 'was', 'were')} left out because ${review.rejected_count === 1 ? 'it was' : 'they were'} not on the page.`}
              </p>

              {result && (
                <div role="status" className="mt-3 rounded-xl px-3 py-2.5 text-[14.5px]" style={{ background: 'rgba(var(--rgb-tulsi),0.1)', color: 'var(--tulsi)' }}>
                  {result}
                </div>
              )}

              {(review.document.patient_name || review.document.prescriber) && (
                <div className="mt-3 text-[14.5px]" style={{ color: INK_70 }}>
                  {review.document.patient_name && <div>Name on the document: <strong>{review.document.patient_name}</strong></div>}
                  {review.document.prescriber && <div>Prescribed by: {review.document.prescriber}</div>}
                </div>
              )}

              {!readOnly && kinds.has('lab') && (
                <div className="mt-3">
                  <label className="flex flex-col gap-1 text-[13.5px] font-medium" style={{ color: INK_70 }}>
                    Date of the test
                    <input
                      type="date"
                      value={doc.test_date ?? ''}
                      onChange={event => setDoc(current => ({ ...current, test_date: event.target.value }))}
                      className="n-field outline-none"
                    />
                  </label>
                </div>
              )}
              {!readOnly && kinds.has('transaction') && (
                <div className="mt-3">
                  <Field label="Account" value={doc.account ?? ''} onChange={account => setDoc(current => ({ ...current, account }))} />
                </div>
              )}

              {!readOnly && escalation?.suggested && escalation.pages?.length ? (
                <div className="mt-4 rounded-xl px-3 py-3" style={{ border: `1px solid ${LINE}`, background: 'var(--surface-raised)' }}>
                  <div className="text-[14.5px]" style={{ color: 'var(--kajal)' }}>
                    {escalation.reason ?? 'Some of this page was hard to read on this Mac.'}
                  </div>
                  {!askConsent ? (
                    <button
                      type="button"
                      onClick={() => setAskConsent(true)}
                      className="n-btn n-btn-block mt-2"
                    >
                      Ask {escalation.label ?? 'a trusted model'} to read {escalation.pages.length > 1 ? 'these pages' : 'this page'}
                    </button>
                  ) : (
                    <div className="mt-2">
                      <p className="text-[13.5px] leading-relaxed" style={{ color: INK_70 }}>
                        {escalation.tier === 'local'
                          ? `Page ${escalation.pages.join(', ')} will be read again by the vision model on this Mac.`
                          : `The image of page ${escalation.pages.join(', ')} will leave this Mac for this one read by ${escalation.label}. `
                            + 'It is logged in your privacy record. You still tick each value it reads.'}
                      </p>
                      <div className="mt-2 flex gap-2">
                        <button type="button" onClick={() => setAskConsent(false)} className="n-btn flex-1">
                          Not now
                        </button>
                        <button type="button" onClick={() => void escalate()} disabled={busy !== ''}
                          className="n-btn n-btn-primary flex-1">
                          {busy === 'escalate' ? 'Reading…' : 'Send and read'}
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              ) : null}

              <ul className="mt-4">
                {review.items.map(item => (
                  <ReviewRow
                    key={item.id}
                    item={item}
                    draft={drafts[item.id] ?? draftFor(item)}
                    readOnly={readOnly}
                    escalatedBy={escalation?.done?.label}
                    onChange={next => setDrafts(current => ({ ...current, [item.id]: next }))}
                  />
                ))}
              </ul>

              {!readOnly && (kinds.has('medicine') || kinds.has('event')) && (
                <div className="mt-2 grid gap-2">
                  {kinds.has('medicine') && (
                    <OptionToggle
                      checked={options.medication_reminders}
                      onChange={medication_reminders => setOptions(current => ({ ...current, medication_reminders }))}
                      title="Set medicine reminders"
                      hint="A daily reminder for each ticked medicine, at the times it says."
                    />
                  )}
                  {kinds.has('event') && (
                    <>
                      <OptionToggle
                        checked={options.calendar_events}
                        onChange={calendar_events => setOptions(current => ({ ...current, calendar_events }))}
                        title="Add to Google Calendar"
                        hint="Each ticked date becomes an event in your calendar."
                      />
                      <OptionToggle
                        checked={options.reminders}
                        onChange={reminders => setOptions(current => ({ ...current, reminders }))}
                        title="Remind me on the day"
                        hint="A reminder on this phone at 9 am, or at the time given."
                      />
                    </>
                  )}
                </div>
              )}
            </>
          )}
        </div>

        {review && !readOnly && (
          <footer
            className="flex gap-2 px-4 pt-3 flex-shrink-0"
            style={{ borderTop: `1px solid ${LINE}`, paddingBottom: 'max(12px, env(safe-area-inset-bottom))' }}
          >
            <button type="button" onClick={() => void discard()} disabled={busy !== ''}
              className="n-btn n-btn-danger">
              Discard
            </button>
            <button type="button" onClick={() => void save()} disabled={busy !== '' || tickedCount === 0}
              className="n-btn n-btn-go flex-1" style={{ fontSize: 15 }}>
              {busy === 'save' ? 'Saving…' : `Save ${tickedCount} value${tickedCount === 1 ? '' : 's'}`}
            </button>
          </footer>
        )}
        {review && readOnly && (
          <footer className="px-4 pt-3 flex-shrink-0" style={{ borderTop: `1px solid ${LINE}`, paddingBottom: 'max(12px, env(safe-area-inset-bottom))' }}>
            <button type="button" onClick={onClose} className="n-btn n-btn-primary n-btn-block" style={{ fontSize: 15 }}>
              Back to chat
            </button>
          </footer>
        )}
      </div>
    </div>
  )
}

// ── Chat card + deep link ─────────────────────────────────────────────────────

function ReviewCard({ notice, saved, onOpen }: { notice: DocumentReviewNotice; saved: boolean; onOpen: () => void }) {
  const title = DOC_TYPE_NAMES[notice.doc_type ?? ''] ?? 'Document'
  const total = notice.item_count ?? 0
  return (
    <div className="chat-card">
      <section
        className="n-card"
        style={{ ['--card-accent' as string]: saved ? 'var(--tulsi)' : 'var(--avatar-matsya)' }}
        aria-label={`${title}: ${total} value${total === 1 ? '' : 's'} to check`}
      >
        <div className="n-card-head">
          <span className="n-chip" style={{ color: 'var(--avatar-matsya)', background: 'rgba(var(--rgb-matsya),0.08)', borderColor: 'rgba(var(--rgb-matsya),0.28)' }}>
            <FileText size={14} aria-hidden="true" />
            {title}
          </span>
          <span className="n-status" style={saved ? { color: 'var(--tulsi)' } : { color: 'var(--avatar-matsya)' }}>
            {saved ? 'Saved' : 'Check before saving'}
          </span>
        </div>
        <p className="n-card-title">{total} value{total === 1 ? '' : 's'} to check</p>
        <p className="n-card-body">
          {saved
            ? 'Saved. Open it to see what was kept.'
            : `Nothing is saved until you tick each value against the photo.${notice.needs_a_look ? ` ${count(notice.needs_a_look, 'needs', 'need')} a closer look.` : ''}`}
        </p>
        <div className="n-card-actions">
          <button
            type="button"
            onClick={onOpen}
            className={saved ? 'n-btn n-btn-block' : 'n-btn n-btn-primary n-btn-block'}
          >
            {saved ? 'Open' : 'Check and save'}
          </button>
        </div>
      </section>
    </div>
  )
}

export function DocumentReviewHost() {
  const [notices, setNotices] = useState<DocumentReviewNotice[]>([])
  const [saved, setSaved] = useState<Set<string>>(() => new Set())
  const [openId, setOpenId] = useState<string | null>(() => (typeof window === 'undefined' ? null : readDeepLink()))

  useEffect(() => {
    const onNotice = (event: Event) => {
      const notice = (event as CustomEvent<DocumentReviewNotice>).detail
      if (!notice?.review_id) return
      setNotices(current => current.some(item => item.review_id === notice.review_id) ? current : [...current, notice])
    }
    const onPopState = () => setOpenId(readDeepLink())
    window.addEventListener(DOCUMENT_REVIEW_EVENT, onNotice)
    window.addEventListener('popstate', onPopState)
    return () => {
      window.removeEventListener(DOCUMENT_REVIEW_EVENT, onNotice)
      window.removeEventListener('popstate', onPopState)
    }
  }, [])

  const close = useCallback(() => {
    setOpenId(null)
    clearDeepLink()
  }, [])

  return (
    <>
      {notices.map(notice => (
        <ReviewCard
          key={notice.review_id}
          notice={notice}
          saved={saved.has(notice.review_id)}
          onOpen={() => setOpenId(notice.review_id)}
        />
      ))}
      {openId && createPortal(
        <DocumentReviewScreen
          reviewId={openId}
          onClose={close}
          onSaved={id => setSaved(current => new Set(current).add(id))}
        />,
        document.body,
      )}
    </>
  )
}
