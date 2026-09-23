import { useEffect, useRef, useState } from 'react'
import {
  ArrowRight,
  Check,
  CheckCircle2,
  Download,
  ExternalLink,
  KeyRound,
  LoaderCircle,
  LockKeyhole,
  MessageCircle,
  Monitor,
  Search,
  ShieldCheck,
  Smartphone,
  Sparkles,
  Workflow,
  X,
} from 'lucide-react'
import { MahatiLogo } from './MahatiLogo'
import { useIsMobile } from '@/hooks/useIsMobile'
import {
  apiFetch,
  apiUrl,
  type ConnectionsPayload,
  type LocalModelStatus,
  type OnboardingStatus,
  type RuntimeCapabilities,
} from '@/lib/api'

interface Props {
  userId: string
  initialStatus: OnboardingStatus
  capabilities: RuntimeCapabilities | null
  onFinished: (status: OnboardingStatus, destination: 'chat' | 'workspaces') => void
}

const MODEL_PROVIDERS = new Set(['google', 'deepseek', 'openai', 'anthropic'])

async function readJson<T>(response: Response): Promise<T> {
  const payload = await response.json().catch(() => ({})) as T & { detail?: string }
  if (!response.ok) throw new Error(payload.detail || `Request failed (${response.status})`)
  return payload
}

function providerLabel(provider: string): string {
  const labels: Record<string, string> = {
    anthropic: 'Claude',
    deepseek: 'DeepSeek',
    google: 'Gemini',
    openai: 'OpenAI',
    'xai-oauth': 'Grok',
    local: 'Local model',
    'narad-local': 'Local model',
  }
  return labels[provider] || provider
}

function StatusRow({ ready, title, detail, optional = false }: { ready: boolean; title: string; detail: string; optional?: boolean }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '28px minmax(0,1fr) auto', gap: 10, alignItems: 'center', padding: '11px 0', borderBottom: '1px solid rgba(45,42,38,0.08)' }}>
      <span style={{ width: 27, height: 27, display: 'grid', placeItems: 'center', borderRadius: 9, background: ready ? 'rgba(53,94,59,0.1)' : 'rgba(180,83,9,0.08)', color: ready ? 'var(--tulsi)' : '#b45309' }}>
        {ready ? <Check size={14} /> : <span style={{ fontSize: 11 }}>-</span>}
      </span>
      <span>
        <span style={{ display: 'block', fontSize: 12, fontWeight: 750, color: 'var(--kajal)' }}>{title}</span>
        <span style={{ display: 'block', marginTop: 2, fontSize: 10.5, lineHeight: 1.35, color: 'rgba(45,42,38,0.5)' }}>{detail}</span>
      </span>
      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 8.5, letterSpacing: '0.08em', textTransform: 'uppercase', color: ready ? 'var(--tulsi)' : 'rgba(45,42,38,0.38)' }}>
        {ready ? 'ready' : optional ? 'optional' : 'needed'}
      </span>
    </div>
  )
}

export function OnboardingFlow({ userId, initialStatus, capabilities, onFinished }: Props) {
  const [step, setStep] = useState(0)
  const [status, setStatus] = useState(initialStatus)
  const [connections, setConnections] = useState<ConnectionsPayload>({ connections: [], subscriptions: [] })
  const [displayName, setDisplayName] = useState(initialStatus.display_name)
  const [provider, setProvider] = useState('google')
  const [keyInput, setKeyInput] = useState('')
  const [exaKey, setExaKey] = useState('')
  const [googleClientId, setGoogleClientId] = useState('')
  const [googleClientSecret, setGoogleClientSecret] = useState('')
  const [showKeyForm, setShowKeyForm] = useState(false)
  const [showExaForm, setShowExaForm] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const pollRef = useRef<number | null>(null)
  const googlePollRef = useRef<number | null>(null)
  const localPollRef = useRef<number | null>(null)
  const isMobile = useIsMobile()

  const refreshSetup = async () => {
    const [nextStatus, nextConnections] = await Promise.all([
      apiFetch(apiUrl('/onboarding', { user_id: userId })).then(response => readJson<OnboardingStatus>(response)),
      apiFetch('/connections').then(response => readJson<ConnectionsPayload>(response)),
    ])
    setStatus(nextStatus)
    setConnections(nextConnections)
    return nextStatus
  }

  const mergeLocalStatus = (localModel: LocalModelStatus) => {
    setStatus(current => ({
      ...current,
      readiness: {
        ...current.readiness,
        model_ready: current.readiness.model_ready || localModel.ready,
        local_model_ready: localModel.ready,
        local_model: localModel,
      },
    }))
  }

  const beginLocalPolling = () => {
    if (localPollRef.current !== null) window.clearInterval(localPollRef.current)
    localPollRef.current = window.setInterval(async () => {
      try {
        const localModel = await apiFetch('/local-model/status').then(response => readJson<LocalModelStatus>(response))
        mergeLocalStatus(localModel)
        if (localModel.ready || localModel.install.state === 'error') {
          if (localPollRef.current !== null) window.clearInterval(localPollRef.current)
          localPollRef.current = null
          setBusy(null)
          if (localModel.install.error) setError(localModel.install.error)
        }
      } catch {
        // A large model pull survives a brief frontend/backend refresh.
      }
    }, 1500)
  }

  useEffect(() => {
    void refreshSetup()
      .then(next => {
        if (next.readiness.local_model?.install.state === 'running') {
          setBusy('local')
          beginLocalPolling()
        }
      })
      .catch(() => setError('Narad is running, but setup status could not be refreshed.'))
    return () => {
      if (pollRef.current !== null) window.clearInterval(pollRef.current)
      if (googlePollRef.current !== null) window.clearInterval(googlePollRef.current)
      if (localPollRef.current !== null) window.clearInterval(localPollRef.current)
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const saveState = async (payload: Record<string, unknown>) => {
    const response = await apiFetch('/onboarding', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_id: userId, ...payload }),
    })
    const next = await readJson<OnboardingStatus>(response)
    setStatus(next)
    return next
  }

  const continueFromWelcome = async () => {
    setBusy('profile')
    setError(null)
    try {
      await saveState({ display_name: displayName })
      setStep(1)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Your setup could not be saved.')
    } finally {
      setBusy(null)
    }
  }

  const connectKey = async (selectedProvider: string, key: string) => {
    if (!key.trim()) return
    setBusy(`key:${selectedProvider}`)
    setError(null)
    try {
      const response = await apiFetch('/connections', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ provider: selectedProvider, key: key.trim() }),
      })
      const result = await readJson<{ ok: boolean; detail?: string }>(response)
      if (!result.ok) throw new Error(result.detail || 'That key did not validate.')
      if (selectedProvider === 'exa') {
        setExaKey('')
        setShowExaForm(false)
      } else {
        setKeyInput('')
        setShowKeyForm(false)
      }
      await refreshSetup()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'The connection could not be completed.')
    } finally {
      setBusy(null)
    }
  }

  const connectGrok = async () => {
    setBusy('grok')
    setError(null)
    try {
      const response = await apiFetch('/connections/xai/oauth/start', { method: 'POST' })
      const result = await readJson<{ authorize_url?: string }>(response)
      if (!result.authorize_url) throw new Error('Grok sign-in did not return an authorization page.')
      window.open(result.authorize_url, '_blank', 'noopener,noreferrer')
      let attempts = 0
      if (pollRef.current !== null) window.clearInterval(pollRef.current)
      pollRef.current = window.setInterval(async () => {
        attempts += 1
        try {
          const oauth = await apiFetch('/connections/xai/oauth/status').then(result => readJson<{ signed_in?: boolean }>(result))
          if (oauth.signed_in) {
            if (pollRef.current !== null) window.clearInterval(pollRef.current)
            pollRef.current = null
            await refreshSetup()
            setBusy(null)
          } else if (attempts >= 40) {
            if (pollRef.current !== null) window.clearInterval(pollRef.current)
            pollRef.current = null
            setBusy(null)
            setError('Sign-in is still pending. You can retry it or connect an API key instead.')
          }
        } catch {
          // Keep polling through transient loopback failures.
        }
      }, 3000)
    } catch (cause) {
      setBusy(null)
      setError(cause instanceof Error ? cause.message : 'Grok sign-in could not start.')
    }
  }

  const installOfflineModel = async () => {
    setBusy('local')
    setError(null)
    try {
      const response = await apiFetch('/local-model/install', { method: 'POST' })
      const localModel = await readJson<LocalModelStatus>(response)
      mergeLocalStatus(localModel)
      if (localModel.ready) {
        setBusy(null)
        return
      }
      beginLocalPolling()
    } catch (cause) {
      setBusy(null)
      setError(cause instanceof Error ? cause.message : 'The offline model could not be installed.')
    }
  }

  const connectGoogle = async () => {
    setBusy('google')
    setError(null)
    try {
      const response = await apiFetch('/connections/google/oauth/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ services: ['gmail', 'calendar', 'drive', 'photos'], access: 'read' }),
      })
      const result = await readJson<{ authorize_url?: string }>(response)
      if (!result.authorize_url) throw new Error('Google sign-in did not return an authorization page.')
      window.open(result.authorize_url, '_blank', 'noopener,noreferrer')
      if (googlePollRef.current !== null) window.clearInterval(googlePollRef.current)
      let attempts = 0
      googlePollRef.current = window.setInterval(async () => {
        attempts += 1
        try {
          const google = await apiFetch('/connections/google/oauth/status').then(value => readJson<{ connected?: boolean }>(value))
          if (google.connected) {
            if (googlePollRef.current !== null) window.clearInterval(googlePollRef.current)
            googlePollRef.current = null
            await refreshSetup()
            setBusy(null)
          } else if (attempts >= 60) {
            if (googlePollRef.current !== null) window.clearInterval(googlePollRef.current)
            googlePollRef.current = null
            setBusy(null)
            setError('Google sign-in is still pending. Finish consent in the opened tab, then retry.')
          }
        } catch {
          // Keep polling through a transient tunnel refresh.
        }
      }, 2000)
    } catch (cause) {
      setBusy(null)
      setError(cause instanceof Error ? cause.message : 'Google sign-in could not start.')
    }
  }

  const configureGoogle = async () => {
    setBusy('google-config')
    setError(null)
    try {
      const response = await apiFetch('/connections/google/oauth/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ client_id: googleClientId.trim(), client_secret: googleClientSecret.trim() }),
      })
      await readJson(response)
      setGoogleClientId('')
      setGoogleClientSecret('')
      await refreshSetup()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Google OAuth setup could not be saved.')
    } finally {
      setBusy(null)
    }
  }

  const grantInteractionTarget = async (kind: 'artemis' | 'cua', externalId: string, label: string) => {
    setBusy(`grant:${kind}:${externalId}`)
    setError(null)
    try {
      const response = await apiFetch('/interaction-targets', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ kind, external_id: externalId, label }),
      })
      await readJson(response)
      await refreshSetup()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'That device could not be granted.')
    } finally {
      setBusy(null)
    }
  }

  const finish = async (destination: 'chat' | 'workspaces', skipped = false) => {
    setBusy(`finish:${destination}`)
    setError(null)
    try {
      const next = await saveState({ display_name: displayName, completed: true, skipped })
      onFinished(next, destination)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Setup could not be completed.')
      setBusy(null)
    }
  }

  const brainReady = status.readiness.model_ready
  const localModel = status.readiness.local_model
  const researchReady = status.readiness.research_ready
  const modelConnections = [
    ...status.readiness.connected_subscriptions,
    ...status.readiness.connected_model_providers,
    ...(status.readiness.local_model_ready ? ['local'] : []),
  ]
  const providerOptions = connections.connections.filter(item => MODEL_PROVIDERS.has(item.provider))
  const googleWorkspace = status.readiness.google_workspace
  const phoneRuntime = status.readiness.phone
  const desktopRuntime = status.readiness.desktop
  const grants = status.readiness.interaction_grants || []
  const desktopGranted = grants.some(item => item.kind === 'cua' && item.external_id === 'host-primary')
  const androidDevices = phoneRuntime?.devices || []

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Set up Narad"
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 100,
        display: 'grid',
        placeItems: 'center',
        padding: isMobile ? 0 : 24,
        background: 'radial-gradient(circle at 12% 10%, rgba(180,83,9,0.17), transparent 31%), radial-gradient(circle at 88% 88%, rgba(53,94,59,0.14), transparent 35%), rgba(31,29,26,0.78)',
        backdropFilter: 'blur(12px)',
      }}
    >
      <div
        style={{
          width: 'min(920px, 100%)',
          height: isMobile ? '100%' : 'min(650px, calc(100vh - 48px))',
          minHeight: isMobile ? 0 : 560,
          display: 'grid',
          gridTemplateColumns: isMobile ? '1fr' : 'minmax(250px, 0.72fr) minmax(0, 1.55fr)',
          overflow: 'hidden',
          borderRadius: isMobile ? 0 : 22,
          border: isMobile ? 0 : '1px solid rgba(252,250,242,0.2)',
          background: 'var(--paper)',
          boxShadow: '0 28px 90px rgba(0,0,0,0.32)',
        }}
      >
        {!isMobile && (
          <aside style={{ position: 'relative', overflow: 'hidden', padding: '28px 25px', color: 'var(--paper)', background: 'linear-gradient(155deg, #282521 0%, #1d2b24 100%)' }}>
            <div style={{ position: 'absolute', inset: 0, opacity: 0.2, backgroundImage: 'repeating-linear-gradient(135deg, transparent 0 15px, rgba(252,250,242,0.08) 15px 16px)' }} />
            <div style={{ position: 'relative', height: '100%', display: 'flex', flexDirection: 'column' }}>
              <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
                <MahatiLogo size={37} />
                <div>
                  <div style={{ fontFamily: 'var(--font-hero)', fontSize: 19, fontWeight: 750 }}>NARAD.OS</div>
                  <div style={{ marginTop: 1, fontFamily: 'var(--font-mono)', fontSize: 8.5, letterSpacing: '0.14em', textTransform: 'uppercase', color: 'rgba(252,250,242,0.5)' }}>Local-first intelligence</div>
                </div>
              </div>
              <div style={{ marginTop: 'auto', paddingBottom: 12 }}>
                <div style={{ fontFamily: 'var(--font-deva)', color: 'var(--haldi)', fontSize: 17 }}>प्रथम</div>
                <div style={{ marginTop: 7, fontFamily: 'var(--font-hero)', fontSize: 27, lineHeight: 1.06 }}>Useful from the first conversation.</div>
                  <p style={{ margin: '12px 0 0', fontSize: 11.5, lineHeight: 1.65, color: 'rgba(252,250,242,0.58)' }}>Four short steps. Each family profile keeps its own memory, Google consent, and device grants.</p>
              </div>
              <div style={{ display: 'grid', gap: 8 }}>
                {['Your data stays under ~/.narad', 'External actions ask first', 'Connections can be removed anytime'].map(item => (
                  <div key={item} style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 10.5, color: 'rgba(252,250,242,0.65)' }}><ShieldCheck size={12} style={{ color: 'var(--haldi)' }} /> {item}</div>
                ))}
              </div>
            </div>
          </aside>
        )}

        <section style={{ minWidth: 0, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
          <header style={{ flexShrink: 0, padding: isMobile ? '16px 17px 12px' : '20px 25px 14px', borderBottom: '1px solid rgba(45,42,38,0.08)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 14, alignItems: 'center' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                {isMobile && <MahatiLogo size={30} />}
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9, letterSpacing: '0.13em', textTransform: 'uppercase', color: 'rgba(45,42,38,0.43)' }}>Setup {step + 1} of 4</span>
              </div>
              <button type="button" onClick={() => void finish('chat', true)} disabled={busy !== null} title="Skip setup for now" style={{ border: 0, background: 'transparent', color: 'rgba(45,42,38,0.42)', display: 'flex', alignItems: 'center', gap: 5, fontSize: 10.5, cursor: 'pointer' }}>
                Skip for now <X size={13} />
              </button>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 5, marginTop: 13 }}>
              {[0, 1, 2, 3].map(index => <span key={index} style={{ height: 3, borderRadius: 99, background: index <= step ? 'var(--sindoor)' : 'rgba(45,42,38,0.09)', transition: 'background 180ms ease' }} />)}
            </div>
          </header>

          <div className="panel-scroll" style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: isMobile ? '24px 18px 28px' : '31px 34px 34px' }}>
            {step === 0 && (
              <div style={{ maxWidth: 500 }}>
                <div style={{ width: 42, height: 42, borderRadius: 13, display: 'grid', placeItems: 'center', background: 'rgba(194,65,12,0.09)', color: 'var(--sindoor)' }}><Sparkles size={20} /></div>
                <h1 style={{ margin: '17px 0 0', fontFamily: 'var(--font-hero)', fontSize: isMobile ? 27 : 31, lineHeight: 1.08, color: 'var(--kajal)' }}>A quiet setup, then Narad gets out of your way.</h1>
                <p style={{ margin: '11px 0 0', maxWidth: 470, fontSize: 12.5, lineHeight: 1.65, color: 'rgba(45,42,38,0.58)' }}>Narad coordinates research, planning, creation, and computer work. Tell it what to call you; folders and documents can be attached directly when they matter.</p>
                <label style={{ display: 'grid', gap: 7, marginTop: 26 }}>
                  <span style={{ fontSize: 10.5, fontWeight: 750, color: 'rgba(45,42,38,0.62)' }}>What should Narad call you? <span style={{ fontWeight: 500, color: 'rgba(45,42,38,0.38)' }}>(optional)</span></span>
                  <input autoFocus value={displayName} onChange={event => setDisplayName(event.target.value)} onKeyDown={event => { if (event.key === 'Enter') void continueFromWelcome() }} placeholder="Your name" maxLength={80} style={{ width: '100%', padding: '12px 13px', borderRadius: 10, border: '1px solid rgba(45,42,38,0.15)', background: 'rgba(255,255,255,0.58)', color: 'var(--kajal)', fontSize: 13, outline: 'none' }} />
                </label>
                <div style={{ marginTop: 16, padding: '11px 12px', display: 'flex', gap: 9, borderRadius: 10, background: 'rgba(53,94,59,0.065)', color: 'rgba(45,42,38,0.58)', fontSize: 10.5, lineHeight: 1.45 }}><LockKeyhole size={14} style={{ flex: '0 0 auto', color: 'var(--tulsi)', marginTop: 1 }} /> Narad stores setup, memory, and uploaded files locally. Only requests to services you connect leave this machine.</div>
                <button type="button" onClick={() => void continueFromWelcome()} disabled={busy !== null} style={{ marginTop: 24, border: 0, borderRadius: 10, background: 'var(--kajal)', color: 'var(--paper)', padding: '11px 16px', display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, fontWeight: 750, cursor: busy ? 'wait' : 'pointer' }}>
                  {busy === 'profile' ? <LoaderCircle size={14} className="animate-spin" /> : <ArrowRight size={14} />} Continue
                </button>
              </div>
            )}

            {step === 1 && (
              <div style={{ maxWidth: 520 }}>
                <div style={{ width: 42, height: 42, borderRadius: 13, display: 'grid', placeItems: 'center', background: brainReady ? 'rgba(53,94,59,0.1)' : 'rgba(194,65,12,0.09)', color: brainReady ? 'var(--tulsi)' : 'var(--sindoor)' }}>{brainReady ? <CheckCircle2 size={20} /> : <Download size={20} />}</div>
                <h1 style={{ margin: '17px 0 0', fontFamily: 'var(--font-hero)', fontSize: isMobile ? 27 : 31, lineHeight: 1.08, color: 'var(--kajal)' }}>{localModel?.ready ? 'Your private brain is ready.' : brainReady ? 'Your brain connection is ready.' : 'Start private and offline.'}</h1>
                <p style={{ margin: '10px 0 0', fontSize: 12.5, lineHeight: 1.6, color: 'rgba(45,42,38,0.56)' }}>{localModel?.ready ? 'Gemma 4 runs on this machine with no key, subscription, or data leaving Narad.' : brainReady ? `Narad can use ${modelConnections.map(providerLabel).join(', ')}. Add the offline model below whenever you want a local fallback.` : 'Download Narad\'s local Gemma 4 brain once. Cloud connections remain optional accelerators.'}</p>

                <div style={{ marginTop: 20, padding: '13px 14px', borderRadius: 13, border: `1px solid ${localModel?.ready ? 'rgba(53,94,59,0.25)' : 'rgba(45,42,38,0.13)'}`, background: localModel?.ready ? 'rgba(53,94,59,0.065)' : 'linear-gradient(135deg, rgba(45,42,38,0.04), rgba(194,65,12,0.045))' }}>
                  <div style={{ display: 'grid', gridTemplateColumns: '36px minmax(0,1fr) auto', gap: 11, alignItems: 'center' }}>
                    <span style={{ width: 35, height: 35, display: 'grid', placeItems: 'center', borderRadius: 10, background: localModel?.ready ? 'rgba(53,94,59,0.12)' : 'rgba(194,65,12,0.09)', color: localModel?.ready ? 'var(--tulsi)' : 'var(--sindoor)' }}>{localModel?.ready ? <Check size={17} /> : busy === 'local' ? <LoaderCircle size={17} className="animate-spin" /> : <Download size={17} />}</span>
                    <span><span style={{ display: 'block', fontSize: 12, fontWeight: 780, color: 'var(--kajal)' }}>Gemma 4 {localModel?.model_size || 'E2B'} · Q4</span><span style={{ display: 'block', marginTop: 2, fontSize: 9.5, color: 'rgba(45,42,38,0.5)' }}>{localModel?.ready ? `Offline · ${Math.round((localModel.configured_context_tokens || 16384) / 1024)}K working context · tools + vision` : `${localModel?.download_gb || 7.2} GB package · selected for ${Math.round(localModel?.ram_gb || 8)} GB RAM`}</span></span>
                    {localModel?.ready ? <span style={{ fontFamily: 'var(--font-mono)', fontSize: 8.5, color: 'var(--tulsi)', textTransform: 'uppercase' }}>ready</span> : localModel?.runtime_installed ? <button type="button" onClick={() => void installOfflineModel()} disabled={busy !== null || localModel.install.state === 'running'} style={{ border: 0, borderRadius: 8, padding: '8px 11px', background: 'var(--tulsi)', color: '#fff', fontSize: 9.5, fontWeight: 750, cursor: busy || localModel.install.state === 'running' ? 'wait' : 'pointer' }}>{busy === 'local' || localModel.install.state === 'running' ? `${Math.round((localModel.install.progress || 0) * 100)}%` : 'Download'}</button> : <a href="https://ollama.com/download" target="_blank" rel="noreferrer" style={{ borderRadius: 8, padding: '8px 10px', background: 'var(--kajal)', color: '#fff', fontSize: 9.5, fontWeight: 750, textDecoration: 'none' }}>Get runtime</a>}
                  </div>
                  {localModel?.install.state === 'running' && <div style={{ marginTop: 10, height: 4, overflow: 'hidden', borderRadius: 99, background: 'rgba(45,42,38,0.09)' }}><div style={{ width: `${Math.max(2, (localModel.install.progress || 0) * 100)}%`, height: '100%', borderRadius: 99, background: 'var(--tulsi)', transition: 'width 250ms ease' }} /></div>}
                  <div style={{ marginTop: 8, fontSize: 9.5, lineHeight: 1.4, color: 'rgba(45,42,38,0.46)' }}>{localModel?.install.state === 'running' ? localModel.install.status : localModel?.memory_constrained ? 'E2B loads after your first local request, then releases after use to keep the browser responsive.' : 'Narad detected at least 16 GB RAM and automatically selected the stronger E4B model.'}</div>
                </div>

                {!brainReady && (
                  <div style={{ marginTop: 16, display: 'grid', gap: 10 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 9, fontFamily: 'var(--font-mono)', fontSize: 8.5, letterSpacing: '0.09em', textTransform: 'uppercase', color: 'rgba(45,42,38,0.36)' }}><span style={{ flex: 1, height: 1, background: 'rgba(45,42,38,0.09)' }} /> or connect a cloud boost <span style={{ flex: 1, height: 1, background: 'rgba(45,42,38,0.09)' }} /></div>
                    <button type="button" onClick={() => void connectGrok()} disabled={busy !== null} style={{ display: 'grid', gridTemplateColumns: '36px minmax(0,1fr) auto', gap: 11, alignItems: 'center', padding: '12px 13px', borderRadius: 12, border: '1px solid rgba(45,42,38,0.13)', background: 'var(--kajal)', color: 'var(--paper)', textAlign: 'left', cursor: busy ? 'wait' : 'pointer' }}>
                      <span style={{ width: 35, height: 35, display: 'grid', placeItems: 'center', borderRadius: 10, background: 'rgba(252,250,242,0.1)' }}>{busy === 'grok' ? <LoaderCircle size={17} className="animate-spin" /> : <Sparkles size={17} />}</span>
                      <span><span style={{ display: 'block', fontSize: 12, fontWeight: 750 }}>Sign in with Grok</span><span style={{ display: 'block', marginTop: 2, fontSize: 9.5, color: 'rgba(252,250,242,0.55)' }}>Optional when you want a larger hosted model</span></span>
                      <ExternalLink size={14} />
                    </button>

                    <button type="button" onClick={() => setShowKeyForm(value => !value)} style={{ display: 'grid', gridTemplateColumns: '36px minmax(0,1fr) auto', gap: 11, alignItems: 'center', padding: '12px 13px', borderRadius: 12, border: '1px solid rgba(45,42,38,0.12)', background: 'rgba(255,255,255,0.55)', color: 'var(--kajal)', textAlign: 'left', cursor: 'pointer' }}>
                      <span style={{ width: 35, height: 35, display: 'grid', placeItems: 'center', borderRadius: 10, background: 'rgba(194,65,12,0.08)', color: 'var(--sindoor)' }}><KeyRound size={17} /></span>
                      <span><span style={{ display: 'block', fontSize: 12, fontWeight: 750 }}>Connect an API key</span><span style={{ display: 'block', marginTop: 2, fontSize: 9.5, color: 'rgba(45,42,38,0.48)' }}>Gemini, DeepSeek, OpenAI, or Claude</span></span>
                      <ArrowRight size={14} style={{ transform: showKeyForm ? 'rotate(90deg)' : undefined }} />
                    </button>

                    {showKeyForm && (
                      <div style={{ display: 'grid', gap: 8, padding: 12, borderRadius: 12, border: '1px solid rgba(45,42,38,0.1)', background: 'rgba(45,42,38,0.025)' }}>
                        <div style={{ display: 'grid', gridTemplateColumns: isMobile ? '1fr' : '140px minmax(0,1fr)', gap: 8 }}>
                          <select value={provider} onChange={event => setProvider(event.target.value)} aria-label="Model provider" style={{ padding: '9px 10px', borderRadius: 8, border: '1px solid rgba(45,42,38,0.14)', background: 'var(--paper)', color: 'var(--kajal)', fontSize: 11.5 }}>
                            {providerOptions.map(item => <option key={item.provider} value={item.provider}>{item.label}</option>)}
                          </select>
                          <input type="password" value={keyInput} onChange={event => setKeyInput(event.target.value)} onKeyDown={event => { if (event.key === 'Enter') void connectKey(provider, keyInput) }} aria-label="Provider API key" placeholder="Paste the API key" style={{ padding: '9px 10px', borderRadius: 8, border: '1px solid rgba(45,42,38,0.14)', background: 'var(--paper)', color: 'var(--kajal)', fontFamily: 'var(--font-mono)', fontSize: 11 }} />
                        </div>
                        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, alignItems: 'center' }}>
                          <a href={providerOptions.find(item => item.provider === provider)?.key_page} target="_blank" rel="noreferrer" style={{ fontSize: 9.5, color: 'var(--nila)', textDecoration: 'underline' }}>Get a key <ExternalLink size={9} style={{ display: 'inline' }} /></a>
                          <button type="button" onClick={() => void connectKey(provider, keyInput)} disabled={!keyInput.trim() || busy !== null} style={{ border: 0, borderRadius: 8, padding: '8px 12px', background: keyInput.trim() ? 'var(--sindoor)' : 'rgba(45,42,38,0.12)', color: keyInput.trim() ? '#fff' : 'rgba(45,42,38,0.4)', fontSize: 10.5, fontWeight: 700, cursor: keyInput.trim() ? 'pointer' : 'default' }}>{busy?.startsWith('key:') ? 'Testing...' : 'Test and save'}</button>
                        </div>
                      </div>
                    )}
                  </div>
                )}

                <div style={{ marginTop: 23, display: 'flex', gap: 9, alignItems: 'center', flexWrap: 'wrap' }}>
                  <button type="button" onClick={() => setStep(2)} disabled={busy !== null || !brainReady} style={{ border: 0, borderRadius: 10, background: brainReady ? 'var(--tulsi)' : 'rgba(45,42,38,0.14)', color: brainReady ? '#fff' : 'rgba(45,42,38,0.42)', padding: '10px 14px', display: 'flex', alignItems: 'center', gap: 7, fontSize: 11.5, fontWeight: 750, cursor: busy || !brainReady ? 'default' : 'pointer' }}>{brainReady ? <Check size={14} /> : <Download size={14} />} {brainReady ? 'Brain ready' : 'Choose a model above'}</button>
                  <button type="button" onClick={() => setStep(0)} style={{ border: 0, background: 'transparent', color: 'rgba(45,42,38,0.48)', fontSize: 10.5, cursor: 'pointer' }}>Back</button>
                </div>
              </div>
            )}

            {step === 2 && (
              <div style={{ maxWidth: 550 }}>
                <div style={{ width: 42, height: 42, borderRadius: 13, display: 'grid', placeItems: 'center', background: 'rgba(36,92,130,0.09)', color: 'var(--nila)' }}><ShieldCheck size={20} /></div>
                <h1 style={{ margin: '17px 0 0', fontFamily: 'var(--font-hero)', fontSize: isMobile ? 27 : 31, lineHeight: 1.08, color: 'var(--kajal)' }}>Connect your world, not everyone else’s.</h1>
                <p style={{ margin: '10px 0 0', fontSize: 12.5, lineHeight: 1.6, color: 'rgba(45,42,38,0.56)' }}>These permissions belong only to this family profile. Narad previews consequential actions and Jev evaluates computer and phone runs.</p>

                <div style={{ display: 'grid', gap: 10, marginTop: 18 }}>
                  <div style={{ padding: '13px 14px', borderRadius: 13, border: `1px solid ${googleWorkspace?.connected ? 'rgba(53,94,59,0.25)' : 'rgba(45,42,38,0.12)'}`, background: 'rgba(255,255,255,0.5)' }}>
                    <div style={{ display: 'grid', gridTemplateColumns: '36px minmax(0,1fr) auto', gap: 11, alignItems: 'center' }}>
                      <span style={{ width: 35, height: 35, display: 'grid', placeItems: 'center', borderRadius: 10, background: 'rgba(194,65,12,0.08)', color: 'var(--sindoor)' }}><KeyRound size={17} /></span>
                      <span><span style={{ display: 'block', fontSize: 12, fontWeight: 760, color: 'var(--kajal)' }}>Google Workspace</span><span style={{ display: 'block', marginTop: 2, fontSize: 9.5, lineHeight: 1.35, color: 'rgba(45,42,38,0.5)' }}>Gmail, Calendar, Drive, and Photos with read-only consent first.</span></span>
                      {googleWorkspace?.connected ? <span style={{ fontFamily: 'var(--font-mono)', fontSize: 8.5, color: 'var(--tulsi)', textTransform: 'uppercase' }}>connected</span> : <button type="button" onClick={() => void connectGoogle()} disabled={!googleWorkspace?.configured || busy !== null} style={{ border: 0, borderRadius: 8, padding: '8px 10px', background: googleWorkspace?.configured ? 'var(--kajal)' : 'rgba(45,42,38,0.1)', color: googleWorkspace?.configured ? '#fff' : 'rgba(45,42,38,0.4)', fontSize: 9.5, fontWeight: 740, cursor: googleWorkspace?.configured ? 'pointer' : 'default' }}>{busy === 'google' ? 'Waiting...' : 'Connect'}</button>}
                    </div>
                    {!googleWorkspace?.configured && googleWorkspace?.can_configure && <div style={{ display: 'grid', gap: 7, marginTop: 9 }}>
                      <div style={{ fontSize: 9.5, lineHeight: 1.4, color: '#9a5b12' }}>Owner setup: create one Google OAuth web client with redirect <code>https://narad.chaipecharcha.ai/google/callback</code>, then save it here. The secret stays in the host keychain.</div>
                      <input value={googleClientId} onChange={event => setGoogleClientId(event.target.value)} placeholder="Google OAuth client ID" aria-label="Google OAuth client ID" style={{ width: '100%', padding: '8px 9px', borderRadius: 8, border: '1px solid rgba(45,42,38,0.13)', background: 'var(--paper)', fontSize: 10.5 }} />
                      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1fr) auto', gap: 7 }}>
                        <input type="password" value={googleClientSecret} onChange={event => setGoogleClientSecret(event.target.value)} placeholder="Google OAuth client secret" aria-label="Google OAuth client secret" style={{ minWidth: 0, padding: '8px 9px', borderRadius: 8, border: '1px solid rgba(45,42,38,0.13)', background: 'var(--paper)', fontSize: 10.5 }} />
                        <button type="button" onClick={() => void configureGoogle()} disabled={!googleClientId.trim() || !googleClientSecret.trim() || busy !== null} style={{ border: 0, borderRadius: 8, padding: '8px 10px', background: 'var(--sindoor)', color: '#fff', fontSize: 9.5, fontWeight: 740 }}>Save</button>
                      </div>
                    </div>}
                    {!googleWorkspace?.configured && !googleWorkspace?.can_configure && <div style={{ marginTop: 8, fontSize: 9.5, color: '#9a5b12' }}>Ask the family pilot owner to configure the shared Google OAuth app once. You will then connect your own Google account here.</div>}
                  </div>

                  <div style={{ padding: '13px 14px', borderRadius: 13, border: `1px solid ${desktopGranted ? 'rgba(53,94,59,0.25)' : 'rgba(45,42,38,0.12)'}`, background: 'rgba(255,255,255,0.5)' }}>
                    <div style={{ display: 'grid', gridTemplateColumns: '36px minmax(0,1fr) auto', gap: 11, alignItems: 'center' }}>
                      <span style={{ width: 35, height: 35, display: 'grid', placeItems: 'center', borderRadius: 10, background: 'rgba(36,92,130,0.08)', color: 'var(--nila)' }}><Monitor size={17} /></span>
                      <span><span style={{ display: 'block', fontSize: 12, fontWeight: 760, color: 'var(--kajal)' }}>This Mac via CUA</span><span style={{ display: 'block', marginTop: 2, fontSize: 9.5, lineHeight: 1.35, color: 'rgba(45,42,38,0.5)' }}>{desktopRuntime?.available ? desktopRuntime.reason || 'Cua Driver is ready for confirmed desktop tasks.' : desktopRuntime?.reason || 'Cua Driver is not ready.'}</span></span>
                      {desktopGranted ? <span style={{ fontFamily: 'var(--font-mono)', fontSize: 8.5, color: 'var(--tulsi)', textTransform: 'uppercase' }}>allowed</span> : <button type="button" onClick={() => void grantInteractionTarget('cua', 'host-primary', 'Narad host desktop')} disabled={!desktopRuntime?.available || busy !== null} style={{ border: 0, borderRadius: 8, padding: '8px 10px', background: desktopRuntime?.available ? 'var(--nila)' : 'rgba(45,42,38,0.1)', color: desktopRuntime?.available ? '#fff' : 'rgba(45,42,38,0.4)', fontSize: 9.5, fontWeight: 740 }}>Allow</button>}
                    </div>
                  </div>

                  <div style={{ padding: '13px 14px', borderRadius: 13, border: '1px solid rgba(45,42,38,0.12)', background: 'rgba(255,255,255,0.5)' }}>
                    <div style={{ display: 'flex', gap: 11, alignItems: 'center' }}>
                      <span style={{ width: 35, height: 35, display: 'grid', placeItems: 'center', borderRadius: 10, background: 'rgba(53,94,59,0.08)', color: 'var(--tulsi)' }}><Smartphone size={17} /></span>
                      <span style={{ minWidth: 0, flex: 1 }}><span style={{ display: 'block', fontSize: 12, fontWeight: 760, color: 'var(--kajal)' }}>Android phones via Artemis</span><span style={{ display: 'block', marginTop: 2, fontSize: 9.5, lineHeight: 1.35, color: 'rgba(45,42,38,0.5)' }}>{phoneRuntime?.ready ? `${androidDevices.length} connected device${androidDevices.length === 1 ? '' : 's'} detected.` : phoneRuntime?.reason || 'Artemis is starting locally.'}</span></span>
                    </div>
                    {androidDevices.map((device, index) => {
                      const externalId = String(device.serial ?? device.device_serial ?? device.id ?? '')
                      if (!externalId) return null
                      const label = String(device.model ?? device.name ?? externalId)
                      const granted = grants.some(item => item.kind === 'artemis' && item.external_id === externalId)
                      return <button key={`${externalId}:${index}`} type="button" onClick={() => void grantInteractionTarget('artemis', externalId, label)} disabled={granted || busy !== null} style={{ width: '100%', marginTop: 8, padding: '8px 10px', borderRadius: 9, border: '1px solid rgba(45,42,38,0.11)', background: granted ? 'rgba(53,94,59,0.06)' : 'transparent', color: granted ? 'var(--tulsi)' : 'rgba(45,42,38,0.7)', fontSize: 10.5, textAlign: 'left' }}>{granted ? `${label} allowed for this profile` : `Allow ${label} for this profile`}</button>
                    })}
                    {phoneRuntime?.ready && androidDevices.length === 0 && <div style={{ marginTop: 8, fontSize: 9.5, color: 'rgba(45,42,38,0.45)' }}>Enable Wireless debugging on the Android phone and pair it once with the Narad host. It will then appear here.</div>}
                  </div>
                </div>

                <div style={{ marginTop: 11, padding: '9px 11px', borderRadius: 10, background: status.readiness.jev?.available ? 'rgba(53,94,59,0.065)' : 'rgba(180,83,9,0.07)', fontSize: 10, lineHeight: 1.45, color: 'rgba(45,42,38,0.58)' }}><ShieldCheck size={12} style={{ verticalAlign: '-2px', marginRight: 6, color: status.readiness.jev?.available ? 'var(--tulsi)' : '#b45309' }} />Jev safety scorer: {status.readiness.jev?.available ? 'connected for computer and phone admission and verification.' : status.readiness.jev?.reason || 'not connected.'}</div>

                <div style={{ marginTop: 20, display: 'flex', gap: 9 }}>
                  <button type="button" onClick={() => setStep(3)} disabled={busy !== null} style={{ border: 0, borderRadius: 10, background: 'var(--kajal)', color: 'var(--paper)', padding: '10px 14px', display: 'flex', alignItems: 'center', gap: 7, fontSize: 11.5, fontWeight: 750 }}>Continue <ArrowRight size={14} /></button>
                  <button type="button" onClick={() => setStep(1)} style={{ border: 0, background: 'transparent', color: 'rgba(45,42,38,0.48)', fontSize: 10.5 }}>Back</button>
                </div>
              </div>
            )}

            {step === 3 && (
              <div style={{ maxWidth: 530 }}>
                <div style={{ width: 42, height: 42, borderRadius: 13, display: 'grid', placeItems: 'center', background: 'rgba(53,94,59,0.1)', color: 'var(--tulsi)' }}><CheckCircle2 size={21} /></div>
                <h1 style={{ margin: '17px 0 0', fontFamily: 'var(--font-hero)', fontSize: isMobile ? 27 : 31, lineHeight: 1.08, color: 'var(--kajal)' }}>{displayName.trim() ? `${displayName.trim()}, Narad is ready.` : 'Narad is ready.'}</h1>
                <p style={{ margin: '10px 0 0', fontSize: 12.5, lineHeight: 1.6, color: 'rgba(45,42,38,0.56)' }}>Start in open chat or choose a guided workflow with its own intake, progress, and feedback loop.</p>

                <div style={{ marginTop: 18, padding: '2px 13px', borderRadius: 12, border: '1px solid rgba(45,42,38,0.1)', background: 'rgba(255,255,255,0.48)' }}>
                  <StatusRow ready={Boolean(capabilities)} title="Narad runtime" detail={capabilities ? 'Backend and four-avatar harness are reachable.' : 'Runtime status is still being checked.'} />
                  <StatusRow ready={brainReady} title="Model connection" detail={brainReady ? modelConnections.map(providerLabel).join(', ') : 'Install the offline model or connect a provider.'} />
                  <StatusRow ready={researchReady} optional title="Enhanced web research" detail={researchReady ? 'Exa or a search provider is connected.' : 'Optional. URL reading and configured fallbacks remain available.'} />
                  <StatusRow ready title="Local workspace" detail="Memory, files, artifacts, and setup stay under ~/.narad." />
                </div>

                {!researchReady && (
                  <div style={{ marginTop: 11 }}>
                    <button type="button" onClick={() => setShowExaForm(value => !value)} style={{ border: 0, background: 'transparent', padding: 0, display: 'flex', gap: 6, alignItems: 'center', color: 'var(--nila)', fontSize: 10.5, cursor: 'pointer' }}><Search size={12} /> Add Exa research now <span style={{ color: 'rgba(45,42,38,0.4)' }}>(optional)</span></button>
                    {showExaForm && (
                      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1fr) auto', gap: 8, marginTop: 8 }}>
                        <input type="password" value={exaKey} onChange={event => setExaKey(event.target.value)} onKeyDown={event => { if (event.key === 'Enter') void connectKey('exa', exaKey) }} aria-label="Exa API key" placeholder="Paste Exa API key" style={{ padding: '9px 10px', borderRadius: 8, border: '1px solid rgba(45,42,38,0.14)', background: 'var(--paper)', color: 'var(--kajal)', fontFamily: 'var(--font-mono)', fontSize: 11 }} />
                        <button type="button" onClick={() => void connectKey('exa', exaKey)} disabled={!exaKey.trim() || busy !== null} style={{ border: 0, borderRadius: 8, padding: '8px 12px', background: exaKey.trim() ? 'var(--nila)' : 'rgba(45,42,38,0.12)', color: exaKey.trim() ? '#fff' : 'rgba(45,42,38,0.4)', fontSize: 10.5, fontWeight: 700 }}>Connect</button>
                      </div>
                    )}
                  </div>
                )}

                <div style={{ display: 'grid', gridTemplateColumns: isMobile ? '1fr' : 'repeat(2,minmax(0,1fr))', gap: 9, marginTop: 23 }}>
                  <button type="button" onClick={() => void finish('chat', !brainReady)} disabled={busy !== null} style={{ display: 'grid', gridTemplateColumns: '34px minmax(0,1fr) auto', gap: 9, alignItems: 'center', padding: '11px 12px', borderRadius: 11, border: 0, background: 'var(--kajal)', color: 'var(--paper)', textAlign: 'left', cursor: busy ? 'wait' : 'pointer' }}><MessageCircle size={17} /><span><span style={{ display: 'block', fontSize: 11.5, fontWeight: 750 }}>Start chatting</span><span style={{ display: 'block', marginTop: 1, fontSize: 9, color: 'rgba(252,250,242,0.5)' }}>Ask or attach anything</span></span><ArrowRight size={13} /></button>
                  <button type="button" onClick={() => void finish('workspaces', !brainReady)} disabled={busy !== null} style={{ display: 'grid', gridTemplateColumns: '34px minmax(0,1fr) auto', gap: 9, alignItems: 'center', padding: '11px 12px', borderRadius: 11, border: '1px solid rgba(45,42,38,0.14)', background: 'rgba(255,255,255,0.55)', color: 'var(--kajal)', textAlign: 'left', cursor: busy ? 'wait' : 'pointer' }}><Workflow size={17} style={{ color: 'var(--sindoor)' }} /><span><span style={{ display: 'block', fontSize: 11.5, fontWeight: 750 }}>Explore workflows</span><span style={{ display: 'block', marginTop: 1, fontSize: 9, color: 'rgba(45,42,38,0.48)' }}>Career, health, teach, more</span></span><ArrowRight size={13} /></button>
                </div>
                <button type="button" onClick={() => setStep(2)} style={{ marginTop: 14, border: 0, background: 'transparent', padding: 0, color: 'rgba(45,42,38,0.45)', fontSize: 10.5, cursor: 'pointer' }}>Back to Google and devices</button>
              </div>
            )}

            {error && <div role="alert" style={{ marginTop: 15, padding: '9px 11px', borderRadius: 9, border: '1px solid rgba(194,65,12,0.22)', background: 'rgba(194,65,12,0.06)', color: 'var(--sindoor)', fontSize: 10.5, lineHeight: 1.4 }}>{error}</div>}
          </div>
        </section>
      </div>
    </div>
  )
}
