/**
 * Anumati approval card: one exact side effect waiting for this person's OK.
 *
 * Rendered inline in the chat when a tool asks for approval (the
 * `approval_requested` stream event), and in a bottom sheet when the app is
 * opened from a notification at /?approval=<id>. The card shows the proposal
 * the server stored; approving runs exactly that, server-side, once.
 *
 * Phone-first: 48px tap targets, a screenshot box with a fixed aspect ratio,
 * and a footer of fixed height that swaps buttons for the outcome in place.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Check,
  Clock,
  Globe,
  Loader,
  Mail,
  Monitor,
  Pencil,
  Route,
  ShieldAlert,
  ShieldCheck,
  Smartphone,
  X,
} from 'lucide-react'
import {
  apiPath,
  decideApproval,
  editApproval,
  fetchApproval,
  type ApprovalEdit,
  type ApprovalPreview,
  type ApprovalProposal,
} from '@/lib/api'

const RUNNING = new Set(['approved', 'executing'])
const POLL_MS = 2_000
const POLL_LIMIT_MS = 10 * 60 * 1000
const FOOTER_MIN_HEIGHT = 56

export type ApprovalChange = (next: ApprovalProposal, replaces?: string) => void

function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!active) return
    setNow(Date.now())
    const timer = window.setInterval(() => setNow(Date.now()), 15_000)
    return () => window.clearInterval(timer)
  }, [active])
  return now
}

function expiry(expiresAt: string, now: number): { label: string; expired: boolean } {
  const ms = Date.parse(expiresAt) - now
  if (!Number.isFinite(ms)) return { label: '', expired: false }
  if (ms <= 0) return { label: 'Expired', expired: true }
  const minutes = Math.ceil(ms / 60_000)
  return { label: minutes >= 120 ? `Expires in ${Math.round(minutes / 60)} h` : `Expires in ${minutes} min`, expired: false }
}

function SurfaceIcon({ surface }: { surface: string }) {
  const size = 13
  if (surface === 'email') return <Mail size={size} />
  if (surface === 'phone') return <Smartphone size={size} />
  if (surface === 'desktop') return <Monitor size={size} />
  if (surface === 'workflow') return <Route size={size} />
  return <Globe size={size} />
}

function hostOf(url?: string): string {
  if (!url) return ''
  try {
    return new URL(url).hostname.replace(/^www\./, '')
  } catch {
    return url
  }
}

function Field({ label, value }: { label: string; value?: string }) {
  if (!value) return null
  return (
    <div className="flex gap-2 text-[12.5px] leading-snug">
      <span className="font-mono text-[10px] uppercase tracking-wider pt-[3px] w-[52px] shrink-0" style={{ color: 'var(--ink-55)' }}>
        {label}
      </span>
      <span className="min-w-0 break-words" style={{ color: 'var(--kajal)' }}>{value}</span>
    </div>
  )
}

function EmailPreview({ preview }: { preview: ApprovalPreview }) {
  return (
    <div className="flex flex-col gap-1">
      <Field label="To" value={(preview.to ?? []).join(', ')} />
      <Field label="Cc" value={(preview.cc ?? []).join(', ')} />
      <Field label="Subject" value={preview.subject} />
      {preview.body && (
        <div
          className="mt-1.5 rounded px-3 py-2 text-[12.5px] leading-relaxed whitespace-pre-wrap break-words overflow-y-auto"
          style={{ maxHeight: 180, background: 'var(--surface-2)', border: 'var(--folk-border)' }}
        >
          {preview.body}
        </div>
      )}
    </div>
  )
}

function ScreenPreview({ preview }: { preview: ApprovalPreview }) {
  const [failed, setFailed] = useState(false)
  const shot = preview.screenshot_url && !failed ? apiPath(preview.screenshot_url) : null
  return (
    <div className="flex flex-col gap-1.5">
      <Field label="Page" value={[hostOf(preview.page_url), preview.page_title].filter(Boolean).join(' · ')} />
      {preview.signed_in && <Field label="Browser" value="Your signed-in browser" />}
      {preview.warning && (
        <div
          className="flex gap-2 items-start rounded px-3 py-2 text-[12px] leading-snug"
          style={{ background: 'rgba(var(--rgb-sindoor), 0.08)', color: 'var(--kesari)', border: '1px solid rgba(var(--rgb-sindoor), 0.25)' }}
        >
          <ShieldAlert size={14} className="shrink-0 mt-px" />
          {preview.warning}
        </div>
      )}
      {preview.screenshot_url && (
        // The box keeps its size while the image loads, so nothing below it jumps.
        <div
          className="w-full rounded overflow-hidden"
          style={{ aspectRatio: '3 / 2', background: 'var(--surface-2)', border: 'var(--folk-border)' }}
        >
          {shot ? (
            <a href={shot} target="_blank" rel="noopener noreferrer" aria-label="Open the screenshot">
              <img src={shot} alt="The page as it will be submitted" loading="lazy" onError={() => setFailed(true)} className="w-full h-full object-contain" />
            </a>
          ) : (
            <div className="w-full h-full flex items-center justify-center text-[11px]" style={{ color: 'var(--ink-55)' }}>
              Screenshot unavailable
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function OtherPreview({ proposal }: { proposal: ApprovalProposal }) {
  const preview = proposal.preview
  if (proposal.surface === 'http') {
    return (
      <div className="flex flex-col gap-1">
        <Field label="Sends" value={[preview.method, hostOf(preview.url)].filter(Boolean).join(' to ')} />
        <Field label="Address" value={preview.url} />
        {preview.body && (
          <div className="mt-1 text-[12.5px] leading-relaxed whitespace-pre-wrap break-words overflow-y-auto" style={{ maxHeight: 160, color: 'var(--ink-70)' }}>
            {preview.body}
          </div>
        )}
      </div>
    )
  }
  if (proposal.surface === 'workflow') {
    return (
      <div className="flex flex-col gap-1">
        <Field label="Path" value={preview.run_title} />
        <Field label="Step" value={preview.stage_title} />
        {preview.text && (
          <div className="mt-1 text-[12.5px] leading-relaxed whitespace-pre-wrap break-words overflow-y-auto" style={{ maxHeight: 160, color: 'var(--ink-70)' }}>
            {preview.text}
          </div>
        )}
      </div>
    )
  }
  return (
    <div className="flex flex-col gap-1">
      <Field label="Device" value={preview.device ?? (proposal.surface === 'desktop' ? 'Narad host computer' : undefined)} />
      <Field label="Mode" value={preview.mode} />
      <Field label="Why" value={preview.reason} />
    </div>
  )
}

function EditForm({
  proposal,
  busy,
  onSave,
  onCancel,
}: {
  proposal: ApprovalProposal
  busy: boolean
  onSave: (changes: ApprovalEdit) => void
  onCancel: () => void
}) {
  const [draft, setDraft] = useState<Required<ApprovalEdit>>({
    to: (proposal.preview.to ?? []).join(', '),
    cc: (proposal.preview.cc ?? []).join(', '),
    subject: proposal.preview.subject ?? '',
    body: proposal.preview.body ?? '',
  })
  const input = 'w-full rounded px-3 text-[14px] outline-none'
  const inputStyle = { minHeight: 44, background: 'var(--surface)', border: '1px solid var(--ink-12)', color: 'var(--kajal)' }
  const set = (key: keyof ApprovalEdit) => (event: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
    setDraft(current => ({ ...current, [key]: event.target.value }))
  return (
    <form
      className="flex flex-col gap-2"
      onSubmit={event => {
        event.preventDefault()
        onSave(draft)
      }}
    >
      {(['to', 'cc', 'subject'] as const).map(key => (
        <label key={key} className="flex flex-col gap-1">
          <span className="font-mono text-[10px] uppercase tracking-wider" style={{ color: 'var(--ink-55)' }}>{key}</span>
          <input className={input} style={inputStyle} value={draft[key]} onChange={set(key)} inputMode={key === 'subject' ? 'text' : 'email'} />
        </label>
      ))}
      <label className="flex flex-col gap-1">
        <span className="font-mono text-[10px] uppercase tracking-wider" style={{ color: 'var(--ink-55)' }}>body</span>
        <textarea className={`${input} py-2 leading-relaxed`} style={{ ...inputStyle, minHeight: 140 }} value={draft.body} onChange={set('body')} />
      </label>
      <div className="flex gap-2 pt-1">
        <button type="button" onClick={onCancel} disabled={busy} className="flex-1 rounded-[10px] text-[13px] font-semibold" style={{ minHeight: 48, border: '1px solid var(--ink-12)', background: 'var(--surface)', color: 'var(--kajal)' }}>
          Cancel
        </button>
        <button type="submit" disabled={busy} className="flex-[2] rounded-[10px] text-[13px] font-semibold inline-flex items-center justify-center gap-2" style={{ minHeight: 48, border: 0, background: 'var(--kajal)', color: 'var(--paper)' }}>
          {busy ? <Loader size={15} className="animate-spin" /> : <Check size={15} />}
          Save changes
        </button>
      </div>
    </form>
  )
}

const OUTCOMES: Record<string, { label: string; colour: string; icon: 'done' | 'failed' | 'closed' | 'running' }> = {
  approved: { label: 'Approved. Running it now…', colour: 'var(--tulsi)', icon: 'running' },
  executing: { label: 'Approved. Running it now…', colour: 'var(--tulsi)', icon: 'running' },
  executed: { label: 'Done', colour: 'var(--tulsi)', icon: 'done' },
  failed: { label: 'Approved, but it did not go through', colour: 'var(--kesari)', icon: 'failed' },
  rejected: { label: 'You declined this', colour: 'var(--loha)', icon: 'closed' },
  expired: { label: 'Expired. Ask Narad again if you still want it', colour: 'var(--loha)', icon: 'closed' },
  edited: { label: 'Replaced by your edited version', colour: 'var(--loha)', icon: 'closed' },
}

function Outcome({ proposal, expired }: { proposal: ApprovalProposal; expired: boolean }) {
  const status = proposal.status === 'pending' && expired ? 'expired' : proposal.status
  const outcome = OUTCOMES[status] ?? { label: status, colour: 'var(--loha)', icon: 'closed' as const }
  const detail = status === 'rejected' ? proposal.decision_reason : proposal.result?.summary
  return (
    <div className="flex items-start gap-2.5 py-1" role="status" aria-live="polite">
      <span className="shrink-0 mt-0.5 inline-flex items-center justify-center rounded-full" style={{ width: 22, height: 22, background: outcome.colour, color: 'var(--paper)' }}>
        {outcome.icon === 'running' ? <Loader size={13} className="animate-spin" />
          : outcome.icon === 'done' ? <Check size={13} />
          : <X size={13} />}
      </span>
      <div className="min-w-0">
        <p className="text-[13px] font-semibold" style={{ color: outcome.colour }}>{outcome.label}</p>
        {detail && <p className="text-[12px] leading-snug mt-0.5 break-words" style={{ color: 'var(--ink-70)' }}>{detail}</p>}
      </div>
    </div>
  )
}

export function ApprovalCard({ proposal: incoming, onChange }: { proposal: ApprovalProposal; onChange?: ApprovalChange }) {
  const [proposal, setProposal] = useState(incoming)
  const [busy, setBusy] = useState<null | 'approve' | 'reject' | 'edit'>(null)
  const [editing, setEditing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const onChangeRef = useRef(onChange)
  onChangeRef.current = onChange
  const pending = proposal.status === 'pending'
  const now = useNow(pending)
  const { label: expiryText, expired } = expiry(proposal.expires_at, now)
  const open = pending && !expired

  // A newer copy from the stream (the same proposal, decided elsewhere) wins.
  useEffect(() => {
    setProposal(current => (current.id === incoming.id && current.status === incoming.status ? current : incoming))
  }, [incoming])

  const commit = useCallback((next: ApprovalProposal, replaces?: string) => {
    setProposal(next)
    onChangeRef.current?.(next, replaces)
  }, [])

  // On mount: it may have been decided on another device, or have expired.
  useEffect(() => {
    if (!pending && !RUNNING.has(proposal.status)) return
    const controller = new AbortController()
    fetchApproval(proposal.id, controller.signal)
      .then(next => { if (next.status !== proposal.status) commit(next) })
      .catch(() => {})
    return () => controller.abort()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [proposal.id])

  // While the approved action runs on the server (a phone task can take
  // minutes), poll until it has a result.
  useEffect(() => {
    if (!RUNNING.has(proposal.status)) return
    const started = Date.now()
    let cancelled = false
    let timer = 0
    const tick = async () => {
      if (cancelled || Date.now() - started > POLL_LIMIT_MS) return
      try {
        const next = await fetchApproval(proposal.id)
        if (cancelled) return
        if (next.status !== proposal.status) {
          commit(next)
          return
        }
      } catch {
        // offline for a moment: keep polling
      }
      timer = window.setTimeout(tick, POLL_MS)
    }
    timer = window.setTimeout(tick, POLL_MS)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [proposal.id, proposal.status, commit])

  const decide = async (verdict: 'approve' | 'reject') => {
    setBusy(verdict)
    setError(null)
    try {
      commit(await decideApproval(proposal.id, verdict))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not reach Narad.')
      fetchApproval(proposal.id).then(next => commit(next)).catch(() => {})
    } finally {
      setBusy(null)
    }
  }

  const saveEdit = async (changes: ApprovalEdit) => {
    setBusy('edit')
    setError(null)
    try {
      const next = await editApproval(proposal.id, changes)
      setEditing(false)
      commit(next, proposal.id)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save the changes.')
    } finally {
      setBusy(null)
    }
  }

  const preview = proposal.preview ?? {}
  const kind = preview.kind ?? proposal.surface
  const accent = open ? 'var(--sindoor)' : RUNNING.has(proposal.status) || proposal.status === 'executed' ? 'var(--tulsi)' : 'var(--loha)'

  return (
    <div
      className="folk-card folk-shadow rounded-[4px_16px_16px_16px] px-4 py-3.5 w-full"
      style={{ borderLeft: `3px solid ${accent}`, color: 'var(--kajal)' }}
      aria-label={`Approval: ${proposal.summary}`}
    >
      <div className="flex items-center gap-2 flex-wrap">
        <span
          className="text-chip px-2 py-1 rounded organic-border inline-flex items-center gap-1.5 uppercase"
          style={{ color: 'var(--sindoor)', borderColor: 'rgba(var(--rgb-sindoor), 0.30)', background: 'rgba(var(--rgb-sindoor), 0.07)' }}
        >
          <SurfaceIcon surface={proposal.surface} />
          {proposal.risk_label || proposal.risk_class}
        </span>
        <span className="font-mono text-[10px] uppercase tracking-wider" style={{ color: 'var(--ink-55)' }}>
          {open ? 'Needs your OK' : 'Approval'}
        </span>
        {pending && expiryText && (
          <span className="ml-auto inline-flex items-center gap-1 font-mono text-[10px]" style={{ color: expired ? 'var(--kesari)' : 'var(--ink-55)' }}>
            <Clock size={11} />
            {expiryText}
          </span>
        )}
      </div>

      <p className="mt-2 text-[14px] leading-snug font-semibold break-words">{proposal.summary}</p>

      <div className="mt-2.5">
        {editing ? (
          <EditForm proposal={proposal} busy={busy === 'edit'} onSave={changes => void saveEdit(changes)} onCancel={() => setEditing(false)} />
        ) : kind === 'email' ? (
          <EmailPreview preview={preview} />
        ) : kind === 'browser' ? (
          <ScreenPreview preview={preview} />
        ) : (
          <OtherPreview proposal={proposal} />
        )}
      </div>

      {!editing && (
        <div className="mt-3 pt-3 flex flex-col justify-center" style={{ borderTop: '1px dashed var(--ink-12)', minHeight: FOOTER_MIN_HEIGHT }}>
          {open ? (
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => void decide('reject')}
                disabled={busy !== null}
                className="flex-1 rounded-[10px] text-[13px] font-semibold inline-flex items-center justify-center gap-1.5"
                style={{ minHeight: 48, border: '1px solid var(--ink-12)', background: 'var(--surface)', color: 'var(--kajal)' }}
              >
                {busy === 'reject' ? <Loader size={15} className="animate-spin" /> : <X size={15} />}
                Reject
              </button>
              {proposal.editable && (
                <button
                  type="button"
                  onClick={() => setEditing(true)}
                  disabled={busy !== null}
                  className="flex-1 rounded-[10px] text-[13px] font-semibold inline-flex items-center justify-center gap-1.5"
                  style={{ minHeight: 48, border: '1px solid var(--ink-12)', background: 'var(--surface)', color: 'var(--kajal)' }}
                >
                  <Pencil size={14} />
                  Edit
                </button>
              )}
              <button
                type="button"
                onClick={() => void decide('approve')}
                disabled={busy !== null}
                className="flex-[1.6] rounded-[10px] text-[13.5px] font-bold inline-flex items-center justify-center gap-1.5"
                style={{ minHeight: 48, border: 0, background: 'var(--tulsi)', color: '#fff' }}
              >
                {busy === 'approve' ? <Loader size={15} className="animate-spin" /> : <ShieldCheck size={15} />}
                Approve
              </button>
            </div>
          ) : (
            <Outcome proposal={proposal} expired={expired} />
          )}
          {error && <p className="mt-2 text-[12px]" role="alert" style={{ color: 'var(--kesari)' }}>{error}</p>}
        </div>
      )}
    </div>
  )
}

/**
 * The app opened from an approval notification (/?approval=<id>): show that
 * card in a bottom sheet over whatever screen is open, then drop the query
 * from the URL. A page that is already open shows one for a cancellable
 * `narad:deeplink` event whose link carries `?approval=`, a
 * `narad:open-approval` window event, or a service-worker message
 * `{ type: 'narad:open-approval', proposal_id }`.
 */
export function ApprovalSheet({ onChange }: { onChange?: ApprovalChange }) {
  const [openId, setOpenId] = useState<string | null>(null)
  const [proposal, setProposal] = useState<ApprovalProposal | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const show = (id: string | null | undefined) => {
      if (!id || !/^apr_[0-9a-f]{16}$/.test(id)) return
      setOpenId(id)
      setProposal(null)
      setError(null)
    }
    const params = new URLSearchParams(window.location.search)
    const fromUrl = params.get('approval')
    if (fromUrl) {
      params.delete('approval')
      const query = params.toString()
      window.history.replaceState(window.history.state, '', `${window.location.pathname}${query ? `?${query}` : ''}${window.location.hash}`)
      show(fromUrl)
    }
    const onWindow = (event: Event) => show((event as CustomEvent<{ id?: string }>).detail?.id)
    const onWorker = (event: MessageEvent) => {
      if (event.data?.type === 'narad:open-approval') show(String(event.data.proposal_id ?? ''))
    }
    // The Activity screen and notification taps announce an in-app link first
    // (cancellable); an approval link opens here without reloading the app.
    const onDeepLink = (event: Event) => {
      const detail = (event as CustomEvent<Record<string, unknown>>).detail ?? {}
      const link = String(detail.url ?? detail.href ?? detail.path ?? '')
      let id: string | null = null
      try {
        id = new URL(link, window.location.origin).searchParams.get('approval')
      } catch {
        id = null
      }
      if (id && /^apr_[0-9a-f]{16}$/.test(id)) {
        event.preventDefault()
        show(id)
      }
    }
    window.addEventListener('narad:open-approval', onWindow)
    window.addEventListener('narad:deeplink', onDeepLink)
    navigator.serviceWorker?.addEventListener('message', onWorker)
    return () => {
      window.removeEventListener('narad:open-approval', onWindow)
      window.removeEventListener('narad:deeplink', onDeepLink)
      navigator.serviceWorker?.removeEventListener('message', onWorker)
    }
  }, [])

  useEffect(() => {
    if (!openId) return
    const controller = new AbortController()
    fetchApproval(openId, controller.signal)
      .then(setProposal)
      .catch(err => { if (!controller.signal.aborted) setError(err instanceof Error ? err.message : 'Could not load it.') })
    return () => controller.abort()
  }, [openId])

  if (!openId) return null
  const close = () => setOpenId(null)
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Approval"
      onClick={close}
      style={{ position: 'fixed', inset: 0, zIndex: 60, background: 'rgba(var(--rgb-kajal), 0.40)', display: 'flex', alignItems: 'flex-end', justifyContent: 'center' }}
    >
      <div
        onClick={event => event.stopPropagation()}
        className="w-full"
        style={{
          maxWidth: 520,
          maxHeight: '92vh',
          overflowY: 'auto',
          background: 'var(--paper)',
          borderRadius: '18px 18px 0 0',
          padding: '8px 14px calc(16px + env(safe-area-inset-bottom))',
          boxShadow: '0 -12px 40px -12px rgba(var(--rgb-kajal), 0.35)',
        }}
      >
        <div className="flex items-center justify-between">
          <span className="label-section">Narad needs your OK</span>
          <button type="button" onClick={close} aria-label="Close" className="inline-flex items-center justify-center rounded-full" style={{ width: 48, height: 48, color: 'var(--ink-70)' }}>
            <X size={20} />
          </button>
        </div>
        {proposal ? (
          <ApprovalCard
            proposal={proposal}
            onChange={(next, replaces) => {
              setProposal(next)
              onChange?.(next, replaces)
            }}
          />
        ) : error ? (
          <p className="text-[13px] py-6 text-center" style={{ color: 'var(--kesari)' }}>{error}</p>
        ) : (
          <div className="folk-card rounded-[4px_16px_16px_16px] flex items-center justify-center gap-2 text-[12px]" style={{ height: 220, color: 'var(--ink-55)' }}>
            <Loader size={15} className="animate-spin" /> Loading the approval…
          </div>
        )}
      </div>
    </div>
  )
}
