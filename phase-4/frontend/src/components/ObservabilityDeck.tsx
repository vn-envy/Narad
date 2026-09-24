import type { AvatarName, AvatarStatus, SessionInfo, StepEvent } from '../hooks/useAvatara'
import type { RuntimeCapabilities } from '@/lib/api'
import { AVATAR_DISCIPLINES, AVATAR_NAMES, DEVA } from '@/lib/avatara-constants'

interface Props {
  open: boolean
  avatars: Record<AvatarName, AvatarStatus>
  currentSession: SessionInfo | null
  stepEvents: StepEvent[]
  sessionTotals: { promptTokens: number; completionTokens: number; totalTokens: number; costUsd: number }
  capabilities: RuntimeCapabilities | null
  compact?: boolean
  metricsOnly?: boolean
}

const INK = 'rgba(var(--rgb-ink),'

function compact(value: number) {
  return value >= 1000 ? `${(value / 1000).toFixed(1)}k` : String(value)
}

function Metric({ label, value, detail }: { label: string; value: string; detail: string }) {
  return (
    <div style={{ padding: '14px 15px', borderRadius: 14, border: `1px solid ${INK}0.08)`, background: 'rgba(var(--rgb-page),0.82)' }}>
      <div style={{ fontSize: 9.5, letterSpacing: '0.15em', textTransform: 'uppercase', color: `${INK}0.42)` }}>{label}</div>
      <div style={{ marginTop: 5, fontFamily: 'var(--font-hero)', fontSize: 23, color: 'var(--kajal)' }}>{value}</div>
      <div style={{ marginTop: 3, fontSize: 10.5, color: `${INK}0.48)` }}>{detail}</div>
    </div>
  )
}

export function ObservabilityDeck({
  open, avatars, currentSession, stepEvents, sessionTotals, capabilities,
}: Props) {
  if (!open) return null
  const toolCalls = stepEvents.filter(event => event.kind === 'tool_call').length
  const activeAgents = AVATAR_NAMES.filter(name => avatars[name]?.state === 'active').length
  const readyTools = capabilities
    ? Object.values(capabilities.tool_families).filter(tool => tool.available).length
    : 0
  const totalTools = capabilities ? Object.keys(capabilities.tool_families).length : 0

  return (
    <div className="panel-scroll" style={{ height: '100%', overflow: 'auto', padding: '20px 24px 34px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 16, flexWrap: 'wrap' }}>
        <div>
          <div style={{ fontSize: 10, letterSpacing: '0.17em', textTransform: 'uppercase', color: `${INK}0.42)` }}>Runtime</div>
          <h2 style={{ marginTop: 5, fontFamily: 'var(--font-hero)', fontSize: 22, color: 'var(--kajal)' }}>Narad at a glance</h2>
        </div>
        <div style={{ padding: '6px 10px', borderRadius: 999, background: capabilities?.status === 'healthy' ? 'rgba(53,94,59,0.10)' : 'rgba(194,65,12,0.09)', color: capabilities?.status === 'healthy' ? 'var(--tulsi)' : 'var(--sindoor)', fontSize: 10.5, fontWeight: 700 }}>
          {capabilities ? `${capabilities.status} · ${capabilities.build.runtime_mode}` : 'connecting'}
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 10, marginTop: 16 }}>
        <Metric label="Session tokens" value={compact(sessionTotals.totalTokens)} detail={`${compact(sessionTotals.promptTokens)} in · ${compact(sessionTotals.completionTokens)} out`} />
        <Metric label="Throughput" value={currentSession?.tokPerSec ? `${currentSession.tokPerSec.toFixed(1)}/s` : '—'} detail="Latest completed synthesis" />
        <Metric label="Agents active" value={String(activeAgents)} detail={`${AVATAR_NAMES.length} specialists available`} />
        <Metric label="Tools ready" value={capabilities ? `${readyTools}/${totalTools}` : '—'} detail={`${toolCalls} calls this session`} />
      </div>

      <div style={{ marginTop: 20, padding: '15px 16px', borderRadius: 15, border: `1px solid ${INK}0.08)`, background: 'linear-gradient(120deg, rgba(53,94,59,0.055), rgba(var(--rgb-page),0.8))' }}>
        <div style={{ fontSize: 10, letterSpacing: '0.15em', textTransform: 'uppercase', color: `${INK}0.42)` }}>Four-agent runtime</div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 8, marginTop: 10 }}>
          {AVATAR_NAMES.map(name => {
            const agent = avatars[name]
            return (
              <div key={name} style={{ display: 'flex', gap: 9, alignItems: 'center', padding: '8px 9px', borderRadius: 10, background: `${INK}0.035)` }}>
                <span style={{ fontFamily: 'var(--font-deva)', color: agent?.state === 'active' ? 'var(--sindoor)' : `${INK}0.6)` }}>{DEVA[name]}</span>
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: 11, fontWeight: 700, color: `${INK}0.78)` }}>{name}</div>
                  <div style={{ fontSize: 9.5, color: `${INK}0.43)`, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{agent?.task || AVATAR_DISCIPLINES[name]}</div>
                </div>
              </div>
            )
          })}
        </div>
      </div>

      {capabilities?.issues.length ? (
        <div style={{ marginTop: 16, fontSize: 10.5, color: 'var(--sindoor)' }}>
          {capabilities.issues.map(issue => issue.message).join(' · ')}
        </div>
      ) : null}
    </div>
  )
}
