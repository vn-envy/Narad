/**
 * KunjiTab (कुंजी) — key & subscription management (O5/S3).
 *
 * Paste a key → provider auto-detected from its prefix → live-tested →
 * stored in the OS keychain. Never shows a stored key again (masked hint
 * only). One card per provider: status, backend, month-to-date spend,
 * test, disconnect. Subscriptions (Claude Agent SDK plan credits) are
 * shown honestly: installed / signed-in / available.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { apiFetch, getProfileSession, isOwnerSession, type FamilyProfile, type LocalModelStatus } from '@/lib/api'

interface Connection {
  provider: string
  label: string
  key_page: string
  connected: boolean
  backend: string
  hint: string
  source: string
  ts: string
  mtd_spend_usd: number
}

interface Subscription {
  provider: string
  label: string
  installed: boolean
  signed_in: boolean
  available: boolean
  detail: string
  plan: string | null
  remaining_credit: number | null
  models: string[]
  disabled_by_policy?: boolean
}

interface ConnectionsPayload {
  connections?: Connection[]
  subscriptions?: Subscription[]
  google_workspace?: GoogleWorkspaceStatus
}

interface GoogleWorkspaceStatus {
  configured: boolean
  connected: boolean
  services: Record<'gmail' | 'calendar' | 'drive' | 'photos', { read: boolean; write: boolean }>
  photos_access?: string
  reason?: string | null
}

interface InteractionTarget {
  target_id: string
  kind: 'browser_skill' | 'artemis' | 'cua'
  external_id: string
  label: string
}

interface InteractionRuntime {
  available: boolean
  ready: boolean
  reason?: string | null
  browsers?: Array<Record<string, unknown>>
  devices?: Array<Record<string, unknown>>
  adapters?: Record<string, { targets?: Array<Record<string, unknown>> }>
}

interface InteractionRuntimes {
  browser_skill: InteractionRuntime
  artemis: InteractionRuntime
  desktop: InteractionRuntime
  grants: InteractionTarget[]
}

// Mirror of kunji.detect_provider_from_key — instant feedback while typing.
const PREFIX_HINTS: Array<[string, string]> = [
  ['sk-ant-', 'anthropic'],
  ['AIza', 'google'],
  ['dsk-', 'deepseek'],
  ['sk-', 'openai'],
  ['eyJ', 'smallest'], // Smallest.ai keys are JWTs
]

const INK = 'rgba(26,24,21,'

function detectProvider(key: string): string {
  const trimmed = key.trim()
  for (const [prefix, provider] of PREFIX_HINTS) {
    if (trimmed.startsWith(prefix)) return provider
  }
  return ''
}

function microLabel(text: string) {
  return (
    <div style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.18em', color: `${INK}0.42)` }}>
      {text}
    </div>
  )
}

export function KunjiTab({ onOpenSetup }: { onOpenSetup?: () => void }) {
  const [connections, setConnections] = useState<Connection[]>([])
  const [subscriptions, setSubscriptions] = useState<Subscription[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [keyInput, setKeyInput] = useState('')
  const [providerOverride, setProviderOverride] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [testResults, setTestResults] = useState<Record<string, { ok: boolean; detail: string }>>({})
  const [localModel, setLocalModel] = useState<LocalModelStatus | null>(null)
  const [googleWorkspace, setGoogleWorkspace] = useState<GoogleWorkspaceStatus | null>(null)
  const [interactionRuntimes, setInteractionRuntimes] = useState<InteractionRuntimes | null>(null)
  const localPollRef = useRef<number | null>(null)
  // Device grants are owner-only: the owner picks whose devices to manage.
  const isOwner = isOwnerSession()
  const ownProfileId = getProfileSession()?.profile.user_id ?? 'default'
  const [familyProfiles, setFamilyProfiles] = useState<FamilyProfile[]>([])
  const [grantProfileId, setGrantProfileId] = useState(ownProfileId)
  const [profileGrants, setProfileGrants] = useState<InteractionTarget[] | null>(null)
  const managingOther = isOwner && grantProfileId !== ownProfileId
  const grantProfileName = familyProfiles.find(item => item.user_id === grantProfileId)?.display_name ?? 'this profile'

  const loadProfileGrants = useCallback(async () => {
    if (!managingOther) {
      setProfileGrants(null)
      return
    }
    try {
      const response = await apiFetch(`/interaction-targets?profile_id=${encodeURIComponent(grantProfileId)}`)
      if (!response.ok) throw new Error(`${response.status}`)
      const data = await response.json() as { targets?: InteractionTarget[] }
      setProfileGrants(data.targets ?? [])
    } catch {
      setProfileGrants([])
    }
  }, [grantProfileId, managingOther])

  useEffect(() => {
    void loadProfileGrants()
  }, [loadProfileGrants])

  const load = useCallback(async () => {
    try {
      const [response, localResponse, interactionResponse, profilesResponse] = await Promise.all([
        apiFetch('/connections'),
        apiFetch('/local-model/status').catch(() => null),
        apiFetch('/interaction-runtimes').catch(() => null),
        isOwner ? apiFetch('/profiles').catch(() => null) : Promise.resolve(null),
      ])
      if (!response.ok) throw new Error(`${response.status}`)
      const data: ConnectionsPayload = await response.json()
      setConnections(data.connections ?? [])
      setSubscriptions(data.subscriptions ?? [])
      setGoogleWorkspace(data.google_workspace ?? null)
      if (localResponse?.ok) setLocalModel(await localResponse.json() as LocalModelStatus)
      if (interactionResponse?.ok) setInteractionRuntimes(await interactionResponse.json() as InteractionRuntimes)
      if (profilesResponse?.ok) setFamilyProfiles(((await profilesResponse.json()) as { profiles?: FamilyProfile[] }).profiles ?? [])
      setLoadError(null)
    } catch {
      setLoadError('Could not reach /connections — is the server running?')
    } finally {
      setLoading(false)
    }
  }, [isOwner])

  useEffect(() => {
    void load()
    return () => {
      if (localPollRef.current !== null) window.clearTimeout(localPollRef.current)
    }
  }, [load])

  const detected = useMemo(() => providerOverride || detectProvider(keyInput), [keyInput, providerOverride])
  const detectedLabel = useMemo(
    () => connections.find(c => c.provider === detected)?.label ?? detected,
    [connections, detected],
  )

  const connect = useCallback(async () => {
    if (!keyInput.trim()) return
    setBusy('connect')
    setNotice(null)
    try {
      const response = await apiFetch('/connections', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key: keyInput.trim(), ...(providerOverride ? { provider: providerOverride } : {}) }),
      })
      const data = await response.json().catch(() => ({}))
      if (response.ok && data.ok) {
        setNotice(`✓ ${data.detail ?? 'connected'}`)
        setKeyInput('')
        setProviderOverride('')
        await load()
      } else {
        setNotice(`✕ ${data.detail ?? 'that key did not validate'}`)
      }
    } catch {
      setNotice('✕ request failed — server unreachable')
    } finally {
      setBusy(null)
    }
  }, [keyInput, providerOverride, load])

  const testConnection = useCallback(async (provider: string) => {
    setBusy(`test:${provider}`)
    try {
      const response = await apiFetch(`/connections/${provider}/test`, { method: 'POST' })
      const data = await response.json().catch(() => ({}))
      setTestResults(prev => ({ ...prev, [provider]: { ok: Boolean(data.ok), detail: String(data.detail ?? '') } }))
    } catch {
      setTestResults(prev => ({ ...prev, [provider]: { ok: false, detail: 'request failed' } }))
    } finally {
      setBusy(null)
    }
  }, [])

  const disconnect = useCallback(async (provider: string) => {
    setBusy(`disconnect:${provider}`)
    try {
      await apiFetch(`/connections/${provider}`, { method: 'DELETE' })
      setTestResults(prev => {
        const next = { ...prev }
        delete next[provider]
        return next
      })
      await load()
    } finally {
      setBusy(null)
    }
  }, [load])

  // Grok is disabled by owner policy: no sign-in is offered, but a stored
  // sign-in can still be removed.
  const disconnectGrok = useCallback(async () => {
    setBusy('grok:disconnect')
    try {
      const response = await apiFetch('/connections/xai/oauth', { method: 'DELETE' })
      if (!response.ok) {
        const data = await response.json().catch(() => ({}))
        setNotice(`✕ ${data.detail ?? 'could not disconnect Grok'}`)
      }
      await load()
    } catch {
      setNotice('✕ request failed — server unreachable')
    } finally {
      setBusy(null)
    }
  }, [load])

  const connectGoogle = useCallback(async (access: 'read' | 'write') => {
    setBusy(`google:${access}`)
    setNotice(null)
    try {
      const response = await apiFetch('/connections/google/oauth/start', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ services: ['gmail', 'calendar', 'drive', 'photos'], access }),
      })
      const data = await response.json().catch(() => ({}))
      if (!response.ok || !data.authorize_url) {
        setNotice(`✕ ${data.detail ?? 'Google sign-in could not start'}`)
        setBusy(null)
        return
      }
      window.open(data.authorize_url, '_blank', 'noopener')
      let remaining = 60
      const poll = window.setInterval(async () => {
        remaining -= 1
        try {
          const statusResponse = await apiFetch('/connections/google/oauth/status')
          const status = await statusResponse.json() as GoogleWorkspaceStatus
          if (status.connected && (access === 'read' || Object.values(status.services).some(service => service.write))) {
            window.clearInterval(poll)
            setBusy(null)
            setNotice('✓ Google connected with your selected permissions')
            await load()
          }
        } catch { /* keep polling while consent is open */ }
        if (remaining <= 0) {
          window.clearInterval(poll)
          setBusy(null)
        }
      }, 2000)
    } catch {
      setNotice('✕ request failed — server unreachable')
      setBusy(null)
    }
  }, [load])

  const disconnectGoogle = useCallback(async () => {
    setBusy('google:disconnect')
    try {
      await apiFetch('/connections/google/oauth', { method: 'DELETE' })
      setNotice('Google disconnected')
      await load()
    } finally {
      setBusy(null)
    }
  }, [load])

  const grantInteractionTarget = useCallback(async (
    kind: InteractionTarget['kind'], externalId: string, label: string,
  ) => {
    setBusy(`interaction:${kind}:${externalId}`)
    try {
      const response = await apiFetch('/interaction-targets', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ kind, external_id: externalId, label, ...(managingOther ? { profile_id: grantProfileId } : {}) }),
      })
      if (!response.ok) throw new Error('target grant failed')
      setNotice(`${label} is now available to ${managingOther ? grantProfileName : 'this profile'}`)
      await load()
      await loadProfileGrants()
    } catch {
      setNotice('Could not grant that local control target')
    } finally {
      setBusy(null)
    }
  }, [load, loadProfileGrants, managingOther, grantProfileId, grantProfileName])

  const revokeInteractionTarget = useCallback(async (targetId: string) => {
    setBusy(`interaction:revoke:${targetId}`)
    try {
      const scope = managingOther ? `?profile_id=${encodeURIComponent(grantProfileId)}` : ''
      const response = await apiFetch(`/interaction-targets/${encodeURIComponent(targetId)}${scope}`, {
        method: 'DELETE',
      })
      if (!response.ok) throw new Error('target revoke failed')
      await load()
      await loadProfileGrants()
    } finally {
      setBusy(null)
    }
  }, [load, loadProfileGrants, managingOther, grantProfileId])

  const importEnv = useCallback(async () => {
    setBusy('import')
    setNotice(null)
    try {
      const response = await apiFetch('/connections/import-env', { method: 'POST' })
      const data = await response.json().catch(() => ({}))
      const imported: string[] = data.imported ?? []
      setNotice(imported.length > 0 ? `✓ imported from .env: ${imported.join(', ')}` : 'nothing new found in the environment')
      await load()
    } catch {
      setNotice('✕ import failed — server unreachable')
    } finally {
      setBusy(null)
    }
  }, [load])

  const installLocalModel = useCallback(async () => {
    setBusy('local-model')
    setNotice(null)
    try {
      const response = await apiFetch('/local-model/install', { method: 'POST' })
      const first = await response.json().catch(() => ({})) as LocalModelStatus & { detail?: string }
      if (!response.ok) throw new Error(first.detail || 'Offline model install could not start')
      setLocalModel(first)

      const poll = async () => {
        try {
          const statusResponse = await apiFetch('/local-model/status')
          const next = await statusResponse.json() as LocalModelStatus
          setLocalModel(next)
          if (next.ready) {
            setBusy(null)
            setNotice(`✓ Gemma 4 ${next.model_size} is ready offline`)
            return
          }
          if (next.install.state === 'error') {
            setBusy(null)
            setNotice(`✕ ${next.install.error || 'model download failed'}`)
            return
          }
        } catch {
          // Keep the download alive through a transient UI refresh.
        }
        localPollRef.current = window.setTimeout(() => void poll(), 1500)
      }
      if (!first.ready) localPollRef.current = window.setTimeout(() => void poll(), 1000)
      else setBusy(null)
    } catch (cause) {
      setBusy(null)
      setNotice(`✕ ${cause instanceof Error ? cause.message : 'offline model setup failed'}`)
    }
  }, [])

  if (loading) {
    return (
      <div style={{ padding: 24 }} aria-label="Loading connections">
        {[0.9, 0.65, 0.4].map(opacity => (
          <div key={opacity} className="skeleton" style={{ height: 64, borderRadius: 16, marginBottom: 12, opacity }} />
        ))}
      </div>
    )
  }

  return (
    <div className="panel-scroll" style={{ height: '100%', overflow: 'auto', padding: '20px 24px 32px' }}>
      {loadError && (
        <div style={{ padding: '10px 14px', borderRadius: 12, marginBottom: 16, border: '1px solid rgba(224,90,43,0.35)', background: 'rgba(224,90,43,0.08)', color: 'var(--sindoor)', fontSize: 12 }}>
          {loadError}
        </div>
      )}

      {onOpenSetup && (
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 14, padding: '12px 14px', borderRadius: 14, marginBottom: 16, border: `1px solid ${INK}0.09)`, background: 'linear-gradient(110deg, rgba(53,94,59,0.08), rgba(252,250,242,0.82))' }}>
          <div>
            <div style={{ fontSize: 12, fontWeight: 700, color: `${INK}0.82)` }}>Need the simple path?</div>
            <div style={{ marginTop: 3, fontSize: 10.5, color: `${INK}0.5)` }}>Run the guided setup again without changing any connection automatically.</div>
          </div>
          <button type="button" onClick={onOpenSetup} style={{ flex: '0 0 auto', padding: '8px 12px', borderRadius: 9, border: 0, background: 'var(--kajal)', color: 'var(--paper)', fontSize: 10.5, fontWeight: 650, cursor: 'pointer' }}>
            Open setup
          </button>
        </div>
      )}

      {localModel && (
        <>
          {microLabel('Offline default')}
          <div style={{ padding: '15px 17px', borderRadius: 16, border: `1px solid ${localModel.ready ? 'rgba(53,94,59,0.3)' : `${INK}0.09)`}`, background: localModel.ready ? 'linear-gradient(135deg, rgba(53,94,59,0.1), rgba(252,250,242,0.9))' : 'linear-gradient(135deg, rgba(194,65,12,0.07), rgba(252,250,242,0.9))', margin: '10px 0 20px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 14, alignItems: 'center', flexWrap: 'wrap' }}>
              <div style={{ minWidth: 0 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span style={{ width: 8, height: 8, borderRadius: '50%', background: localModel.ready ? 'var(--tulsi)' : localModel.install.state === 'running' ? 'var(--haldi)' : `${INK}0.2)` }} />
                  <span style={{ fontSize: 13, fontWeight: 700, color: `${INK}0.86)` }}>Gemma 4 {localModel.model_size} · Q4</span>
                  <span style={{ padding: '2px 7px', borderRadius: 99, background: `${INK}0.06)`, fontSize: 8.5, letterSpacing: '0.08em', textTransform: 'uppercase', color: `${INK}0.5)` }}>no key</span>
                </div>
                <div style={{ marginTop: 7, fontSize: 10.5, lineHeight: 1.5, color: `${INK}0.52)` }}>
                  {localModel.ready
                    ? `Ready for chat, images, Narad tools, browser use, and computer use · ${Math.round(localModel.configured_context_tokens / 1024)}K working context · ${localModel.residency}`
                    : localModel.install.state === 'running'
                      ? localModel.install.status
                      : localModel.reason}
                </div>
              </div>
              {localModel.ready ? (
                <span style={{ fontSize: 10, fontWeight: 700, color: 'var(--tulsi)' }}>✓ Offline fallback ready</span>
              ) : !isOwner ? (
                <span style={{ fontSize: 10, color: `${INK}0.5)` }}>The Narad owner can add it</span>
              ) : localModel.runtime_installed ? (
                <button type="button" onClick={() => void installLocalModel()} disabled={busy === 'local-model' || localModel.install.state === 'running'} style={{ padding: '8px 13px', borderRadius: 9, border: 0, background: 'var(--tulsi)', color: '#fff', fontSize: 10.5, fontWeight: 700, cursor: busy === 'local-model' || localModel.install.state === 'running' ? 'wait' : 'pointer' }}>
                  {busy === 'local-model' || localModel.install.state === 'running' ? `${Math.round((localModel.install.progress || 0) * 100)}%` : `Download ${localModel.download_gb} GB`}
                </button>
              ) : (
                <a href="https://ollama.com/download" target="_blank" rel="noreferrer" style={{ padding: '8px 13px', borderRadius: 9, background: 'var(--kajal)', color: '#fff', fontSize: 10.5, fontWeight: 700, textDecoration: 'none' }}>Install runtime ↗</a>
              )}
            </div>
            {localModel.install.state === 'running' && <div style={{ height: 4, marginTop: 11, borderRadius: 99, overflow: 'hidden', background: `${INK}0.08)` }}><div style={{ width: `${Math.max(2, localModel.install.progress * 100)}%`, height: '100%', borderRadius: 99, background: 'var(--tulsi)', transition: 'width 250ms ease' }} /></div>}
            {localModel.memory_constrained && <div style={{ marginTop: 8, fontSize: 9.5, color: `${INK}0.43)` }}>Adaptive memory mode is active: Narad keeps Ollama available but releases model weights after use so the dashboard and browser stay responsive.</div>}
          </div>
        </>
      )}

      {googleWorkspace && (
        <div style={{ padding: '16px 18px', borderRadius: 16, border: `1px solid ${googleWorkspace.connected ? 'rgba(53,94,59,0.28)' : `${INK}0.09)`}`, background: 'linear-gradient(135deg, rgba(66,133,244,0.06), rgba(252,250,242,0.94))', marginBottom: 20 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, alignItems: 'flex-start', flexWrap: 'wrap' }}>
            <div>
              {microLabel('Google workspace')}
              <div style={{ marginTop: 6, fontSize: 13, fontWeight: 700, color: `${INK}0.84)` }}>One account, permission by permission</div>
              <div style={{ marginTop: 4, maxWidth: 580, fontSize: 10.5, lineHeight: 1.55, color: `${INK}0.52)` }}>
                Connect Gmail, Calendar, Drive, and user-selected Photos. Narad reads or writes only the access you approve; Calendar remains optional.
              </div>
            </div>
            <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap' }}>
              {!googleWorkspace.connected && <button type="button" disabled={!googleWorkspace.configured || busy === 'google:read'} onClick={() => void connectGoogle('read')} style={{ padding: '8px 12px', borderRadius: 9, border: 0, background: googleWorkspace.configured ? 'var(--kajal)' : `${INK}0.12)`, color: googleWorkspace.configured ? 'var(--paper)' : `${INK}0.42)`, fontSize: 10.5, fontWeight: 650, cursor: googleWorkspace.configured ? 'pointer' : 'default' }}>Connect Google</button>}
              {googleWorkspace.connected && <button type="button" disabled={busy === 'google:write'} onClick={() => void connectGoogle('write')} style={{ padding: '8px 12px', borderRadius: 9, border: `1px solid ${INK}0.14)`, background: 'transparent', color: `${INK}0.72)`, fontSize: 10.5, cursor: 'pointer' }}>Allow writes</button>}
              {googleWorkspace.connected && <button type="button" disabled={busy === 'google:disconnect'} onClick={() => void disconnectGoogle()} style={{ padding: '8px 12px', borderRadius: 9, border: '1px solid rgba(224,90,43,0.25)', background: 'transparent', color: 'var(--sindoor)', fontSize: 10.5, cursor: 'pointer' }}>Disconnect</button>}
            </div>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(90px, 1fr))', gap: 8, marginTop: 13 }}>
            {(['gmail', 'calendar', 'drive', 'photos'] as const).map(service => {
              const access = googleWorkspace.services[service]
              return <div key={service} style={{ padding: '9px 10px', borderRadius: 10, background: `${INK}0.035)`, border: `1px solid ${INK}0.06)` }}><div style={{ fontSize: 10.5, fontWeight: 650, textTransform: 'capitalize', color: `${INK}0.76)` }}>{service}</div><div style={{ marginTop: 3, fontSize: 9.5, color: access.write ? 'var(--tulsi)' : access.read ? 'var(--nila)' : `${INK}0.38)` }}>{access.write ? 'read + write' : access.read ? 'read only' : 'not allowed'}</div></div>
            })}
          </div>
          {!googleWorkspace.configured && <div style={{ marginTop: 10, fontSize: 9.5, color: `${INK}0.44)` }}>Narad’s Google OAuth client is not configured on this installation. Set GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET once; users then connect here without API keys.</div>}
          <div style={{ marginTop: 8, fontSize: 9.5, color: `${INK}0.4)` }}>Photos uses Google’s picker: existing library items are visible only after you select them.</div>
        </div>
      )}

      {interactionRuntimes && (
        <>
          {microLabel('Local control')}
          {isOwner && familyProfiles.length > 1 && (
            <label style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 8, fontSize: 10.5, color: `${INK}0.6)` }}>
              Manage devices for
              <select value={grantProfileId} onChange={event => setGrantProfileId(event.target.value)} aria-label="Family profile whose devices to manage" style={{ padding: '5px 8px', borderRadius: 8, border: `1px solid ${INK}0.14)`, background: 'var(--paper)', color: `${INK}0.8)`, fontSize: 10.5 }}>
                {familyProfiles.map(item => <option key={item.user_id} value={item.user_id}>{item.user_id === ownProfileId ? `${item.display_name} (you)` : item.display_name}</option>)}
              </select>
            </label>
          )}
          {!isOwner && <div style={{ marginTop: 8, fontSize: 10.5, color: `${INK}0.5)` }}>Ask the Narad owner to allow a browser, desktop, or phone for your profile.</div>}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 260px), 1fr))', gap: 10, margin: '10px 0 20px' }}>
            {([
              { kind: 'browser_skill' as const, title: 'Signed-in browser', runtime: interactionRuntimes.browser_skill, items: interactionRuntimes.browser_skill.browsers ?? [] },
              { kind: 'cua' as const, title: 'Desktop computer', runtime: interactionRuntimes.desktop, items: interactionRuntimes.desktop.adapters?.cua?.targets ?? [] },
              { kind: 'artemis' as const, title: 'Android phone', runtime: interactionRuntimes.artemis, items: interactionRuntimes.artemis.devices ?? [] },
            ]).map(section => {
              const grants = (profileGrants ?? interactionRuntimes.grants).filter(item => item.kind === section.kind)
              return (
                <div key={section.kind} style={{ padding: '14px 15px', borderRadius: 15, border: `1px solid ${section.runtime.ready ? 'rgba(53,94,59,0.24)' : `${INK}0.09)`}`, background: 'rgba(252,250,242,0.88)', minWidth: 0 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span style={{ width: 8, height: 8, borderRadius: '50%', background: section.runtime.ready ? 'var(--tulsi)' : `${INK}0.18)` }} />
                    <span style={{ fontSize: 12.5, fontWeight: 700, color: `${INK}0.82)` }}>{section.title}</span>
                    <span style={{ marginLeft: 'auto', fontSize: 9, color: `${INK}0.42)` }}>{section.kind === 'artemis' ? 'Android' : section.kind === 'cua' ? 'CUA + Jev' : 'BrowserSkill'}</span>
                  </div>
                  <div style={{ marginTop: 7, fontSize: 10.25, lineHeight: 1.5, color: `${INK}0.5)` }}>
                    {section.runtime.ready
                      ? section.kind === 'browser_skill' ? 'Use an existing Chromium login without sharing it across family profiles.' : section.kind === 'cua' ? 'Run confirmed desktop tasks through CUA with Jev admission and verification.' : 'Run previewed tasks on a local Android device through Artemis and Jev.'
                      : section.runtime.reason || 'Optional runtime is not connected.'}
                  </div>
                  {grants.map(grant => (
                    <div key={grant.target_id} style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 10, padding: '8px 9px', borderRadius: 10, background: `${INK}0.035)` }}>
                      <span style={{ minWidth: 0, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontSize: 10.5, color: `${INK}0.7)` }}>{grant.label}</span>
                      {isOwner && <button type="button" onClick={() => void revokeInteractionTarget(grant.target_id)} disabled={busy === `interaction:revoke:${grant.target_id}`} style={{ border: 0, background: 'transparent', color: 'var(--sindoor)', fontSize: 9.5, cursor: 'pointer' }}>Remove</button>}
                    </div>
                  ))}
                  {isOwner && section.items.map((item, index) => {
                    const externalId = String(item.instance_id ?? item.serial ?? item.device_serial ?? item.id ?? '')
                    if (!externalId || grants.some(grant => grant.external_id === externalId)) return null
                    const label = String(item.label ?? item.name ?? item.model ?? externalId)
                    return <button key={`${externalId}:${index}`} type="button" onClick={() => void grantInteractionTarget(section.kind, externalId, label)} disabled={busy === `interaction:${section.kind}:${externalId}`} style={{ width: '100%', marginTop: 8, padding: '8px 10px', borderRadius: 9, border: `1px solid ${INK}0.12)`, background: 'transparent', color: `${INK}0.7)`, fontSize: 10.5, textAlign: 'left', cursor: 'pointer' }}>Allow {label} for {managingOther ? grantProfileName : 'this profile'}</button>
                  })}
                </div>
              )
            })}
          </div>
        </>
      )}

      {/* Paste-a-key composer */}
      <div
        style={{
          padding: '16px 18px',
          borderRadius: 16,
          border: `1px solid ${INK}0.08)`,
          background: 'linear-gradient(145deg, color-mix(in srgb, var(--haldi) 8%, var(--paper)) 0%, rgba(252,250,242,0.94) 100%)',
          marginBottom: 20,
        }}
      >
        {microLabel('Connect a provider')}
        <div style={{ fontSize: 12, color: `${INK}0.6)`, margin: '6px 0 10px' }}>
          Paste an API key — Narad recognises the provider, runs a one-token test, and stores it in your
          system keychain. The key is never shown again.
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <input
            type="password"
            value={keyInput}
            onChange={e => setKeyInput(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') void connect() }}
            placeholder="sk-ant-…  AIza…  dsk-…  sk-…"
            aria-label="API key"
            style={{
              flex: '1 1 260px',
              padding: '9px 12px',
              borderRadius: 10,
              border: `1px solid ${INK}0.14)`,
              background: 'var(--paper)',
              fontFamily: 'monospace',
              fontSize: 12,
              color: `${INK}0.85)`,
            }}
          />
          <select
            value={providerOverride}
            onChange={e => setProviderOverride(e.target.value)}
            aria-label="Provider override"
            style={{ padding: '9px 10px', borderRadius: 10, border: `1px solid ${INK}0.14)`, background: 'var(--paper)', fontSize: 12, color: `${INK}0.7)` }}
          >
            <option value="">auto-detect</option>
            {connections.map(c => (
              <option key={c.provider} value={c.provider}>{c.label}</option>
            ))}
          </select>
          <button
            onClick={() => void connect()}
            disabled={busy === 'connect' || !keyInput.trim() || !detected}
            style={{
              padding: '9px 18px',
              borderRadius: 10,
              border: 'none',
              background: keyInput.trim() && detected ? 'var(--sindoor)' : `${INK}0.12)`,
              color: keyInput.trim() && detected ? '#fcfaf2' : `${INK}0.45)`,
              fontSize: 12,
              fontWeight: 600,
              cursor: keyInput.trim() && detected ? 'pointer' : 'default',
            }}
          >
            {busy === 'connect' ? 'testing…' : 'Connect'}
          </button>
        </div>
        {keyInput.trim() && (
          <div style={{ fontSize: 11, marginTop: 8, color: detected ? 'var(--tulsi)' : 'var(--sindoor)' }}>
            {detected ? `Looks like a ${detectedLabel} key` : 'Unrecognised prefix — pick the provider explicitly'}
          </div>
        )}
        {notice && (
          <div style={{ fontSize: 11, marginTop: 8, color: notice.startsWith('✓') ? 'var(--tulsi)' : 'var(--sindoor)' }}>
            {notice}
          </div>
        )}
      </div>

      {/* Provider cards */}
      {microLabel('Providers')}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: 12, margin: '10px 0 24px' }}>
        {connections.map(conn => {
          const test = testResults[conn.provider]
          return (
            <div
              key={conn.provider}
              style={{
                padding: '14px 16px',
                borderRadius: 16,
                border: `1px solid ${conn.connected ? 'rgba(53,94,59,0.30)' : `${INK}0.08)`}`,
                background: conn.connected
                  ? 'linear-gradient(145deg, color-mix(in srgb, var(--tulsi) 8%, var(--paper)) 0%, rgba(252,250,242,0.94) 100%)'
                  : 'rgba(252,250,242,0.85)',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span
                  aria-label={conn.connected ? 'connected' : 'not connected'}
                  style={{
                    width: 8,
                    height: 8,
                    borderRadius: '50%',
                    flexShrink: 0,
                    background: conn.connected ? 'var(--tulsi)' : `${INK}0.20)`,
                  }}
                />
                <span style={{ fontSize: 13, fontWeight: 600, color: `${INK}0.85)` }}>{conn.label}</span>
                {conn.connected && conn.backend && (
                  <span style={{ fontSize: 9, padding: '2px 8px', borderRadius: 999, background: `${INK}0.07)`, color: `${INK}0.55)`, textTransform: 'uppercase', letterSpacing: '0.08em' }}>
                    {conn.backend === 'keyring' ? 'keychain' : conn.backend}
                  </span>
                )}
              </div>

              {conn.connected ? (
                <>
                  <div style={{ display: 'flex', gap: 16, margin: '10px 0 12px', fontSize: 11, color: `${INK}0.55)` }}>
                    <span style={{ fontFamily: 'monospace' }}>{conn.hint || '····'}</span>
                    <span>this month: <strong style={{ color: `${INK}0.75)` }}>${conn.mtd_spend_usd.toFixed(2)}</strong></span>
                  </div>
                  {isOwner && <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                    <button
                      onClick={() => void testConnection(conn.provider)}
                      disabled={busy === `test:${conn.provider}`}
                      style={{ padding: '6px 14px', borderRadius: 8, border: `1px solid ${INK}0.16)`, background: 'transparent', fontSize: 11, color: `${INK}0.7)`, cursor: 'pointer' }}
                    >
                      {busy === `test:${conn.provider}` ? 'testing…' : 'Test'}
                    </button>
                    <button
                      onClick={() => void disconnect(conn.provider)}
                      disabled={busy === `disconnect:${conn.provider}`}
                      style={{ padding: '6px 14px', borderRadius: 8, border: '1px solid rgba(224,90,43,0.30)', background: 'transparent', fontSize: 11, color: 'var(--sindoor)', cursor: 'pointer' }}
                    >
                      Disconnect
                    </button>
                    {test && (
                      <span style={{ fontSize: 11, color: test.ok ? 'var(--tulsi)' : 'var(--sindoor)' }}>
                        {test.ok ? '✓' : '✕'} {test.detail}
                      </span>
                    )}
                  </div>}
                </>
              ) : (
                <div style={{ marginTop: 10 }}>
                  <a
                    href={conn.key_page}
                    target="_blank"
                    rel="noreferrer"
                    style={{ fontSize: 11, color: 'var(--nila)', textDecoration: 'underline', textUnderlineOffset: 3 }}
                  >
                    Get a key ↗
                  </a>
                </div>
              )}
            </div>
          )
        })}
      </div>

      {/* Subscriptions */}
      {microLabel('Subscriptions')}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: 12, margin: '10px 0 24px' }}>
        {subscriptions.filter(sub => !sub.disabled_by_policy || sub.signed_in).map(sub => (
          <div
            key={sub.provider}
            style={{
              padding: '14px 16px',
              borderRadius: 16,
              border: `1px solid ${sub.available ? 'rgba(53,94,59,0.30)' : `${INK}0.08)`}`,
              background: 'rgba(252,250,242,0.85)',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span
                style={{
                  width: 8,
                  height: 8,
                  borderRadius: '50%',
                  flexShrink: 0,
                  background: sub.available ? 'var(--tulsi)' : sub.installed && !sub.disabled_by_policy ? 'var(--haldi)' : `${INK}0.20)`,
                }}
              />
              <span style={{ fontSize: 13, fontWeight: 600, color: `${INK}0.85)` }}>{sub.label}</span>
              {sub.plan && (
                <span style={{ fontSize: 9, padding: '2px 8px', borderRadius: 999, background: `${INK}0.07)`, color: `${INK}0.55)`, textTransform: 'uppercase', letterSpacing: '0.08em' }}>
                  {sub.plan}
                </span>
              )}
              {sub.disabled_by_policy && (
                <span style={{ fontSize: 9, padding: '2px 8px', borderRadius: 999, background: `${INK}0.07)`, color: `${INK}0.55)`, textTransform: 'uppercase', letterSpacing: '0.08em' }}>
                  disabled by owner policy
                </span>
              )}
            </div>
            <div style={{ fontSize: 11, color: `${INK}0.55)`, marginTop: 8 }}>{sub.detail}</div>
            <div style={{ display: 'flex', gap: 12, marginTop: 8, fontSize: 10, color: `${INK}0.45)` }}>
              {sub.provider !== 'xai-oauth' && (
                <span>{sub.installed ? '✓ SDK installed' : '· SDK not installed'}</span>
              )}
              <span>{sub.signed_in ? '✓ signed in' : '· not signed in'}</span>
            </div>
            {sub.provider === 'xai-oauth' && sub.signed_in && (
              <div style={{ display: 'flex', gap: 8, marginTop: 10, flexWrap: 'wrap' }}>
                <button
                  onClick={() => void disconnectGrok()}
                  disabled={busy === 'grok:disconnect'}
                  style={{ padding: '6px 14px', borderRadius: 8, border: '1px solid rgba(224,90,43,0.30)', background: 'transparent', fontSize: 11, color: 'var(--sindoor)', cursor: 'pointer' }}
                >
                  Disconnect
                </button>
              </div>
            )}
          </div>
        ))}
      </div>

      {/* Power-user escape hatch */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, paddingTop: 4 }}>
        <button
          onClick={() => void importEnv()}
          disabled={busy === 'import'}
          style={{ padding: '7px 14px', borderRadius: 8, border: `1px solid ${INK}0.16)`, background: 'transparent', fontSize: 11, color: `${INK}0.6)`, cursor: 'pointer' }}
        >
          {busy === 'import' ? 'importing…' : 'Import keys from .env'}
        </button>
        <span style={{ fontSize: 10, color: `${INK}0.4)` }}>
          .env stays the power-user escape hatch — a real environment variable always wins over a stored key.
        </span>
      </div>
    </div>
  )
}
