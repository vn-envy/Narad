import { useCallback, useEffect, useState, type ReactNode } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { ArrowLeft, Check, LoaderCircle } from 'lucide-react'
import type { FamilyProfile } from '@/lib/api'
import {
  CONSENT_REQUIRED_EVENT,
  acceptConsent,
  fetchConsent,
  type ConsentStatus,
  type TrustLang,
} from '@/lib/trust'
import { MahatiLogo } from './MahatiLogo'
import { LangToggle } from './EgressScreen'

type Phase = 'checking' | 'ready' | 'reading' | 'later'

const LANG_KEY = 'narad_consent_lang'

function initialLang(userId: string): TrustLang {
  try {
    const stored = localStorage.getItem(`${LANG_KEY}:${userId}`)
    if (stored === 'hi' || stored === 'en') return stored
  } catch { /* storage is optional */ }
  return typeof navigator !== 'undefined' && navigator.language?.toLowerCase().startsWith('hi') ? 'hi' : 'en'
}

/** After sign-in, a member reads and accepts Part A of the consent sheet before
 *  Narad handles anything of theirs. The server enforces the same rule (403
 *  consent_required on /chat, /voice and uploads); the owner is never asked. */
export function ConsentGate({
  profile, onSwitchProfile, children,
}: {
  profile: FamilyProfile
  onSwitchProfile: () => void
  children: ReactNode
}) {
  const [phase, setPhase] = useState<Phase>(profile.is_owner ? 'ready' : 'checking')
  const [lang, setLang] = useState<TrustLang>(() => initialLang(profile.user_id))
  const [status, setStatus] = useState<ConsentStatus | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async (language: TrustLang, reopen = false) => {
    try {
      const next = await fetchConsent(language)
      setStatus(next)
      if (!next.needs_consent || next.enforced === false) setPhase('ready')
      else setPhase(current => (reopen || current === 'checking' ? 'reading' : current))
    } catch {
      // The server still refuses unconsented work; never trap someone on a spinner.
      setPhase(current => (current === 'checking' ? 'ready' : current))
    }
  }, [])

  useEffect(() => {
    if (!profile.is_owner) void load(lang)
    // Language changes reload the sheet below; this runs once per profile.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [profile.user_id, profile.is_owner, load])

  useEffect(() => {
    if (profile.is_owner) return
    const reopen = () => void load(lang, true)
    window.addEventListener(CONSENT_REQUIRED_EVENT, reopen)
    return () => window.removeEventListener(CONSENT_REQUIRED_EVENT, reopen)
  }, [profile.is_owner, load, lang])

  const changeLang = (next: TrustLang) => {
    setLang(next)
    try { localStorage.setItem(`${LANG_KEY}:${profile.user_id}`, next) } catch { /* optional */ }
    void load(next)
  }

  const accept = async () => {
    if (!status) return
    setBusy(true)
    setError(null)
    try {
      await acceptConsent(status.current_version)
      setPhase('ready')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not save your answer.')
    } finally {
      setBusy(false)
    }
  }

  if (phase === 'ready') return <>{children}</>

  const hi = lang === 'hi'
  const langAttr = hi ? 'hi' : 'en'

  if (phase === 'checking') {
    return (
      <main className="family-gate" aria-label="Opening your private Narad profile">
        <div className="family-gate-glow family-gate-glow-one" />
        <div className="family-session-loading">
          <span className="family-session-pulse" />
          Opening your Narad
        </div>
      </main>
    )
  }

  if (phase === 'later') {
    return (
      <main
        className="min-h-[100dvh] flex items-center justify-center px-5"
        lang={langAttr}
        style={{ background: 'var(--paper)', color: 'var(--kajal)' }}
      >
        <div className="w-full max-w-[420px] flex flex-col items-center text-center gap-4">
          <MahatiLogo size={44} />
          <h1 className="text-[22px] font-semibold leading-snug">
            {hi ? 'कोई बात नहीं' : 'That is completely fine'}
          </h1>
          <p className="text-[16px] leading-relaxed" style={{ color: 'var(--ink-70)' }}>
            {hi
              ? 'जब तक आप सहमत नहीं होते, नारद आपके संदेश, आवाज़ या फ़ाइलें न पढ़ेगा, न रखेगा। जब मन हो, पत्र फिर से पढ़ लीजिए, या घर के मालिक से बात कर लीजिए। मुश्किल घड़ी में टेली-मानस 14416 पर कभी भी बात कर सकते हैं।'
              : "Until you agree, Narad won't read or keep your messages, voice or files. Read the sheet again whenever you like, or talk it over with the owner. If you're going through a hard time, Tele-MANAS is free on 14416, any time."}
          </p>
          <div className="w-full flex flex-col gap-2 mt-2">
            <button type="button" className="family-primary" onClick={() => setPhase('reading')}>
              {hi ? 'पत्र फिर से पढ़ें' : 'Read the sheet again'}
            </button>
            <button
              type="button"
              onClick={onSwitchProfile}
              className="n-btn n-btn-block"
            >
              {hi ? 'दूसरी प्रोफ़ाइल चुनें' : 'Switch person'}
            </button>
          </div>
        </div>
      </main>
    )
  }

  return (
    <main className="h-full overflow-y-auto flex flex-col" lang={langAttr} style={{ background: 'var(--paper)', color: 'var(--kajal)' }}>
      <header
        className="flex items-center gap-1 pl-1 pr-3 flex-shrink-0 sticky top-0"
        style={{
          minHeight: 'calc(56px + env(safe-area-inset-top))',
          paddingTop: 'env(safe-area-inset-top)',
          background: 'var(--paper)',
          borderBottom: '1px solid var(--line)',
          zIndex: 1,
        }}
      >
        <button
          type="button"
          onClick={onSwitchProfile}
          className="n-icon-btn"
          aria-label={hi ? 'प्रोफ़ाइल पर वापस' : 'Back to profiles'}
        >
          <ArrowLeft size={20} />
        </button>
        <span className="text-[14px] flex-1 truncate" style={{ color: 'var(--ink-70)' }}>
          {hi ? 'नारद में आपका स्वागत है' : 'Welcome to Narad'}
        </span>
        <LangToggle lang={lang} onChange={changeLang} />
      </header>

      <div className="flex-1 w-full max-w-[600px] mx-auto px-5 pt-6 pb-40">
        <MahatiLogo size={40} />
        <h1 className="mt-3 text-[26px] leading-tight font-semibold" style={{ fontFamily: hi ? 'var(--font-hindi)' : 'var(--font-hero)' }}>
          {hi ? `नमस्ते, ${profile.display_name}` : `Namaste, ${profile.display_name}`}
        </h1>
        <p className="mt-2 mb-6 text-[16px] leading-relaxed" style={{ color: 'var(--ink-70)' }}>
          {hi
            ? 'शुरू करने से पहले, कृपया पढ़ लीजिए कि नारद आपकी बातें कैसे निजी रखता है। इसमें करीब पांच मिनट लगते हैं, और आप कभी भी सवाल पूछ सकते हैं।'
            : 'Before we begin, please read how Narad keeps your things private. It takes about five minutes, and questions are always welcome.'}
        </p>

        {status?.sheet?.markdown ? (
          <article className="consent-sheet text-[16px] leading-[1.65]">
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                h3: ({ children }) => <h3 className="text-[19px] font-semibold mt-7 mb-2 leading-snug">{children}</h3>,
                p: ({ children }) => <p className="mb-3">{children}</p>,
                ul: ({ children }) => <ul className="pl-5 mb-3 flex flex-col gap-1.5" style={{ listStyleType: 'disc' }}>{children}</ul>,
                li: ({ children }) => <li>{children}</li>,
                strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
                em: ({ children }) => <em style={{ color: 'var(--ink-70)' }}>{children}</em>,
                code: ({ children }) => (
                  <code className="text-[13px] px-1 py-0.5 rounded" style={{ background: 'var(--ink-08)', fontFamily: 'var(--font-mono)' }}>{children}</code>
                ),
                table: ({ children }) => (
                  <div className="overflow-x-auto mb-4 rounded-[11px]" style={{ border: '1px solid var(--ink-12)' }}>
                    <table className="w-full text-[14px] border-collapse min-w-[520px]">{children}</table>
                  </div>
                ),
                th: ({ children }) => <th className="text-left font-semibold px-3 py-2" style={{ background: 'var(--ink-08)' }}>{children}</th>,
                td: ({ children }) => <td className="align-top px-3 py-2" style={{ borderTop: '1px solid var(--ink-08)' }}>{children}</td>,
                a: ({ href, children }) => <a href={href} target="_blank" rel="noopener noreferrer" className="underline">{children}</a>,
              }}
            >
              {status.sheet.markdown}
            </ReactMarkdown>
          </article>
        ) : (
          <div role="status" className="flex items-center gap-2 text-[15px]" style={{ color: 'var(--ink-55)' }}>
            <LoaderCircle size={16} className="animate-spin" aria-hidden="true" /> {hi ? 'पत्र लोड हो रहा है…' : 'Loading the sheet…'}
          </div>
        )}
      </div>

      <footer
        className="fixed bottom-0 inset-x-0 px-5 pt-3"
        style={{
          background: 'linear-gradient(to top, var(--paper) 78%, transparent)',
          paddingBottom: 'max(16px, env(safe-area-inset-bottom))',
        }}
      >
        <div className="w-full max-w-[600px] mx-auto flex flex-col gap-2">
          {error && <p role="alert" className="text-[14px] text-center" style={{ color: 'var(--sindoor)' }}>{error}</p>}
          <button type="button" className="family-primary" disabled={busy || !status?.sheet?.markdown} onClick={() => void accept()}>
            {busy ? <LoaderCircle size={16} className="animate-spin" /> : <Check size={16} />}
            {hi ? 'मैं सहमत हूं' : 'I agree'}
          </button>
          <button
            type="button"
            onClick={() => setPhase('later')}
            className="n-btn n-btn-block"
            style={{ background: 'var(--paper)' }}
          >
            {hi ? 'अभी नहीं' : 'Not now'}
          </button>
          {status?.current_version && (
            <p className="text-[12px] text-center" style={{ color: 'var(--ink-55)', fontFamily: 'var(--font-mono)' }}>
              {hi ? 'संस्करण' : 'Version'} {status.current_version}
            </p>
          )}
        </div>
      </footer>
    </main>
  )
}
