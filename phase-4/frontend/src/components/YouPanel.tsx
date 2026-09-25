/**
 * You — everything about this person and this phone in one place:
 * notifications, voice, what left the Mac, PIN and devices, and sign out.
 * Memory and System (desktop rail items) open from here on a phone.
 */
import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { Bell, Brain, ChevronRight, KeyRound, LogOut, Mic, Settings2, ShieldCheck } from 'lucide-react'
import { toast } from 'sonner'
import { apiFetch, type FamilyProfile } from '@/lib/api'
import type { AppSurface } from '@/lib/surfaces'
import { useIsMobile } from '@/hooks/useIsMobile'
import { NotificationSettings } from './NotificationSettings'
import { ProfileTab } from './ProfileTab'
import { EgressScreen } from './EgressScreen'
import { PROFILE_COLORS } from './ProfileBadge'
import { DotText, Sheet } from './pulli'

type ReplyLanguage = 'en' | 'hi' | 'auto'
type HindiScript = 'devanagari' | 'roman'

interface VoicePrefs {
  reply_language: ReplyLanguage
  script: HindiScript
  keep_voice_on_mac: boolean
}

const DEFAULT_VOICE: VoicePrefs = { reply_language: 'en', script: 'devanagari', keep_voice_on_mac: false }

function Choice<T extends string>({ label, value, options, onChange, disabled }: {
  label: string
  value: T
  options: Array<[T, string, string?]>
  onChange: (value: T) => void
  disabled?: boolean
}) {
  return (
    <div role="radiogroup" aria-label={label} style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 8 }}>
      {options.map(([key, text, lang]) => {
        const chosen = value === key
        return (
          <button
            key={key}
            type="button"
            role="radio"
            aria-checked={chosen}
            disabled={disabled}
            lang={lang}
            onClick={() => onChange(key)}
            style={{
              minHeight: 44,
              padding: '0 16px',
              borderRadius: 999,
              border: `1px solid ${chosen ? 'transparent' : 'var(--ink-20)'}`,
              background: chosen ? 'var(--kajal)' : 'transparent',
              color: chosen ? 'var(--paper)' : 'var(--ink-85)',
              fontSize: 14,
              fontWeight: chosen ? 700 : 500,
              lineHeight: 1.2,
              cursor: 'pointer',
            }}
          >
            {text}
          </button>
        )
      })}
    </div>
  )
}

/** The same settings as voice mode's own sheet, reachable without opening the mic. */
function VoiceSettingsCard({ onOpenVoice }: { onOpenVoice: () => void }) {
  const [prefs, setPrefs] = useState<VoicePrefs | null>(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    let cancelled = false
    apiFetch('/voice/preferences')
      .then(response => (response.ok ? response.json() : null))
      .then(data => { if (!cancelled) setPrefs({ ...DEFAULT_VOICE, ...(data?.preferences ?? {}) }) })
      .catch(() => { if (!cancelled) setPrefs(DEFAULT_VOICE) })
    return () => { cancelled = true }
  }, [])

  const save = useCallback(async (update: Partial<VoicePrefs>) => {
    if (!prefs) return
    const previous = prefs
    setPrefs({ ...prefs, ...update })
    setSaving(true)
    try {
      const response = await apiFetch('/voice/preferences', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(update),
      })
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
    } catch {
      setPrefs(previous)
      toast.error('Could not save your voice settings')
    } finally {
      setSaving(false)
    }
  }, [prefs])

  return (
    <div className="n-panel" style={{ marginTop: 10 }}>
      <div style={{ fontSize: 15, fontWeight: 650 }}>Narad replies in</div>
      <Choice
        label="Narad replies in"
        value={prefs?.reply_language ?? 'en'}
        disabled={!prefs || saving}
        options={[['en', 'English'], ['hi', 'हिन्दी', 'hi'], ['auto', 'Same as I speak']]}
        onChange={reply_language => void save({ reply_language })}
      />
      <div style={{ marginTop: 16, fontSize: 15, fontWeight: 650 }}>Hindi written as</div>
      <Choice
        label="Hindi written as"
        value={prefs?.script ?? 'devanagari'}
        disabled={!prefs || saving}
        options={[['devanagari', 'देवनागरी', 'hi'], ['roman', 'Roman (Hinglish)']]}
        onChange={script => void save({ script })}
      />
      <label style={{ display: 'flex', alignItems: 'flex-start', gap: 12, minHeight: 48, marginTop: 14, cursor: 'pointer' }}>
        <input
          type="checkbox"
          checked={prefs?.keep_voice_on_mac ?? false}
          disabled={!prefs || saving}
          onChange={event => void save({ keep_voice_on_mac: event.target.checked })}
          style={{ width: 22, height: 22, margin: '1px 0 0', flex: '0 0 auto', accentColor: 'var(--sindoor)' }}
        />
        <span>
          <span style={{ display: 'block', fontSize: 15, fontWeight: 600 }}>Keep my voice on this Mac</span>
          <span style={{ display: 'block', marginTop: 2, fontSize: 13.5, lineHeight: 1.45, color: 'var(--ink-70)' }}>
            Off, Sarvam (a trusted provider) hears and speaks for Narad, which is far better in Hindi.
          </span>
        </span>
      </label>
      <button type="button" className="n-btn n-btn-block" style={{ marginTop: 12 }} onClick={onOpenVoice}>
        <Mic size={17} aria-hidden="true" /> Talk to Narad
      </button>
    </div>
  )
}

type SheetId = 'notifications' | 'voice' | 'devices'

/** One row of the You menu: what it is, its state in a line, and a chevron. */
function MenuRow({ icon, title, detail, onClick }: { icon: ReactNode; title: string; detail: string; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="pl-row"
      style={{ width: '100%', minHeight: 66, padding: '8px 14px', display: 'flex', alignItems: 'center', gap: 14, border: 0, background: 'transparent', textAlign: 'left', cursor: 'pointer', color: 'var(--kajal)' }}
    >
      <span aria-hidden="true" style={{ width: 36, height: 36, flex: '0 0 auto', display: 'grid', placeItems: 'center', borderRadius: 999, background: 'var(--paper)', color: 'var(--kajal)' }}>
        {icon}
      </span>
      <span style={{ flex: 1, minWidth: 0 }}>
        <span style={{ display: 'block', fontSize: 16.5, fontWeight: 600 }}>{title}</span>
        <span style={{ display: 'block', marginTop: 1, fontSize: 13.5, lineHeight: 1.35, color: 'var(--ink-55)' }}>{detail}</span>
      </span>
      <ChevronRight size={18} aria-hidden="true" style={{ color: 'var(--ink-40)', flex: '0 0 auto' }} />
    </button>
  )
}

/**
 * You is a menu, not a settings wall: who is signed in, then one row per
 * group. Each group opens in its own sheet, so the screen asks nothing of
 * the person until they choose.
 */
export function YouPanel({ profile, onSignOut, onOpenVoice, onOpenSurface }: {
  profile: FamilyProfile
  onSignOut: () => void
  onOpenVoice: () => void
  onOpenSurface: (surface: AppSurface) => void
}) {
  const [egressOpen, setEgressOpen] = useState(false)
  const [sheet, setSheet] = useState<SheetId | null>(null)
  const isMobile = useIsMobile()
  const colour = PROFILE_COLORS[profile.color] || PROFILE_COLORS.sindoor
  const close = useCallback(() => setSheet(null), [])

  return (
    <div className="panel-scroll" style={{ height: '100%', overflow: 'auto' }}>
      <div style={{ maxWidth: 640, margin: '0 auto', padding: '18px 16px 40px' }}>
        <div className="pl-in" style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          <span
            aria-hidden="true"
            className="font-dot"
            style={{ width: 64, height: 64, flex: '0 0 auto', display: 'grid', placeItems: 'center', borderRadius: 999, background: colour, color: '#ffffff', fontSize: 32 }}
          >
            {profile.initial}
          </span>
          <div style={{ minWidth: 0 }}>
            <h1 className="font-display" style={{ fontSize: 32, color: 'var(--kajal)', overflowWrap: 'anywhere' }}>{profile.display_name}</h1>
            <DotText size={13.5}>{profile.is_owner ? 'OWNER OF THIS NARAD' : 'FAMILY MEMBER'} · PRIVATE</DotText>
          </div>
        </div>

        <div className="pl-in pl-in2" style={{ marginTop: 24, borderRadius: 22, overflow: 'hidden', background: 'var(--surface-raised)' }}>
          <MenuRow icon={<Bell size={18} />} title="Notifications" detail="This phone, quiet hours, family" onClick={() => setSheet('notifications')} />
          <MenuRow icon={<Mic size={18} />} title="Voice" detail="Language, Hindi script, where your voice is heard" onClick={() => setSheet('voice')} />
          <MenuRow icon={<ShieldCheck size={18} />} title="Privacy" detail="What left the family Mac, and what it saw" onClick={() => setEgressOpen(true)} />
          <MenuRow icon={<KeyRound size={18} />} title="PIN and devices" detail="Change your PIN, phones signed in" onClick={() => setSheet('devices')} />
        </div>

        {isMobile && (
          <div className="pl-in pl-in3" style={{ marginTop: 12, borderRadius: 22, overflow: 'hidden', background: 'var(--surface-raised)' }}>
            <MenuRow icon={<Brain size={18} />} title="Memory" detail="What Narad remembers from your conversations" onClick={() => onOpenSurface('memory')} />
            <MenuRow icon={<Settings2 size={18} />} title="System" detail="Status, traces and connections" onClick={() => onOpenSurface('system')} />
          </div>
        )}

        <div style={{ display: 'flex', justifyContent: 'center', marginTop: 18 }}>
          <button type="button" className="n-link" onClick={onSignOut}>
            <LogOut size={16} aria-hidden="true" /> Sign out of this phone
          </button>
        </div>
      </div>

      <Sheet title="Notifications" open={sheet === 'notifications'} onClose={close}>
        <NotificationSettings profile={profile} />
      </Sheet>
      <Sheet title="Voice" open={sheet === 'voice'} onClose={close}>
        <VoiceSettingsCard onOpenVoice={() => { close(); onOpenVoice() }} />
      </Sheet>
      <Sheet title="PIN and devices" open={sheet === 'devices'} onClose={close}>
        <ProfileTab profile={profile} onSignedOut={onSignOut} />
      </Sheet>
      {egressOpen && <EgressScreen onClose={() => setEgressOpen(false)} />}
    </div>
  )
}
