import { useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { ArrowLeft, LoaderCircle, ShieldCheck } from 'lucide-react'
import {
  destinationSentence,
  fetchEgress,
  providerName,
  purpose,
  refusalReason,
  tierLabel,
  type EgressRow,
  type TrustLang,
} from '@/lib/trust'

/** "What left my Mac": the caller's own egress ledger, newest first, by day.
 *  The screen docs/PILOT_CONSENT_AND_METRICS.md promises. */
export function EgressScreen({ initialLang = 'en', onClose }: { initialLang?: TrustLang; onClose: () => void }) {
  const [lang, setLang] = useState<TrustLang>(initialLang)
  const [rows, setRows] = useState<EgressRow[] | null>(null)
  const [failed, setFailed] = useState(false)
  const hi = lang === 'hi'

  useEffect(() => {
    let cancelled = false
    fetchEgress(300)
      .then(calls => { if (!cancelled) setRows(calls) })
      .catch(() => { if (!cancelled) setFailed(true) })
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const days = useMemo(() => groupByDay(rows ?? [], lang), [rows, lang])

  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-label={hi ? 'मेरे Mac से क्या बाहर गया' : 'What left my Mac'}
      lang={hi ? 'hi' : 'en'}
      className="fixed inset-0 flex flex-col"
      style={{ zIndex: 70, background: 'var(--paper)', color: 'var(--kajal)' }}
    >
      <header
        className="flex items-center gap-1 pl-1 pr-3 flex-shrink-0"
        style={{ minHeight: 'calc(56px + env(safe-area-inset-top))', paddingTop: 'env(safe-area-inset-top)', borderBottom: '1px solid var(--line)' }}
      >
        <button
          type="button"
          onClick={onClose}
          className="n-icon-btn"
          aria-label={hi ? 'वापस' : 'Back'}
        >
          <ArrowLeft size={20} />
        </button>
        <h1 className="text-[17px] font-semibold flex-1 truncate">
          {hi ? 'मेरे Mac से क्या बाहर गया' : 'What left my Mac'}
        </h1>
        <LangToggle lang={lang} onChange={setLang} />
      </header>

      <main className="flex-1 overflow-y-auto">
        <div className="w-full max-w-[560px] mx-auto px-4 pt-4 pb-10">
          <p className="text-[15px] leading-relaxed mb-5" style={{ color: 'var(--ink-70)' }}>
            {hi
              ? 'जब भी नारद ने आपके लिए इस Mac के बाहर की कोई सेवा इस्तेमाल की, वह यहां लिखा है: कौन-सी सेवा, किस काम के लिए, और कितनी निजी जानकारियां प्लेसहोल्डर से बदली गईं। आपके शब्द इस सूची में कभी नहीं रखे जाते। यह सूची सिर्फ़ आप देख सकते हैं।'
              : 'Each time Narad used a service outside this Mac for you, it is listed here: which service, what for, and how many personal details were swapped for placeholders. Your words are never kept in this list. Only you can see it.'}
          </p>

          {!rows && !failed && (
            <div role="status" className="flex items-center gap-2 text-[14.5px]" style={{ color: 'var(--ink-55)' }}>
              <LoaderCircle size={16} className="animate-spin" aria-hidden="true" /> {hi ? 'लोड हो रहा है…' : 'Loading…'}
            </div>
          )}
          {failed && (
            <p role="alert" className="text-[14.5px]" style={{ color: 'var(--sindoor)' }}>
              {hi ? 'सूची अभी खुल नहीं पाई। थोड़ी देर बाद फिर कोशिश करें।' : "The list couldn't load just now. Try again in a moment."}
            </p>
          )}
          {rows && rows.length === 0 && (
            <div className="flex items-center gap-2 text-[15px] rounded-[11px] px-3 py-3" style={{ color: 'var(--tulsi)', background: 'rgba(var(--rgb-tulsi),0.08)' }}>
              <ShieldCheck size={18} aria-hidden="true" />
              {hi ? 'अब तक आपके Mac से कुछ भी बाहर नहीं गया।' : 'Nothing has left your Mac yet.'}
            </div>
          )}

          {days.map(day => (
            <section key={day.key} className="mb-6">
              <h2 className="n-section-label" style={{ margin: '0 0 10px' }}>
                {day.label}
              </h2>
              <ul className="flex flex-col gap-2">
                {day.entries.map(entry => (
                  <li
                    key={entry.key}
                    className="rounded-[12px] px-3.5 py-3"
                    style={{ background: 'var(--surface-2)', border: '1px solid var(--line)' }}
                  >
                    <div className="flex items-baseline justify-between gap-3 mb-0.5">
                      <span className="text-[15px] font-semibold">
                        {entry.row.blocked
                          ? (hi ? 'भेजने से पहले रोका' : 'Stopped before sending')
                          : providerName(entry.row.provider, lang)}
                        {entry.count > 1 && (
                          <span className="font-normal" style={{ color: 'var(--ink-55)' }}> · {entry.count}×</span>
                        )}
                      </span>
                      <span className="text-[12.5px] flex-shrink-0" style={{ color: 'var(--ink-55)' }}>
                        {entry.time}
                      </span>
                    </div>
                    <p className="text-[14.5px] leading-relaxed" style={{ color: 'var(--ink-70)' }}>
                      {entry.row.blocked
                        ? (hi
                            ? `${providerName(entry.row.provider, lang)}, ${purpose(entry.row.source, lang)}: कुछ नहीं भेजा गया, क्योंकि ${refusalReason(entry.row.blocked, lang)}।`
                            : `${providerName(entry.row.provider, lang)}, ${purpose(entry.row.source, lang)}: nothing was sent, because ${refusalReason(entry.row.blocked, lang)}.`)
                        : destinationSentence(entry.row.provider, entry.row.tier, entry.row.source, entry.replaced, lang)}
                    </p>
                    {!entry.row.blocked && (
                      <span className="text-[12.5px]" style={{ color: 'var(--ink-55)' }}>
                        {tierLabel(entry.row.tier, lang)}
                        {!entry.row.turn_id && (hi ? ' · बातचीत के बाद' : ' · after a conversation')}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>
      </main>
    </div>,
    document.body,
  )
}

export function LangToggle({ lang, onChange }: { lang: TrustLang; onChange: (lang: TrustLang) => void }) {
  return (
    <div className="inline-flex rounded-full p-0.5 flex-shrink-0" style={{ border: '1px solid var(--ink-20)' }} role="group" aria-label="Language">
      {(['en', 'hi'] as TrustLang[]).map(option => (
        <button
          key={option}
          type="button"
          onClick={() => onChange(option)}
          aria-pressed={lang === option}
          lang={option}
          className="px-3.5 min-h-[44px] rounded-full text-[14px]"
          style={{
            background: lang === option ? 'var(--kajal)' : 'transparent',
            color: lang === option ? 'var(--paper)' : 'var(--ink-70)',
            lineHeight: 1.2,
          }}
        >
          {option === 'hi' ? 'हिन्दी' : 'English'}
        </button>
      ))}
    </div>
  )
}

interface Entry {
  key: string
  row: EgressRow
  count: number
  replaced: Record<string, number>
  time: string
}

function parseTs(ts: string): Date | null {
  // Ledger stamps look like 2026-09-24T10:02:11+0530.
  const date = new Date(ts.replace(/([+-]\d{2})(\d{2})$/, '$1:$2'))
  return Number.isNaN(date.getTime()) ? null : date
}

/** Newest first, by local day; repeats of one call in one turn fold into "3×". */
function groupByDay(rows: EgressRow[], lang: TrustLang): Array<{ key: string; label: string; entries: Entry[] }> {
  const locale = lang === 'hi' ? 'hi-IN' : 'en-IN'
  const today = new Date()
  const yesterday = new Date(today)
  yesterday.setDate(today.getDate() - 1)
  const sameDay = (a: Date, b: Date) => a.toDateString() === b.toDateString()
  const days: Array<{ key: string; label: string; entries: Entry[] }> = []
  for (const row of rows) {
    if (row.tier === 'local') continue
    const date = parseTs(row.ts)
    const dayKey = date ? date.toDateString() : 'unknown'
    let day = days[days.length - 1]
    if (!day || day.key !== dayKey) {
      const label = !date
        ? (lang === 'hi' ? 'तारीख़ नहीं' : 'Unknown day')
        : sameDay(date, today) ? (lang === 'hi' ? 'आज' : 'Today')
        : sameDay(date, yesterday) ? (lang === 'hi' ? 'कल' : 'Yesterday')
        : date.toLocaleDateString(locale, { weekday: 'short', day: 'numeric', month: 'short' })
      day = { key: dayKey, label, entries: [] }
      days.push(day)
    }
    const foldKey = [row.turn_id || 'none', row.provider, row.source, row.tier, row.blocked || ''].join('|')
    const last = day.entries[day.entries.length - 1]
    if (last && last.key.startsWith(`${foldKey}#`)) {
      last.count += 1
      for (const [kind, n] of Object.entries(row.entities ?? {})) {
        last.replaced[kind] = (last.replaced[kind] ?? 0) + (Number(n) || 0)
      }
      continue
    }
    day.entries.push({
      key: `${foldKey}#${day.entries.length}`,
      row,
      count: 1,
      replaced: { ...(row.entities ?? {}) },
      time: date ? date.toLocaleTimeString(locale, { hour: 'numeric', minute: '2-digit' }) : '',
    })
  }
  return days
}
