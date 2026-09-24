import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Cloud, Globe, Search, ShieldCheck, ThumbsDown, ThumbsUp, X } from 'lucide-react'
import type { Message } from '../hooks/useAvatara'
import {
  FEEDBACK_REASONS,
  destinationSentence,
  postFeedback,
  receiptChipLabel,
  refusalReason,
  textLang,
  type PrivacyReceipt,
  type TrustLang,
} from '@/lib/trust'
import { EgressScreen } from './EgressScreen'

/** Under each finished answer: where it went (privacy receipt) and a quiet
 *  thumbs up/down. Renders nothing for answers that carry neither. */
export function MessageFooter({ message, userId }: { message: Message; userId: string }) {
  const [sheetOpen, setSheetOpen] = useState(false)
  const [ledgerOpen, setLedgerOpen] = useState(false)
  const receipt = message.privacyReceipt
  const turnId = message.turnId
  if (!receipt && !turnId) return null
  const lang = textLang(message.text)

  return (
    <div className="flex items-center flex-wrap gap-x-1 mt-0.5 max-w-full" lang={lang === 'hi' ? 'hi' : undefined}>
      {receipt && <ReceiptChip receipt={receipt} lang={lang} onOpen={() => setSheetOpen(true)} />}
      {turnId && message.sessionId && (
        <FeedbackControl userId={userId} sessionId={message.sessionId} turnId={turnId} lang={lang} />
      )}
      {sheetOpen && receipt && (
        <ReceiptSheet
          receipt={receipt}
          lang={lang}
          onClose={() => setSheetOpen(false)}
          onOpenLedger={() => { setSheetOpen(false); setLedgerOpen(true) }}
        />
      )}
      {ledgerOpen && <EgressScreen initialLang={lang} onClose={() => setLedgerOpen(false)} />}
    </div>
  )
}

function ReceiptIcon({ receipt }: { receipt: PrivacyReceipt }) {
  if (receipt.stayed_local || receipt.destinations.length === 0) return <ShieldCheck size={14} aria-hidden="true" />
  const tiers = receipt.destinations.map(d => d.tier)
  if (tiers.includes('trusted') || tiers.includes('redact')) return <Cloud size={14} aria-hidden="true" />
  return receipt.destinations.some(d => d.sources.includes('search')) ? <Search size={14} aria-hidden="true" /> : <Globe size={14} aria-hidden="true" />
}

function ReceiptChip({ receipt, lang, onOpen }: { receipt: PrivacyReceipt; lang: TrustLang; onOpen: () => void }) {
  const local = receipt.stayed_local || receipt.destinations.length === 0
  return (
    // The pill is small; the button around it is a full 44 px tap target.
    <button
      type="button"
      onClick={onOpen}
      className="inline-flex items-center min-h-[44px] max-w-full text-left"
      title={lang === 'hi' ? 'इस जवाब के लिए Mac से क्या बाहर गया' : 'What left your Mac for this answer'}
    >
      <span
        className="inline-flex items-center gap-1.5 rounded-[14px] px-2.5 py-1 text-[12.5px] leading-snug min-w-0"
        style={{
          color: local ? 'var(--tulsi)' : 'var(--ink-70)',
          background: local ? 'rgba(var(--rgb-tulsi),0.08)' : 'var(--ink-05)',
          border: `1px solid ${local ? 'rgba(var(--rgb-tulsi),0.22)' : 'var(--ink-12)'}`,
        }}
      >
        <ReceiptIcon receipt={receipt} />
        <span className="min-w-0">{receiptChipLabel(receipt, lang)}</span>
      </span>
    </button>
  )
}

function ReceiptSheet({
  receipt, lang, onClose, onOpenLedger,
}: {
  receipt: PrivacyReceipt
  lang: TrustLang
  onClose: () => void
  onOpenLedger: () => void
}) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const hi = lang === 'hi'
  const local = receipt.stayed_local || receipt.destinations.length === 0
  const refused = Object.entries(receipt.refused ?? {})

  return createPortal(
    <div
      className="fixed inset-0 flex items-end sm:items-center justify-center"
      style={{ zIndex: 60, background: 'rgba(0,0,0,0.38)' }}
      onClick={onClose}
      role="presentation"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={hi ? 'इस जवाब की प्राइवेसी रसीद' : 'Privacy receipt for this answer'}
        lang={hi ? 'hi' : 'en'}
        className="w-full sm:max-w-[480px] max-h-[80dvh] overflow-y-auto px-5 pt-2 sm:rounded-2xl"
        style={{
          background: 'var(--paper)',
          color: 'var(--kajal)',
          borderRadius: '18px 18px 0 0',
          paddingBottom: 'max(20px, env(safe-area-inset-bottom))',
          boxShadow: '0 -12px 40px rgba(0,0,0,0.22)',
        }}
        onClick={event => event.stopPropagation()}
      >
        <div aria-hidden="true" className="mx-auto mb-2 rounded-full" style={{ width: 36, height: 4, background: 'var(--ink-20)' }} />
        <div className="flex items-start justify-between gap-3 mb-3">
          <h2 className="text-[17px] font-semibold leading-snug pt-2">
            {hi ? 'इस जवाब के लिए Mac से क्या बाहर गया' : 'What left your Mac for this answer'}
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="n-icon-btn -mr-2.5"
            style={{ color: 'var(--ink-55)' }}
            aria-label={hi ? 'बंद करें' : 'Close'}
          >
            <X size={20} />
          </button>
        </div>

        {local ? (
          <p className="text-[15px] leading-relaxed mb-3">
            {hi
              ? 'इस जवाब के लिए कुछ भी आपके Mac से बाहर नहीं गया। इसे इसी Mac पर चलने वाले मॉडल ने लिखा, या नारद ने खुद जवाब दिया।'
              : 'Nothing left your Mac for this answer. The model on this Mac wrote it, or Narad answered by itself.'}
          </p>
        ) : (
          <ul className="flex flex-col gap-2.5 mb-3">
            {receipt.destinations.map(destination => (
              <li key={`${destination.provider}:${destination.tier}`} className="text-[15px] leading-relaxed">
                {destination.sources.map(source => (
                  <span key={source} className="block">
                    {destinationSentence(destination.provider, destination.tier, source, destination.replaced, lang)}
                  </span>
                ))}
              </li>
            ))}
          </ul>
        )}

        {refused.length > 0 && (
          <p className="text-[14px] leading-relaxed mb-3" style={{ color: 'var(--ink-70)' }}>
            {refused.map(([reason, count]) => (hi
              ? `नारद ने ${count} बार भेजने से पहले ही रोक दिया, क्योंकि ${refusalReason(reason, lang)}। कुछ नहीं भेजा गया।`
              : `Narad stopped ${count === 1 ? 'one call' : `${count} calls`} before sending, because ${refusalReason(reason, lang)}. Nothing was sent.`
            )).join(' ')}
          </p>
        )}

        <p className="text-[13.5px] leading-relaxed mb-4" style={{ color: 'var(--ink-70)' }}>
          {hi
            ? 'यह रसीद सिर्फ़ गिनती रखती है: किसने देखा और कितनी जानकारियां बदली गईं। आपके शब्द इसमें कभी नहीं रहते।'
            : 'This receipt keeps counts only: who saw something, and how many details were replaced. Your words are never kept in it.'}
        </p>

        <button
          type="button"
          onClick={onOpenLedger}
          className="n-btn n-btn-block"
        >
          {hi ? 'मेरे Mac से क्या बाहर गया, पूरी सूची देखें' : 'See everything that left your Mac'}
        </button>
      </div>
    </div>,
    document.body,
  )
}

// A per-viewer convenience: the chosen rating shows again after a reload.
function feedbackKey(userId: string, turnId: string): string {
  return `narad_feedback:${userId}:${turnId}`
}

function readFeedback(key: string): { rating: 'up' | 'down' | null; reason: string | null } {
  try {
    const [rating, reason] = (localStorage.getItem(key) ?? '').split(':')
    return {
      rating: rating === 'up' || rating === 'down' ? rating : null,
      reason: reason || null,
    }
  } catch {
    return { rating: null, reason: null }
  }
}

function writeFeedback(key: string, rating: 'up' | 'down', reason: string | null): void {
  try { localStorage.setItem(key, reason ? `${rating}:${reason}` : rating) } catch { /* optional */ }
}

function FeedbackControl({
  userId, sessionId, turnId, lang,
}: {
  userId: string
  sessionId: string
  turnId: string
  lang: TrustLang
}) {
  const key = feedbackKey(userId, turnId)
  const [state, setState] = useState(() => readFeedback(key))
  const [askingWhy, setAskingWhy] = useState(false)
  const reasonsRef = useRef<HTMLDivElement>(null)
  const hi = lang === 'hi'

  useEffect(() => {
    if (askingWhy) reasonsRef.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }, [askingWhy])

  const rate = (rating: 'up' | 'down', reason: string | null = null) => {
    setState({ rating, reason })
    writeFeedback(key, rating, reason)
    setAskingWhy(rating === 'down' && reason === null)
    void postFeedback({ session_id: sessionId, turn_id: turnId, rating, reason })
  }

  const button = (rating: 'up' | 'down') => {
    const chosen = state.rating === rating
    const Icon = rating === 'up' ? ThumbsUp : ThumbsDown
    const label = rating === 'up'
      ? (hi ? 'मददगार' : 'Helpful')
      : (hi ? 'मददगार नहीं' : 'Not helpful')
    return (
      <button
        type="button"
        onClick={() => { if (!chosen) rate(rating) }}
        aria-pressed={chosen}
        aria-label={label}
        title={label}
        className="w-11 h-11 flex items-center justify-center rounded-full transition-colors"
        style={{
          color: chosen ? 'var(--kajal)' : 'var(--ink-55)',
          background: chosen ? 'var(--ink-08)' : 'transparent',
        }}
      >
        <Icon size={16} fill={chosen ? 'currentColor' : 'none'} strokeWidth={chosen ? 1.6 : 2} aria-hidden="true" />
      </button>
    )
  }

  return (
    <>
      <span className="inline-flex items-center">
        {button('up')}
        {button('down')}
        {state.rating && !askingWhy && (
          <span className="text-[12.5px] pl-1" role="status" style={{ color: 'var(--ink-55)' }}>
            {hi ? 'धन्यवाद' : 'Thanks'}
          </span>
        )}
      </span>
      {askingWhy && (
        <div ref={reasonsRef} className="w-full flex flex-col gap-2 pt-1">
          <span className="text-[13px]" style={{ color: 'var(--ink-70)' }}>
            {hi ? 'क्या ठीक नहीं लगा? (चाहें तो चुनें)' : 'What went wrong? (optional)'}
          </span>
          <div className="flex flex-wrap gap-1.5">
            {FEEDBACK_REASONS.map(reason => (
              <button
                key={reason.id}
                type="button"
                onClick={() => rate('down', reason.id)}
                className="rounded-full px-3.5 text-[13.5px] min-h-[44px]"
                style={{ color: 'var(--ink-85)', background: 'var(--surface)', border: '1px solid var(--ink-20)' }}
              >
                {reason[lang]}
              </button>
            ))}
          </div>
        </div>
      )}
    </>
  )
}
