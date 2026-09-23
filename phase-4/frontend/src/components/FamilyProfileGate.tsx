import { useEffect, useState } from 'react'
import { ArrowLeft, ArrowRight, KeyRound, LoaderCircle, LockKeyhole, Plus, ShieldCheck, UserRound } from 'lucide-react'
import {
  apiFetch,
  clearProfileSession,
  setProfileSession,
  type FamilyProfile,
  type FamilyProfileInvite,
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
  const [inviting, setInviting] = useState(false)
  const [name, setName] = useState('')
  const [pin, setPin] = useState('')
  const [confirmPin, setConfirmPin] = useState('')
  const [inviteCode, setInviteCode] = useState('')
  const [ownerApproves, setOwnerApproves] = useState(false)
  const [ownerPin, setOwnerPin] = useState('')
  const [invite, setInvite] = useState<FamilyProfileInvite | null>(null)
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

  const owner = profiles.find(profile => profile.is_owner && profile.has_pin)

  // The owner approves on this device with their PIN. That session is used for
  // one request and never stored, so this device stays signed out.
  const ownerHeaders = async (): Promise<Record<string, string>> => {
    if (!owner) throw new Error('The owner profile has no PIN yet.')
    const response = await apiFetch('/profiles/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_id: owner.user_id, pin: ownerPin }),
    })
    const session = await readJson<FamilyProfileSession>(response)
    return { Authorization: `Bearer ${session.token}` }
  }

  const create = async () => {
    if (pin !== confirmPin) {
      setError('The two PINs do not match.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      const approval = ownerApproves ? await ownerHeaders() : {}
      const response = await apiFetch('/profiles', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...approval },
        body: JSON.stringify({ display_name: name, pin, invite_code: ownerApproves ? '' : inviteCode }),
      })
      finish(await readJson<FamilyProfileSession>(response))
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'The profile could not be created.')
    } finally {
      setBusy(false)
    }
  }

  const createInvite = async () => {
    setBusy(true)
    setError(null)
    try {
      const response = await apiFetch('/profiles/invites', { method: 'POST', headers: await ownerHeaders() })
      setInvite(await readJson<FamilyProfileInvite>(response))
      setOwnerPin('')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'The invite could not be created.')
    } finally {
      setBusy(false)
    }
  }

  const backToProfiles = () => {
    setCreating(false)
    setInviting(false)
    setInvite(null)
    setOwnerPin('')
    setError(null)
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
            <h1>{inviting ? 'Invite someone' : creating ? 'Create your space' : selected ? `${selected.has_pin ? 'Welcome' : 'Secure your space'}, ${selected.display_name}` : 'Who is using Narad?'}</h1>
            <p>{inviting ? 'The owner creates a one-time code for a new family member.' : creating ? 'Private memory, workflows, health, finance, and Google access.' : selected ? selected.has_pin ? 'Enter your private PIN to continue.' : 'Choose a PIN before opening your existing Narad data.' : 'Each person gets an isolated local workspace.'}</p>
          </div>
        </header>

        {!creating && !inviting && !selected && (
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
              <span className="family-profile-meta">Owner or invite code</span>
            </button>
          </div>
        )}

        {!creating && !inviting && !selected && owner && (
          <button type="button" className="family-back" style={{ alignSelf: 'center', margin: '14px auto 0' }} onClick={() => { setInviting(true); setError(null) }}>
            <KeyRound size={12} /> Owner: create an invite code
          </button>
        )}

        {inviting && (
          <form className="family-profile-form" onSubmit={event => { event.preventDefault(); void createInvite() }}>
            <button type="button" className="family-back" onClick={backToProfiles}><ArrowLeft size={14} /> Profiles</button>
            <span className="family-create-icon"><KeyRound size={22} /></span>
            {invite ? (
              <>
                <label>Invite code<input readOnly value={invite.code} onFocus={event => event.currentTarget.select()} style={{ textAlign: 'center', letterSpacing: '0.2em' }} /></label>
                <p className="family-profile-meta" style={{ justifyContent: 'center', textAlign: 'center' }}>Works once, until {new Date(invite.expires_at * 1000).toLocaleString()}. It will not be shown again.</p>
                <button className="family-primary" type="button" onClick={backToProfiles}>Done</button>
              </>
            ) : (
              <>
                <label>{owner?.display_name ?? 'Owner'} PIN<input inputMode="numeric" autoComplete="current-password" pattern="[0-9]*" maxLength={8} value={ownerPin} onChange={event => setOwnerPin(event.target.value.replace(/\D/g, ''))} autoFocus placeholder="4-8 digits" /></label>
                <button className="family-primary" type="submit" disabled={busy || ownerPin.length < 4}>{busy ? <LoaderCircle size={16} className="animate-spin" /> : <KeyRound size={16} />} Create invite code</button>
              </>
            )}
          </form>
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
            <button type="button" className="family-back" onClick={backToProfiles}><ArrowLeft size={14} /> Profiles</button>
            <span className="family-create-icon"><UserRound size={22} /></span>
            <label>Your name<input autoComplete="name" maxLength={48} value={name} onChange={event => setName(event.target.value)} autoFocus placeholder="e.g. Meera" /></label>
            <div className="family-pin-row">
              <label>Choose PIN<input inputMode="numeric" autoComplete="new-password" pattern="[0-9]*" maxLength={8} value={pin} onChange={event => setPin(event.target.value.replace(/\D/g, ''))} placeholder="4-8 digits" /></label>
              <label>Confirm PIN<input inputMode="numeric" autoComplete="new-password" pattern="[0-9]*" maxLength={8} value={confirmPin} onChange={event => setConfirmPin(event.target.value.replace(/\D/g, ''))} placeholder="Repeat PIN" /></label>
            </div>
            {ownerApproves ? (
              <label>{owner?.display_name ?? 'Owner'} PIN (owner approval)<input inputMode="numeric" autoComplete="off" pattern="[0-9]*" maxLength={8} value={ownerPin} onChange={event => setOwnerPin(event.target.value.replace(/\D/g, ''))} placeholder="4-8 digits" /></label>
            ) : (
              <label>Invite code<input autoComplete="off" autoCapitalize="characters" maxLength={16} value={inviteCode} onChange={event => setInviteCode(event.target.value.toUpperCase())} placeholder="From the owner, e.g. K7QM-2XPF" /></label>
            )}
            {owner && (
              <button type="button" className="family-back" style={{ alignSelf: 'center' }} onClick={() => { setOwnerApproves(!ownerApproves); setOwnerPin(''); setError(null) }}>
                {ownerApproves ? 'Use an invite code instead' : `${owner.display_name} is here? Approve with the owner PIN`}
              </button>
            )}
            <button className="family-primary" type="submit" disabled={busy || !name.trim() || pin.length < 4 || confirmPin.length < 4 || (ownerApproves && ownerPin.length < 4)}>{busy ? <LoaderCircle size={16} className="animate-spin" /> : <Plus size={16} />} Create private profile</button>
          </form>
        )}

        {error && <p className="family-gate-error">{error}</p>}
        <footer><ShieldCheck size={13} /> Personal data stays separated on this Narad device.</footer>
      </section>
    </main>
  )
}
