/**
 * ProfileTab — your PIN and your signed-in devices; the owner also looks
 * after each family member's PIN and devices. It is the sign-in part of the
 * You screen (YouPanel), which also holds notifications, voice and privacy.
 *
 * A new PIN or "sign out everywhere" bumps the profile's session epoch on the
 * server, so every older token stops working at once, this device's included.
 */
import { useCallback, useEffect, useState, type ReactNode } from 'react'
import {
  apiFetch,
  setProfileSession,
  type FamilyProfile,
  type FamilyProfileSession,
} from '@/lib/api'
import { SectionTitle } from './NotificationSettings'

const PIN_RE = /^\d{4,8}$/

type Notice = { ok: boolean; text: string } | null
type MemberAction = { userId: string; action: 'reset' | 'signout' } | null

const card = {
  padding: '16px',
  borderRadius: 16,
  border: '1px solid var(--line)',
  background: 'var(--surface-raised)',
  margin: '10px 0 18px',
} as const

const primaryButton = (enabled: boolean) => ({
  minHeight: 48,
  padding: '0 18px',
  borderRadius: 10,
  border: 'none',
  background: enabled ? 'var(--sindoor)' : 'var(--ink-12)',
  color: enabled ? '#fcfaf2' : 'var(--ink-55)',
  fontSize: 14,
  fontWeight: 650,
  cursor: enabled ? 'pointer' : 'default',
}) as const

const outlineButton = (danger = false) => ({
  minHeight: 48,
  padding: '0 16px',
  borderRadius: 10,
  border: danger ? '1px solid color-mix(in srgb, var(--kesari) 50%, transparent)' : '1px solid var(--ink-20)',
  background: 'transparent',
  color: danger ? 'var(--kesari)' : 'var(--ink-85)',
  fontSize: 14,
  fontWeight: 600,
  cursor: 'pointer',
}) as const

function microLabel(text: string) {
  return <SectionTitle>{text}</SectionTitle>
}

function Hint({ children }: { children: ReactNode }) {
  return <div style={{ fontSize: 14, lineHeight: 1.5, color: 'var(--ink-70)', margin: '6px 0 12px' }}>{children}</div>
}

function NoticeLine({ notice }: { notice: Notice }) {
  if (!notice) return null
  return (
    <div
      role={notice.ok ? 'status' : 'alert'}
      style={{ fontSize: 14, lineHeight: 1.45, marginTop: 10, color: notice.ok ? 'var(--tulsi)' : 'var(--sindoor)' }}
    >
      {notice.text}
    </div>
  )
}

function PinField({
  label,
  value,
  onChange,
  autoComplete,
  autoFocus = false,
}: {
  label: string
  value: string
  onChange: (value: string) => void
  autoComplete: 'current-password' | 'new-password'
  autoFocus?: boolean
}) {
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 6, fontSize: 14, fontWeight: 600, color: 'var(--ink-70)' }}>
      {label}
      <input
        type="password"
        inputMode="numeric"
        pattern="[0-9]*"
        autoComplete={autoComplete}
        maxLength={8}
        value={value}
        autoFocus={autoFocus}
        onChange={event => onChange(event.target.value.replace(/\D/g, ''))}
        placeholder="4-8 digits"
        className="n-field"
        style={{ letterSpacing: '0.2em' }}
      />
    </label>
  )
}

function waitText(response: Response): string {
  const seconds = Number(response.headers.get('Retry-After') || 0)
  if (!seconds) return 'a little while'
  if (seconds < 90) return `${seconds} seconds`
  return `${Math.ceil(seconds / 60)} minutes`
}

/** A plain-language reason for a failed PIN or session request. */
async function failureText(response: Response, fallback: string): Promise<string> {
  if (response.status === 429) {
    return `Too many wrong PINs, so this profile is locked for ${waitText(response)}. Try again after that.`
  }
  if (response.status === 401) return 'Your sign-in on this device has ended. Sign in again to continue.'
  const payload = await response.json().catch(() => ({})) as { detail?: string }
  return payload.detail || fallback
}

export function ProfileTab({ profile, onSignedOut }: { profile: FamilyProfile; onSignedOut: () => void }) {
  const [currentPin, setCurrentPin] = useState('')
  const [newPin, setNewPin] = useState('')
  const [confirmPin, setConfirmPin] = useState('')
  const [pinNotice, setPinNotice] = useState<Notice>(null)
  const [confirmSignOut, setConfirmSignOut] = useState(false)
  const [sessionNotice, setSessionNotice] = useState<Notice>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [family, setFamily] = useState<FamilyProfile[]>([])
  const [memberAction, setMemberAction] = useState<MemberAction>(null)
  const [memberPin, setMemberPin] = useState('')
  const [memberConfirm, setMemberConfirm] = useState('')
  const [familyNotice, setFamilyNotice] = useState<Notice>(null)
  const ownPath = `/profiles/${encodeURIComponent(profile.user_id)}`

  useEffect(() => {
    if (!profile.is_owner) return
    let cancelled = false
    apiFetch('/profiles')
      .then(response => response.ok ? response.json() as Promise<{ profiles?: FamilyProfile[] }> : null)
      .then(payload => {
        if (!cancelled && payload) setFamily((payload.profiles ?? []).filter(item => item.user_id !== profile.user_id))
      })
      .catch(() => {
        if (!cancelled) setFamilyNotice({ ok: false, text: 'Could not load the family profiles.' })
      })
    return () => { cancelled = true }
  }, [profile.is_owner, profile.user_id])

  const pinReady = currentPin.length >= 4 && newPin.length >= 4 && confirmPin.length >= 4

  const changePin = useCallback(async () => {
    if (!PIN_RE.test(newPin)) {
      setPinNotice({ ok: false, text: 'A new PIN is 4 to 8 digits.' })
      return
    }
    if (newPin !== confirmPin) {
      setPinNotice({ ok: false, text: 'The two new PINs do not match.' })
      return
    }
    setBusy('pin')
    setPinNotice(null)
    try {
      const response = await apiFetch(ownPath, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ current_pin: currentPin, pin: newPin }),
      })
      if (!response.ok) {
        setPinNotice({
          ok: false,
          text: response.status === 403
            ? 'That is not your current PIN.'
            : await failureText(response, 'Your PIN could not be changed.'),
        })
        return
      }
      const payload = await response.json() as { session?: FamilyProfileSession }
      // The change signed out every older token; keep this device signed in.
      if (payload.session) setProfileSession(payload.session)
      setCurrentPin('')
      setNewPin('')
      setConfirmPin('')
      setPinNotice({ ok: true, text: 'PIN changed. Your other devices are signed out; use the new PIN there.' })
    } catch {
      setPinNotice({ ok: false, text: 'Narad could not be reached. Your PIN was not changed.' })
    } finally {
      setBusy(null)
    }
  }, [confirmPin, currentPin, newPin, ownPath])

  const signOutEverywhere = useCallback(async () => {
    setBusy('signout')
    setSessionNotice(null)
    try {
      const response = await apiFetch(`${ownPath}/revoke-sessions`, { method: 'POST' })
      // 401: this device was already signed out, which is the goal too.
      if (response.ok || response.status === 401) {
        onSignedOut()
        return
      }
      setSessionNotice({ ok: false, text: await failureText(response, 'Your devices could not be signed out.') })
    } catch {
      setSessionNotice({ ok: false, text: 'Narad could not be reached. Nothing was signed out.' })
    } finally {
      setBusy(null)
      setConfirmSignOut(false)
    }
  }, [onSignedOut, ownPath])

  const openMemberAction = (next: MemberAction) => {
    setMemberAction(next)
    setMemberPin('')
    setMemberConfirm('')
    setFamilyNotice(null)
  }

  const runMemberAction = useCallback(async (member: FamilyProfile, action: 'reset' | 'signout') => {
    if (action === 'reset' && !PIN_RE.test(memberPin)) {
      setFamilyNotice({ ok: false, text: 'A new PIN is 4 to 8 digits.' })
      return
    }
    if (action === 'reset' && memberPin !== memberConfirm) {
      setFamilyNotice({ ok: false, text: 'The two PINs do not match.' })
      return
    }
    setBusy(`${action}:${member.user_id}`)
    setFamilyNotice(null)
    try {
      const path = `/profiles/${encodeURIComponent(member.user_id)}/${action === 'reset' ? 'reset-pin' : 'revoke-sessions'}`
      const response = await apiFetch(path, {
        method: 'POST',
        ...(action === 'reset'
          ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ pin: memberPin }) }
          : {}),
      })
      if (!response.ok) {
        setFamilyNotice({ ok: false, text: await failureText(response, 'That did not work. Try again.') })
        return
      }
      setMemberAction(null)
      setMemberPin('')
      setMemberConfirm('')
      setFamilyNotice({
        ok: true,
        text: action === 'reset'
          ? `${member.display_name}'s PIN is reset and their devices are signed out. Give them the new PIN in person.`
          : `${member.display_name} is signed out on every device.`,
      })
    } catch {
      setFamilyNotice({ ok: false, text: 'Narad could not be reached. Nothing was changed.' })
    } finally {
      setBusy(null)
    }
  }, [memberConfirm, memberPin])

  return (
    <div>
      <div>
        <form
          style={card}
          onSubmit={event => { event.preventDefault(); if (pinReady && busy !== 'pin') void changePin() }}
        >
          {microLabel('Change my PIN')}
          <Hint>Choosing a new PIN signs you out on your other devices. This one stays signed in.</Hint>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 150px), 1fr))', gap: 10 }}>
            <PinField label="Current PIN" value={currentPin} onChange={setCurrentPin} autoComplete="current-password" />
            <PinField label="New PIN" value={newPin} onChange={setNewPin} autoComplete="new-password" />
            <PinField label="New PIN again" value={confirmPin} onChange={setConfirmPin} autoComplete="new-password" />
          </div>
          <div style={{ marginTop: 12 }}>
            <button type="submit" disabled={!pinReady || busy === 'pin'} style={primaryButton(pinReady && busy !== 'pin')}>
              {busy === 'pin' ? 'Changing…' : 'Change PIN'}
            </button>
          </div>
          <NoticeLine notice={pinNotice} />
        </form>

        <div style={card}>
          {microLabel('Signed-in devices')}
          <Hint>Lost a phone, or signed in somewhere you should not have? Sign out everywhere, then sign in again here with your PIN.</Hint>
          {confirmSignOut ? (
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
              <span style={{ flex: '1 1 100%', fontSize: 14, color: 'var(--ink-85)' }}>
                Sign out of Narad on all your devices, this one included?
              </span>
              <button type="button" onClick={() => void signOutEverywhere()} disabled={busy === 'signout'} style={primaryButton(busy !== 'signout')}>
                {busy === 'signout' ? 'Signing out…' : 'Yes, sign out everywhere'}
              </button>
              <button type="button" onClick={() => setConfirmSignOut(false)} disabled={busy === 'signout'} style={outlineButton()}>
                Cancel
              </button>
            </div>
          ) : (
            <button type="button" onClick={() => { setConfirmSignOut(true); setSessionNotice(null) }} style={outlineButton(true)}>
              Sign out of all my devices
            </button>
          )}
          <NoticeLine notice={sessionNotice} />
        </div>

        {profile.is_owner && (
          <>
            {microLabel('Family members')}
            <Hint>As the owner you can give someone a new PIN if they forget theirs, or sign them out on every device.</Hint>
            {family.length === 0 && !familyNotice && (
              <div style={{ fontSize: 14, color: 'var(--ink-55)' }}>No one else has a profile yet.</div>
            )}
            {family.map(member => {
              const open = memberAction?.userId === member.user_id ? memberAction.action : null
              const resetReady = memberPin.length >= 4 && memberConfirm.length >= 4
              const working = busy === `${open}:${member.user_id}`
              return (
                <div key={member.user_id} style={{ ...card, margin: '10px 0 0', padding: '14px 16px' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                    <span style={{ flex: '1 1 140px', minWidth: 0, fontSize: 15, fontWeight: 650, color: 'var(--kajal)' }}>
                      {member.display_name}
                    </span>
                    <button type="button" onClick={() => openMemberAction(open === 'reset' ? null : { userId: member.user_id, action: 'reset' })} style={outlineButton()}>
                      Reset PIN
                    </button>
                    <button type="button" onClick={() => openMemberAction(open === 'signout' ? null : { userId: member.user_id, action: 'signout' })} style={outlineButton(true)}>
                      Sign out their devices
                    </button>
                  </div>
                  {open === 'reset' && (
                    <form
                      style={{ marginTop: 12 }}
                      onSubmit={event => { event.preventDefault(); if (resetReady && !working) void runMemberAction(member, 'reset') }}
                    >
                      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 150px), 1fr))', gap: 10 }}>
                        <PinField label={`New PIN for ${member.display_name}`} value={memberPin} onChange={setMemberPin} autoComplete="new-password" autoFocus />
                        <PinField label="New PIN again" value={memberConfirm} onChange={setMemberConfirm} autoComplete="new-password" />
                      </div>
                      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 12 }}>
                        <button type="submit" disabled={!resetReady || working} style={primaryButton(resetReady && !working)}>
                          {working ? 'Resetting…' : 'Set new PIN'}
                        </button>
                        <button type="button" onClick={() => openMemberAction(null)} style={outlineButton()}>Cancel</button>
                      </div>
                    </form>
                  )}
                  {open === 'signout' && (
                    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', marginTop: 12 }}>
                      <span style={{ flex: '1 1 100%', fontSize: 14, color: 'var(--ink-85)' }}>
                        Sign {member.display_name} out on every device? They can sign in again with their PIN.
                      </span>
                      <button type="button" onClick={() => void runMemberAction(member, 'signout')} disabled={working} style={primaryButton(!working)}>
                        {working ? 'Signing out…' : 'Yes, sign them out'}
                      </button>
                      <button type="button" onClick={() => openMemberAction(null)} style={outlineButton()}>Cancel</button>
                    </div>
                  )}
                </div>
              )
            })}
            <NoticeLine notice={familyNotice} />
          </>
        )}
      </div>
    </div>
  )
}
