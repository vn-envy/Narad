/**
 * NotificationSettings — notifications on this phone, what the lock screen
 * shows, quiet hours, and care circles (sharing chosen notifications with
 * family). Rendered in System → Profile; the phone card also sits at the top
 * of Activity.
 *
 * Permission is asked only when the person taps "Turn on", never on load.
 */
import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { BellOff, BellRing, LoaderCircle, Send, Smartphone } from 'lucide-react'
import { apiFetch, type FamilyProfile } from '@/lib/api'
import {
  currentPushSubscription,
  disablePush,
  enablePush,
  fetchCareCircle,
  fetchPreferences,
  fetchPushDevices,
  fetchSharedWithMe,
  leaveCareCircle,
  notificationPermission,
  pushSupport,
  saveCareCircle,
  savePreferences,
  sendTestPush,
  type CareCircle,
  type NotificationPreferences,
  type PushDevice,
  type SharedWithMe,
} from '@/lib/notifications'

type Notice = { ok: boolean; text: string } | null

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
  display: 'inline-flex',
  alignItems: 'center',
  gap: 7,
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
  display: 'inline-flex',
  alignItems: 'center',
  gap: 6,
}) as const

/** A card's heading on the You screen. */
export function SectionTitle({ children }: { children: ReactNode }) {
  return (
    <h3 style={{ fontSize: 13, fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--ink-55)' }}>
      {children}
    </h3>
  )
}

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

function ToggleRow({
  label,
  hint,
  checked,
  disabled = false,
  onChange,
}: {
  label: string
  hint?: string
  checked: boolean
  disabled?: boolean
  onChange: (checked: boolean) => void
}) {
  return (
    <label style={{ display: 'flex', alignItems: 'flex-start', gap: 12, minHeight: 48, padding: '8px 0', cursor: disabled ? 'default' : 'pointer' }}>
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={event => onChange(event.target.checked)}
        style={{ width: 22, height: 22, margin: '1px 0 0', flex: '0 0 auto', accentColor: 'var(--sindoor)' }}
      />
      <span>
        <span style={{ display: 'block', fontSize: 15, fontWeight: 600, color: 'var(--kajal)' }}>{label}</span>
        {hint && <span style={{ display: 'block', marginTop: 2, fontSize: 13.5, lineHeight: 1.45, color: 'var(--ink-70)' }}>{hint}</span>}
      </span>
    </label>
  )
}

function errorText(cause: unknown, fallback: string): string {
  return cause instanceof Error && cause.message ? cause.message : fallback
}

// ── Notifications on this phone ─────────────────────────────────────────────

type PhoneState = 'loading' | 'on' | 'off' | 'blocked' | 'unavailable'

const UNAVAILABLE_TEXT: Record<string, string> = {
  unsupported: 'This browser cannot show notifications from Narad. On Android, open Narad in Chrome.',
  'ios-needs-install': 'On iPhone, first add Narad to your Home Screen (Share, then Add to Home Screen), and turn notifications on there.',
  insecure: 'Notifications need Narad\'s secure https:// address.',
  dev: 'Notifications work in the installed app, not in the development server.',
}

export function PhoneNotificationsCard({ userId, compact = false }: { userId: string; compact?: boolean }) {
  const support = pushSupport()
  const [state, setState] = useState<PhoneState>(support === 'supported' ? 'loading' : 'unavailable')
  const [endpoint, setEndpoint] = useState<string | null>(null)
  const [devices, setDevices] = useState<PushDevice[]>([])
  const [busy, setBusy] = useState<string | null>(null)
  const [notice, setNotice] = useState<Notice>(null)

  const refresh = useCallback(async () => {
    if (support !== 'supported') return
    if (notificationPermission() === 'denied') {
      setState('blocked')
      return
    }
    try {
      const [subscription, list] = await Promise.all([currentPushSubscription(), fetchPushDevices()])
      setDevices(list)
      setEndpoint(subscription?.endpoint ?? null)
      const linked = Boolean(subscription && list.some(device => device.endpoint === subscription.endpoint))
      setState(linked && notificationPermission() === 'granted' ? 'on' : 'off')
    } catch {
      setState('off')
    }
  }, [support])

  useEffect(() => { void refresh() }, [refresh])

  const turnOn = async () => {
    setBusy('on')
    setNotice(null)
    try {
      const subscription = await enablePush(userId)
      setEndpoint(subscription.endpoint)
      setNotice({ ok: true, text: 'Notifications are on for this phone.' })
      await refresh()
    } catch (cause) {
      setNotice({ ok: false, text: errorText(cause, 'Notifications could not be turned on.') })
      if (notificationPermission() === 'denied') setState('blocked')
    } finally {
      setBusy(null)
    }
  }

  const turnOff = async () => {
    setBusy('off')
    setNotice(null)
    try {
      await disablePush(userId)
      setNotice({ ok: true, text: 'Notifications are off for this phone. Everything still arrives in Activity.' })
      await refresh()
    } catch (cause) {
      setNotice({ ok: false, text: errorText(cause, 'Notifications could not be turned off.') })
    } finally {
      setBusy(null)
    }
  }

  const test = async () => {
    setBusy('test')
    setNotice(null)
    try {
      const result = await sendTestPush(endpoint ?? undefined)
      setNotice(result.sent > 0
        ? { ok: true, text: 'Test sent. It should appear in a few seconds (it shows in the app if Narad is open).' }
        : { ok: false, text: 'The test did not go through. Turn notifications off and on again on this phone.' })
      await refresh()
    } catch (cause) {
      setNotice({ ok: false, text: errorText(cause, 'The test could not be sent.') })
    } finally {
      setBusy(null)
    }
  }

  const title = state === 'on' ? 'On for this phone'
    : state === 'blocked' ? 'Blocked in this browser'
      : state === 'unavailable' ? 'Not available here'
        : state === 'loading' ? 'Checking this phone' : 'Off for this phone'
  const detail = state === 'on'
    ? 'Reminders and approvals reach this phone. What the lock screen shows is set in You.'
    : state === 'blocked'
      ? 'Chrome is blocking Narad\'s notifications. Tap the lock icon by the address, then Permissions, Notifications, Allow. Then turn them on here.'
      : state === 'unavailable'
        ? UNAVAILABLE_TEXT[support] ?? UNAVAILABLE_TEXT.unsupported
        : 'Get reminders and approvals on this phone, even when Narad is closed.'

  return (
    <div style={compact ? { ...card, margin: 0, padding: '14px' } : card}>
      {!compact && microLabel('Notifications on this phone')}
      <div style={{ display: 'flex', alignItems: 'center', gap: 11, marginTop: compact ? 0 : 10, flexWrap: 'wrap' }}>
        <span aria-hidden="true" style={{ width: 36, height: 36, flex: '0 0 auto', display: 'grid', placeItems: 'center', borderRadius: 10, background: state === 'on' ? 'rgba(var(--rgb-tulsi),0.12)' : 'var(--ink-05)', color: state === 'on' ? 'var(--tulsi)' : 'var(--ink-55)' }}>
          {state === 'on' ? <BellRing size={16} /> : state === 'loading' ? <LoaderCircle size={16} className="animate-spin" /> : <BellOff size={16} />}
        </span>
        <span style={{ flex: '1 1 180px', minWidth: 0 }}>
          <span style={{ display: 'block', fontSize: 15, fontWeight: 700, color: 'var(--kajal)' }}>{title}</span>
          <span style={{ display: 'block', marginTop: 2, fontSize: 13.5, lineHeight: 1.45, color: 'var(--ink-70)' }}>{detail}</span>
        </span>
        <span style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {state === 'off' && (
            <button type="button" onClick={() => void turnOn()} disabled={busy !== null} style={primaryButton(busy === null)}>
              {busy === 'on' ? <LoaderCircle size={14} className="animate-spin" /> : <BellRing size={14} />} Turn on
            </button>
          )}
          {state === 'on' && (
            <>
              <button type="button" onClick={() => void test()} disabled={busy !== null} style={outlineButton()}>
                {busy === 'test' ? <LoaderCircle size={13} className="animate-spin" /> : <Send size={13} />} Test
              </button>
              {!compact && (
                <button type="button" onClick={() => void turnOff()} disabled={busy !== null} style={outlineButton(true)}>
                  {busy === 'off' ? <LoaderCircle size={13} className="animate-spin" /> : <BellOff size={13} />} Turn off
                </button>
              )}
            </>
          )}
        </span>
      </div>
      <NoticeLine notice={notice} />
      {!compact && devices.length > 0 && (
        <div style={{ marginTop: 14, paddingTop: 12, borderTop: '1px solid var(--line)' }}>
          <div style={{ fontSize: 13, fontWeight: 650, color: 'var(--ink-55)', marginBottom: 6 }}>Your devices with notifications on</div>
          {devices.map(device => (
            <div key={device.id} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 0', fontSize: 14, color: 'var(--ink-85)' }}>
              <Smartphone size={15} aria-hidden="true" style={{ flex: '0 0 auto', color: 'var(--ink-40)' }} />
              <span style={{ flex: 1, minWidth: 0 }}>
                {device.label}{device.endpoint === endpoint ? ' (this one)' : ''}
              </span>
              <span style={{ fontSize: 12.5, color: device.last_error ? 'var(--sindoor)' : 'var(--ink-55)' }}>
                {device.last_error ? 'Last push failed' : device.last_success_at ? 'Working' : 'Not tested yet'}
              </span>
            </div>
          ))}
          <Hint>To stop notifications on another phone, turn them off on that phone, or use Sign out of all my devices below.</Hint>
        </div>
      )}
    </div>
  )
}

// ── Lock screen and quiet hours ─────────────────────────────────────────────

function NotificationPreferencesCard() {
  const [prefs, setPrefs] = useState<NotificationPreferences | null>(null)
  const [notice, setNotice] = useState<Notice>(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    let cancelled = false
    fetchPreferences()
      .then(value => { if (!cancelled) setPrefs(value) })
      .catch(() => { if (!cancelled) setNotice({ ok: false, text: 'Your notification settings could not be loaded.' }) })
    return () => { cancelled = true }
  }, [])

  const save = async (changes: Partial<NotificationPreferences>) => {
    if (!prefs) return
    const previous = prefs
    setPrefs({ ...prefs, ...changes, quiet_hours: { ...prefs.quiet_hours, ...(changes.quiet_hours ?? {}) } })
    setSaving(true)
    setNotice(null)
    try {
      const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone
      setPrefs(await savePreferences({ ...changes, timezone }))
      setNotice({ ok: true, text: 'Saved.' })
    } catch (cause) {
      setPrefs(previous)
      setNotice({ ok: false, text: errorText(cause, 'That setting could not be saved.') })
    } finally {
      setSaving(false)
    }
  }

  if (!prefs) {
    return (
      <div style={card}>
        {microLabel('What notifications show')}
        {notice ? <NoticeLine notice={notice} /> : <Hint>Loading…</Hint>}
      </div>
    )
  }
  const quiet = prefs.quiet_hours
  const timeInput = {
    minHeight: 48,
    padding: '8px 10px',
    borderRadius: 10,
    border: '1px solid var(--ink-20)',
    background: 'var(--field)',
    fontSize: 16,
    color: 'var(--kajal)',
  } as const

  return (
    <div style={card}>
      {microLabel('What notifications show')}
      <ToggleRow
        label="Show details on the lock screen"
        hint={prefs.lock_screen_details
          ? 'Anyone who picks up your phone can read what a reminder or approval is about.'
          : 'Off: the lock screen only says "Narad has a reminder for you". The details stay inside Narad.'}
        checked={prefs.lock_screen_details}
        disabled={saving}
        onChange={checked => void save({ lock_screen_details: checked })}
      />
      <ToggleRow
        label="Quiet hours"
        hint="Only urgent notifications buzz during quiet hours. Everything else waits in Activity."
        checked={quiet.enabled}
        disabled={saving}
        onChange={checked => void save({ quiet_hours: { ...quiet, enabled: checked } })}
      />
      {quiet.enabled && (
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'flex-end', margin: '4px 0 6px 32px' }}>
          <label style={{ display: 'flex', flexDirection: 'column', gap: 6, fontSize: 14, fontWeight: 600, color: 'var(--ink-70)' }}>
            From
            <input type="time" value={quiet.start} disabled={saving} style={timeInput}
              onChange={event => event.target.value && void save({ quiet_hours: { ...quiet, start: event.target.value } })} />
          </label>
          <label style={{ display: 'flex', flexDirection: 'column', gap: 6, fontSize: 14, fontWeight: 600, color: 'var(--ink-70)' }}>
            Until
            <input type="time" value={quiet.end} disabled={saving} style={timeInput}
              onChange={event => event.target.value && void save({ quiet_hours: { ...quiet, end: event.target.value } })} />
          </label>
        </div>
      )}
      {quiet.enabled && (
        <ToggleRow
          label="My medicine reminders still come through"
          hint="A reminder you set for a time inside quiet hours still buzzes then. Reminders that are running late wait."
          checked={prefs.medicine_in_quiet_hours}
          disabled={saving}
          onChange={checked => void save({ medicine_in_quiet_hours: checked })}
        />
      )}
      <NoticeLine notice={notice} />
    </div>
  )
}

// ── Care circle ─────────────────────────────────────────────────────────────

function CareCircleCard({ profile }: { profile: FamilyProfile }) {
  const [family, setFamily] = useState<FamilyProfile[]>([])
  const [circle, setCircle] = useState<CareCircle | null>(null)
  const [draft, setDraft] = useState<Record<string, string[]>>({})
  const [notice, setNotice] = useState<Notice>(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    let cancelled = false
    Promise.all([
      apiFetch('/profiles').then(response => response.ok ? response.json() as Promise<{ profiles?: FamilyProfile[] }> : { profiles: [] }),
      fetchCareCircle(),
    ])
      .then(([people, loaded]) => {
        if (cancelled) return
        setFamily((people.profiles ?? []).filter(person => person.user_id !== profile.user_id))
        setCircle(loaded)
        setDraft(Object.fromEntries(loaded.grants.map(grant => [grant.carer, grant.kinds])))
      })
      .catch(() => { if (!cancelled) setNotice({ ok: false, text: 'Your care circle could not be loaded.' }) })
    return () => { cancelled = true }
  }, [profile.user_id])

  const saved = Object.fromEntries((circle?.grants ?? []).map(grant => [grant.carer, grant.kinds]))
  const dirty = family.some(person =>
    [...(draft[person.user_id] ?? [])].sort().join() !== [...(saved[person.user_id] ?? [])].sort().join())

  const toggle = (carer: string, kind: string, on: boolean) => {
    setNotice(null)
    setDraft(current => {
      const kinds = new Set(current[carer] ?? [])
      if (on) kinds.add(kind)
      else kinds.delete(kind)
      return { ...current, [carer]: [...kinds] }
    })
  }

  const save = async () => {
    setSaving(true)
    setNotice(null)
    try {
      const grants = Object.entries(draft)
        .filter(([, kinds]) => kinds.length > 0)
        .map(([carer, kinds]) => ({ carer, kinds }))
      const next = await saveCareCircle(grants)
      setCircle(current => current ? { ...current, grants: next.grants } : current)
      setDraft(Object.fromEntries(next.grants.map(grant => [grant.carer, grant.kinds])))
      setNotice({ ok: true, text: 'Saved. Only you can change who sees your notifications.' })
    } catch (cause) {
      setNotice({ ok: false, text: errorText(cause, 'Your care circle could not be saved.') })
    } finally {
      setSaving(false)
    }
  }

  return (
    <div style={card}>
      {microLabel('Share with family')}
      <Hint>
        Let someone who looks after you see some of your notifications, for example your medicine reminders.
        They see the title and one line, never the details, and approvals stay yours to decide.
        Only you can change this; you can stop sharing at any time.
      </Hint>
      {!circle && !notice && <Hint>Loading…</Hint>}
      {circle && family.length === 0 && <div style={{ fontSize: 14, color: 'var(--ink-55)' }}>No one else has a profile yet.</div>}
      {circle && family.map(person => {
        const chosen = draft[person.user_id] ?? []
        return (
          <div key={person.user_id} style={{ padding: '10px 0', borderTop: '1px solid var(--line)' }}>
            <div style={{ fontSize: 15, fontWeight: 650, color: 'var(--kajal)' }}>
              {person.display_name}
              <span style={{ marginLeft: 8, fontSize: 13, fontWeight: 500, color: 'var(--ink-55)' }}>
                {chosen.length ? `sees ${chosen.length} kind${chosen.length === 1 ? '' : 's'}` : 'sees nothing'}
              </span>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 210px), 1fr))', columnGap: 12 }}>
              {circle.kinds.map(kind => (
                <ToggleRow
                  key={kind.id}
                  label={kind.label}
                  checked={chosen.includes(kind.id)}
                  disabled={saving}
                  onChange={on => toggle(person.user_id, kind.id, on)}
                />
              ))}
            </div>
          </div>
        )
      })}
      {circle && family.length > 0 && (
        <div style={{ marginTop: 10 }}>
          <button type="button" onClick={() => void save()} disabled={!dirty || saving} style={primaryButton(dirty && !saving)}>
            {saving ? 'Saving…' : 'Save sharing'}
          </button>
        </div>
      )}
      <NoticeLine notice={notice} />
    </div>
  )
}

function SharedWithMeCard() {
  const [shared, setShared] = useState<SharedWithMe[] | null>(null)
  const [kinds, setKinds] = useState<Record<string, string>>({})
  const [confirming, setConfirming] = useState<string | null>(null)
  const [notice, setNotice] = useState<Notice>(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    try {
      const [rows, circle] = await Promise.all([fetchSharedWithMe(), fetchCareCircle()])
      setShared(rows)
      setKinds(Object.fromEntries(circle.kinds.map(kind => [kind.id, kind.label])))
    } catch {
      setNotice({ ok: false, text: 'Could not load what family members share with you.' })
    }
  }, [])

  useEffect(() => { void load() }, [load])

  const leave = async (row: SharedWithMe) => {
    setBusy(true)
    setNotice(null)
    try {
      await leaveCareCircle(row.subject)
      setConfirming(null)
      setNotice({ ok: true, text: `You will no longer get ${row.subject_name}'s notifications.` })
      await load()
    } catch (cause) {
      setNotice({ ok: false, text: errorText(cause, 'That did not work. Try again.') })
    } finally {
      setBusy(false)
    }
  }

  if (!shared?.length && !notice) return null
  return (
    <div style={card}>
      {microLabel('Shared with you')}
      <Hint>Family members who chose to share some of their notifications with you. These arrive in your Activity.</Hint>
      {(shared ?? []).map(row => (
        <div key={row.subject} style={{ padding: '10px 0', borderTop: '1px solid var(--line)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
            <span style={{ flex: '1 1 180px', minWidth: 0 }}>
              <span style={{ display: 'block', fontSize: 15, fontWeight: 650, color: 'var(--kajal)' }}>{row.subject_name}</span>
              <span style={{ display: 'block', marginTop: 2, fontSize: 13.5, color: 'var(--ink-70)' }}>
                {row.kinds.map(kind => kinds[kind] ?? kind).join(' · ')}
              </span>
            </span>
            {confirming === row.subject ? (
              <>
                <button type="button" onClick={() => void leave(row)} disabled={busy} style={primaryButton(!busy)}>
                  {busy ? 'Leaving…' : 'Yes, stop'}
                </button>
                <button type="button" onClick={() => setConfirming(null)} disabled={busy} style={outlineButton()}>Cancel</button>
              </>
            ) : (
              <button type="button" onClick={() => { setConfirming(row.subject); setNotice(null) }} style={outlineButton(true)}>
                Leave
              </button>
            )}
          </div>
        </div>
      ))}
      <NoticeLine notice={notice} />
    </div>
  )
}

export function NotificationSettings({ profile }: { profile: FamilyProfile }) {
  return (
    <>
      <PhoneNotificationsCard userId={profile.user_id} />
      <NotificationPreferencesCard />
      <CareCircleCard profile={profile} />
      <SharedWithMeCard />
    </>
  )
}
