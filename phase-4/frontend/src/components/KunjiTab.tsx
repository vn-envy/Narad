/**
 * KunjiTab (कुंजी) — key & subscription management (O5/S3).
 *
 * Paste a key → provider auto-detected from its prefix → live-tested →
 * stored in the OS keychain. Never shows a stored key again (masked hint
 * only). One card per provider: status, backend, month-to-date spend,
 * test, disconnect. Subscriptions (Claude Agent SDK plan credits) are
 * shown honestly: installed / signed-in / available.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { apiFetch } from '@/lib/api'

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
}

interface ConnectionsPayload {
  connections?: Connection[]
  subscriptions?: Subscription[]
}

// Mirror of kunji.detect_provider_from_key — instant feedback while typing.
const PREFIX_HINTS: Array<[string, string]> = [
  ['sk-ant-', 'anthropic'],
  ['AIza', 'google'],
  ['dsk-', 'deepseek'],
  ['sk-', 'openai'],
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

export function KunjiTab() {
  const [connections, setConnections] = useState<Connection[]>([])
  const [subscriptions, setSubscriptions] = useState<Subscription[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [keyInput, setKeyInput] = useState('')
  const [providerOverride, setProviderOverride] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [testResults, setTestResults] = useState<Record<string, { ok: boolean; detail: string }>>({})

  const load = useCallback(async () => {
    try {
      const response = await apiFetch('/connections')
      if (!response.ok) throw new Error(`${response.status}`)
      const data: ConnectionsPayload = await response.json()
      setConnections(data.connections ?? [])
      setSubscriptions(data.subscriptions ?? [])
      setLoadError(null)
    } catch {
      setLoadError('Could not reach /connections — is the server running?')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

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
                  <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
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
                  </div>
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
        {subscriptions.map(sub => (
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
                  background: sub.available ? 'var(--tulsi)' : sub.installed ? 'var(--haldi)' : `${INK}0.20)`,
                }}
              />
              <span style={{ fontSize: 13, fontWeight: 600, color: `${INK}0.85)` }}>{sub.label}</span>
              {sub.plan && (
                <span style={{ fontSize: 9, padding: '2px 8px', borderRadius: 999, background: `${INK}0.07)`, color: `${INK}0.55)`, textTransform: 'uppercase', letterSpacing: '0.08em' }}>
                  {sub.plan}
                </span>
              )}
            </div>
            <div style={{ fontSize: 11, color: `${INK}0.55)`, marginTop: 8 }}>{sub.detail}</div>
            <div style={{ display: 'flex', gap: 12, marginTop: 8, fontSize: 10, color: `${INK}0.45)` }}>
              <span>{sub.installed ? '✓ SDK installed' : '· SDK not installed'}</span>
              <span>{sub.signed_in ? '✓ signed in' : '· not signed in'}</span>
            </div>
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
