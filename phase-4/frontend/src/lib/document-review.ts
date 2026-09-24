/** Document reviews: values read from a photo or PDF wait here until the
 *  person confirms them against their crops (see phase-8/document_review.py). */
import { apiFetch } from './api'

export type ReviewItemKind = 'lab' | 'transaction' | 'medicine' | 'event' | 'field'

export interface ReviewItem {
  id: string
  kind: ReviewItemKind
  label: string
  value: string
  unit: string
  reference_range: string
  normalised_value: number | null
  flag: '' | 'high' | 'low' | 'normal'
  details: Record<string, string>
  page: number
  confidence: number
  issues: string[]
  saveable: boolean
  default_checked: boolean
  decision?: 'confirmed' | 'dropped'
  /** Set on a saved review when the person edited the value before saving. */
  confirmed_as?: Partial<Pick<ReviewItem, 'label' | 'value' | 'unit' | 'reference_range' | 'details'>>
  crop_url: string
}

export interface ReviewEscalation {
  suggested?: boolean
  pages?: number[]
  reason?: string
  label?: string
  tier?: string
  done?: { at: string; pages: number[]; label: string; tier: string }
}

export interface DocumentReview {
  review_id: string
  status: 'pending' | 'saved'
  created_at: string
  doc_type: string
  source_name: string
  pages: Array<{ page: number; width: number; height: number; source: string; poor: boolean; image_url: string }>
  document: Record<string, string>
  items: ReviewItem[]
  rejected_count: number
  escalation: ReviewEscalation
  saved?: { at: string; confirmed: string[]; notes: string[] } | null
}

export interface ItemDecision {
  id: string
  action: 'confirm' | 'drop'
  label?: string
  value?: string
  unit?: string
  reference_range?: string
  details?: Record<string, string>
}

export interface SaveOptions {
  medication_reminders: boolean
  calendar_events: boolean
  reminders: boolean
}

/** The chat card event: useAvatara re-emits the `document_review` SSE event. */
export interface DocumentReviewNotice {
  review_id: string
  doc_type?: string
  item_count?: number
  needs_a_look?: number
}

export const DOCUMENT_REVIEW_EVENT = 'narad:document-review'
export const REVIEW_ID_RE = /^rev_[a-f0-9]{16}$/

export const DOC_TYPE_NAMES: Record<string, string> = {
  lab_report: 'Lab report',
  bank_statement: 'Bank statement',
  prescription: 'Prescription',
  school_circular: 'School circular',
  bill: 'Bill',
  generic: 'Document',
}

export function emitDocumentReview(data: Record<string, unknown>): void {
  if (typeof window === 'undefined' || !REVIEW_ID_RE.test(String(data.review_id ?? ''))) return
  window.dispatchEvent(new CustomEvent<DocumentReviewNotice>(DOCUMENT_REVIEW_EVENT, {
    detail: data as unknown as DocumentReviewNotice,
  }))
}

async function json<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let detail = `Request failed (${response.status})`
    try {
      const body = await response.json() as { detail?: string }
      if (body.detail) detail = body.detail
    } catch { /* keep the status text */ }
    throw new Error(detail)
  }
  return response.json() as Promise<T>
}

export async function fetchReview(reviewId: string): Promise<DocumentReview> {
  return json(await apiFetch(`/documents/reviews/${reviewId}`))
}

/** Crops need the session header, so they load as blobs rather than <img src>. */
export async function fetchCropUrl(path: string, signal?: AbortSignal): Promise<string> {
  const response = await apiFetch(path, { signal })
  if (!response.ok) throw new Error(`Crop unavailable (${response.status})`)
  return URL.createObjectURL(await response.blob())
}

export async function saveReview(
  reviewId: string,
  items: ItemDecision[],
  document: Record<string, string>,
  options: SaveOptions,
): Promise<{ status: string; stored?: number; confirmed?: number; message?: string; field?: string }> {
  return json(await apiFetch(`/documents/reviews/${reviewId}/save`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ items, document, options }),
  }))
}

export async function escalateReview(
  reviewId: string,
  pages: number[],
): Promise<{ status: string; message?: string }> {
  return json(await apiFetch(`/documents/reviews/${reviewId}/escalate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ pages, consent: true }),
  }))
}

export async function discardReview(reviewId: string): Promise<void> {
  await json(await apiFetch(`/documents/reviews/${reviewId}/discard`, { method: 'POST' }))
}
