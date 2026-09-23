import { useEffect, useState } from 'react'
import { ArrowLeft, ArrowRight, LoaderCircle, LockKeyhole, Plus, ShieldCheck, UserRound } from 'lucide-react'
import {
  apiFetch,
  clearProfileSession,
  setProfileSession,
  type FamilyProfile,
  type FamilyProfileSession,
} from '@/lib/api'
import { MahatiLogo } from './MahatiLogo'

interface Props {
  onAuthenticated: (session: FamilyProfileSession) => void
}

const COLOR_MAP: Record<string, string> = {
  sindoor: '#c2410c',
  matsya: '#2450a4',
  rama: '#a16207',
  krishna: '#0f766e',
  parashurama: '#9f1239',
  nila: '#3d477f',
  gulab: '#b0316b',
  tulsi: '#065f46',
}

async function readJson<T>(response: Response): Promise<T> {
  const payload = await response.json().catch(() => ({})) as T & { detail?: string }
  if (!response.ok) throw new Error(payload.detail || `Request failed (${response.status})`)
  return payload
}

export function FamilyProfileGate({ onAuthenticated }: Props) {
  const [profiles, setProfiles] = useState<FamilyProfile[]>([])
  const [selected, setSelected] = useState<FamilyProfile | null>(null)
  const [creating, setCreating] = useState(false)
  const [name, setName] = useState('')
  const [pin, setPin] = useState('')
  const [confirmPin, setConfirmPin] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = async () => {
    const response = await apiFetch('/profiles')
    const payload = await readJson<{ profiles: FamilyProfile[] }>(response)
    setProfiles(payload.profiles)
  }

  useEffect(() => {
    clearProfileSession()
    void load().catch(() => setError('Narad could not load family profiles. Check that the server is running.'))
  }, [])

  const finish = (session: FamilyProfileSession) => {
    setProfileSession(session)
    onAuthenticated(session)
  }

  const login = async (profile: FamilyProfile, suppliedPin = '') => {
    setBusy(true)
    setError(null)
    try {
      const response = await apiFetch('/profiles/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: profile.user_id, pin: suppliedPin }),
      })
      finish(await readJson<FamilyProfileSession>(response))
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'That profile could not be opened.')
    } finally {
      setBusy(false)
    }
  }

  const choose = (profile: FamilyProfile) => {
    setError(null)
    setPin('')
    setConfirmPin('')
    setSelected(profile)
  }

  const openSelected = async () => {
    if (!selected) return
    if (selected.has_pin) {
      await login(selected, pin)
      return
    }
    if (pin !== confirmPin) {
      setError('The two PINs do not match.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      const response = await apiFetch('/profiles/bootstrap', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: selected.user_id, pin }),
      })
      finish(await readJson<FamilyProfileSession>(response))
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'The owner profile could not be secured.')
    } finally {
      setBusy(false)
    }
  }

  const create = async () => {
    if (pin !== confirmPin) {
      setError('The two PINs do not match.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      const response = await apiFetch('/profiles', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ display_name: name, pin }),
      })
      finish(await readJson<FamilyProfileSession>(response))
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'The profile could not be created.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="family-gate">
      <div className="family-gate-glow family-gate-glow-one" />
      <div className="family-gate-glow family-gate-glow-two" />
      <section className="family-gate-card" aria-label="Choose a Narad profile">
        <header className="family-gate-header">
          <MahatiLogo size={38} />
          <div>
            <p className="family-gate-kicker">NARAD FAMILY</p>
            <h1>{creating ? 'Create your space' : selected ? `${selected.has_pin ? 'Welcome' : 'Secure your space'}, ${selected.display_name}` : 'Who is using Narad?'}</h1>
            <p>{creating ? 'Private memory, workflows, health, finance, and Google access.' : selected ? selected.has_pin ? 'Enter your private PIN to continue.' : 'Choose a PIN before opening your existing Narad data.' : 'Each person gets an isolated local workspace.'}</p>
          </div>
        </header>

        {!creating && !selected && (
          <div className="family-profile-grid">
            {profiles.map(profile => (
              <button key={profile.user_id} type="button" className="family-profile-card" onClick={() => choose(profile)} disabled={busy}>
                <span className="family-profile-avatar" style={{ background: COLOR_MAP[profile.color] || COLOR_MAP.sindoor }}>{profile.initial}</span>
                <span className="family-profile-name">{profile.display_name}</span>
                <span className="family-profile-meta">{profile.has_pin ? <><LockKeyhole size={11} /> Private</> : profile.is_owner ? 'Owner profile' : 'Profile'}</span>
              </button>
            ))}
            <button type="button" className="family-profile-card family-profile-add" onClick={() => { setCreating(true); setError(null) }}>
              <span className="family-profile-avatar"><Plus size={22} /></span>
              <span className="family-profile-name">Add person</span>
              <span className="family-profile-meta">Up to 12 profiles</span>
            </button>
          </div>
        )}

        {selected && !creating && (
          <form className="family-profile-form" onSubmit={event => { event.preventDefault(); void openSelected() }}>
            <button type="button" className="family-back" onClick={() => { setSelected(null); setError(null) }}><ArrowLeft size={14} /> Profiles</button>
            <div className="family-selected-avatar" style={{ background: COLOR_MAP[selected.color] || COLOR_MAP.sindoor }}>{selected.initial}</div>
            <label>{selected.has_pin ? 'Private PIN' : 'Choose private PIN'}<input inputMode="numeric" autoComplete={selected.has_pin ? 'current-password' : 'new-password'} pattern="[0-9]*" maxLength={8} value={pin} onChange={event => setPin(event.target.value.replace(/\D/g, ''))} autoFocus placeholder="4-8 digits" /></label>
            {!selected.has_pin && (
              <label>Confirm PIN<input inputMode="numeric" autoComplete="new-password" pattern="[0-9]*" maxLength={8} value={confirmPin} onChange={event => setConfirmPin(event.target.value.replace(/\D/g, ''))} placeholder="Repeat PIN" /></label>
            )}
            <button className="family-primary" type="submit" disabled={busy || pin.length < 4 || (!selected.has_pin && confirmPin.length < 4)}>{busy ? <LoaderCircle size={16} className="animate-spin" /> : <ArrowRight size={16} />} {selected.has_pin ? 'Open my Narad' : 'Secure and open'}</button>
          </form>
        )}

        {creating && (
          <form className="family-profile-form" onSubmit={event => { event.preventDefault(); void create() }}>
            <button type="button" className="family-back" onClick={() => { setCreating(false); setError(null) }}><ArrowLeft size={14} /> Profiles</button>
            <span className="family-create-icon"><UserRound size={22} /></span>
            <label>Your name<input autoComplete="name" maxLength={48} value={name} onChange={event => setName(event.target.value)} autoFocus placeholder="e.g. Meera" /></label>
            <div className="family-pin-row">
              <label>Choose PIN<input inputMode="numeric" autoComplete="new-password" pattern="[0-9]*" maxLength={8} value={pin} onChange={event => setPin(event.target.value.replace(/\D/g, ''))} placeholder="4-8 digits" /></label>
              <label>Confirm PIN<input inputMode="numeric" autoComplete="new-password" pattern="[0-9]*" maxLength={8} value={confirmPin} onChange={event => setConfirmPin(event.target.value.replace(/\D/g, ''))} placeholder="Repeat PIN" /></label>
            </div>
            <button className="family-primary" type="submit" disabled={busy || !name.trim() || pin.length < 4 || confirmPin.length < 4}>{busy ? <LoaderCircle size={16} className="animate-spin" /> : <Plus size={16} />} Create private profile</button>
          </form>
        )}

        {error && <p className="family-gate-error">{error}</p>}
        <footer><ShieldCheck size={13} /> Personal data stays separated on this Narad device.</footer>
      </section>
    </main>
  )
}
