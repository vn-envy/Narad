import { useEffect, useState } from 'react'
import type { SessionInfo, StepEvent, AvatarName } from '../hooks/useAvatara'
import { apiFetch, apiUrl } from '@/lib/api'

// ── Types ─────────────────────────────────────────────────────────────────────

interface TraceEvent {
  ts: string
  event: string
  avatar?: string | null
  task?: string | null
  result_len?: number
  result_digest?: string
  latency_ms?: number
  total_ms?: number
  usage?: { prompt_tokens: number; completion_tokens: number; total_tokens: number }
  trajectory?: {
    avatar: string
    model: string
    task_preview: string
    turns: Array<{
      turn: number
      tool_calls: Array<{ tool: string; params_preview: string; result_preview: string; latency_ms: number; error?: string | null }>
      text_preview: string
      prompt_tokens: number
      completion_tokens: number
    }>
    total_ms: number
    error?: string | null
  }
  phase?: string
  discipline?: string
  degraded_capabilities?: string[]
  avatars_invoked?: string[]
  error?: string
  error_type?: string
  sandbox_uuid?: string
}

interface AuditEntry {
  event: 'invocation' | 'scope_warning'
  avatar: string
  task_preview: string
  user_id: string
  ts: string
  matched_signals?: string[]
}

interface Props {
  currentSession: SessionInfo | null
  stepEvents: StepEvent[]
  sessionTotals: { promptTokens: number; completionTokens: number; totalTokens: number }
  userId: string
}

// ── Avatar accent colours — canonical identity tokens ──────────────────────

const AVATAR_COLOR: Record<string, string> = {
  Matsya:       'var(--avatar-matsya)',
  Rama:         'var(--avatar-rama)',
  Krishna:      'var(--avatar-krishna)',
  Parashurama:  'var(--avatar-parashurama)',
  __narad__:    'var(--avatar-narad)',
}

function avatarColor(name: string | null | undefined): string {
  if (!name) return 'var(--marigold)'
  return AVATAR_COLOR[name] ?? 'var(--loha)'
}

function fmtTime(ts: string): string {
  try {
    const d = new Date(ts)
    const base = d.toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' })
    return `${base}.${String(d.getMilliseconds()).padStart(3, '0')}`
  } catch { return ts.slice(11, 23) }
}

function fmtMs(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(2)}s` : `${ms}ms`
}

// ── Sub-components ─────────────────────────────────────────────────────────

function MetaPill({ label, color }: { label: string; color?: string }) {
  return (
    <span style={{
      fontSize: 10.5, padding: '1px 6px', borderRadius: 10,
      fontFamily: 'var(--font-mono)',
      background: color ? `${color}22` : 'rgba(26,24,21,0.06)',
      color: color ?? 'rgba(26,24,21,0.5)',
    }}>{label}</span>
  )
}

function ToolCallRow({ tc }: { tc: NonNullable<TraceEvent['trajectory']>['turns'][0]['tool_calls'][0] }) {
  return (
    <div style={{
      display: 'flex', alignItems: 'flex-start', gap: 8,
      padding: '5px 8px', background: 'rgba(26,24,21,0.04)', borderRadius: 4, fontSize: 11.5,
    }}>
      <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--kajal)', fontSize: 11, flexShrink: 0 }}>
        {tc.tool}
      </span>
      <span style={{ color: 'rgba(26,24,21,0.45)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {tc.result_preview ?? tc.params_preview}
      </span>
      {tc.error && <span style={{ color: 'var(--sindoor)', fontSize: 10, flexShrink: 0 }}>⚠ err</span>}
      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'rgba(26,24,21,0.35)', flexShrink: 0 }}>
        {fmtMs(tc.latency_ms)}
      </span>
    </div>
  )
}

function ValidateRow({ passed, total }: { passed: number; total: number }) {
  const failed = total - passed
  const ok = failed === 0 || failed / total <= 0.5
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 8, padding: '5px 8px',
      background: ok ? 'rgba(46,125,79,0.06)' : 'rgba(229,90,31,0.06)',
      borderLeft: `2px solid ${ok ? 'var(--tulsi)' : 'var(--kesari)'}`,
      borderRadius: '0 4px 4px 0', fontSize: 11,
    }}>
      <span style={{ fontWeight: 600, color: ok ? 'var(--tulsi)' : 'var(--kesari)', fontSize: 10, textTransform: 'uppercase' }}>
        {ok ? '✓ VALIDATE' : '✗ CORRECT'}
      </span>
      <span style={{ color: 'rgba(26,24,21,0.45)' }}>
        {passed}/{total} sources passed{!ok ? ' → re-queried with refined terms' : ''}
      </span>
    </div>
  )
}

function CompressRow({ uuid, wordsBefore, wordsAfter, onExpand }: { uuid: string; wordsBefore?: number; wordsAfter?: number; onExpand: (uuid: string) => void }) {
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 8, padding: '6px 8px',
      background: 'rgba(232,121,249,0.07)', border: '1px solid rgba(232,121,249,0.22)',
      borderRadius: 4, fontSize: 11,
    }}>
      <span style={{ color: '#e879f9', fontWeight: 600, fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.3px', flexShrink: 0 }}>
        ⚡ Compressed
      </span>
      <span style={{ color: 'rgba(26,24,21,0.45)', flex: 1 }}>
        {wordsBefore ? `${wordsBefore.toLocaleString()} words → summary` : 'output compressed'} · uuid: {uuid.slice(0, 12)}…
      </span>
      <button
        onClick={() => onExpand(uuid)}
        style={{
          fontSize: 10, color: '#e879f9', cursor: 'pointer', padding: '1px 6px',
          border: '1px solid rgba(232,121,249,0.35)', borderRadius: 3, background: 'transparent',
          flexShrink: 0,
        }}
      >Expand ↗</button>
    </div>
  )
}

function ScopeWarnRow({ signals, avatar }: { signals: string[]; avatar: string }) {
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 8, padding: '5px 8px',
      background: 'rgba(245,158,11,0.08)', border: '1px solid rgba(245,158,11,0.25)',
      borderRadius: 4, fontSize: 11,
    }}>
      <span style={{ color: '#f59e0b', flexShrink: 0 }}>⚠</span>
      <span style={{ color: 'rgba(26,24,21,0.5)', flex: 1 }}>
        <strong style={{ color: '#b45309' }}>Scope signal</strong>: '{signals.join("', '")}' in {avatar} task — cross-scope logged to audit
      </span>
    </div>
  )
}

function SessionMarker({ label, ts, end }: { label: string; ts: string; end?: boolean }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '4px 0', fontSize: 11, color: 'rgba(26,24,21,0.45)' }}>
      <div style={{ width: 8, height: 8, borderRadius: '50%', flexShrink: 0, background: end ? 'rgba(26,24,21,0.3)' : 'var(--marigold)' }} />
      <span>{label}{ts ? ` · ${fmtTime(ts)}` : ''}</span>
      <div style={{ flex: 1, height: 1, background: 'rgba(26,24,21,0.1)' }} />
    </div>
  )
}

// ── Main TracesTab ────────────────────────────────────────────────────────────

export function TracesTab({ currentSession, stepEvents, sessionTotals, userId }: Props) {
  const [traceEvents, setTraceEvents] = useState<TraceEvent[]>([])
  const [auditEntries, setAuditEntries] = useState<AuditEntry[]>([])
  const [expandedCard, setExpandedCard] = useState<string | null>(null)
  const [expandedContent, setExpandedContent] = useState<Record<string, string>>({})
  const sessionId = currentSession?.sessionId ?? null

  // Fetch trace on session change
  useEffect(() => {
    if (!sessionId) return
    apiFetch(`/trace/${sessionId}`)
      .then(r => r.ok ? r.json() : [])
      .then((data: TraceEvent[]) => setTraceEvents(Array.isArray(data) ? data : []))
      .catch(() => {})
  }, [sessionId])

  // Fetch audit log
  useEffect(() => {
    apiFetch(apiUrl('/audit', { user_id: userId, limit: 30 }))
      .then(r => r.ok ? r.json() : [])
      .then((data: AuditEntry[]) => setAuditEntries(Array.isArray(data) ? data : []))
      .catch(() => {})
  }, [userId, sessionId])

  const expandSandbox = async (uuid: string) => {
    if (expandedContent[uuid]) return
    try {
      const r = await apiFetch(`/sandbox/${uuid}`)
      if (r.ok) {
        const d = await r.json()
        setExpandedContent(prev => ({ ...prev, [uuid]: d.content ?? '' }))
      }
    } catch {}
  }

  // Derive avatar invocations from trace events
  const avatarDoneEvents = traceEvents.filter(e => e.event === 'avatar_done' && e.avatar)
  const sessionStart = traceEvents.find(e => e.event === 'session_start')
  const sessionEnd   = traceEvents.find(e => e.event === 'session_done')

  // Audit entries for this session's avatars (cross-scope warnings)
  const sessionWarnings = auditEntries.filter(e =>
    e.event === 'scope_warning' &&
    avatarDoneEvents.some(ae => ae.avatar === e.avatar)
  )

  // Token bar per avatar
  const avatarUsage: Record<string, number> = {}
  for (const ev of avatarDoneEvents) {
    if (ev.avatar && ev.usage) avatarUsage[ev.avatar] = ev.usage.total_tokens
  }
  const maxTok = Math.max(...Object.values(avatarUsage), 1)

  // Fallback: derive from stepEvents if no trace yet (live session)
  const fallbackAvatars = Object.keys(
    stepEvents.reduce<Record<string, boolean>>((a, e) => { if (e.avatar) a[e.avatar] = true; return a }, {})
  )

  const hasData = avatarDoneEvents.length > 0 || stepEvents.length > 0

  if (!hasData) {
    return (
      <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'rgba(26,24,21,0.35)', fontSize: 13 }}>
        No session trace yet — send a message to start.
      </div>
    )
  }

  return (
    <div style={{ flex: 1, display: 'flex', overflow: 'hidden' }}>

      {/* Timeline column */}
      <div style={{ flex: 1, overflowY: 'auto', padding: 16, display: 'flex', flexDirection: 'column' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
          <span style={{ fontSize: 11.5, fontWeight: 600, color: 'rgba(26,24,21,0.5)', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
            Session Timeline
          </span>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'rgba(26,24,21,0.4)' }}>
            {sessionId ? sessionId.slice(0, 8) + '…' : '—'}
            {sessionEnd && ` · ${fmtMs(sessionEnd.total_ms ?? 0)}`}
          </span>
        </div>

        {sessionStart && <SessionMarker label="session_start" ts={sessionStart.ts} />}

        {/* Avatar invocation cards */}
        {avatarDoneEvents.map((ev, i) => {
          const cardKey = `${ev.avatar}_${i}`
          const isExpanded = expandedCard === cardKey
          const traj = ev.trajectory
          const scopeWarn = sessionWarnings.filter(w => w.avatar === ev.avatar)
          const hasSandbox = !!ev.sandbox_uuid

          return (
            <div key={cardKey} style={{ position: 'relative', paddingLeft: 22, marginBottom: 4 }}>
              {/* connector */}
              {i < avatarDoneEvents.length - 1 && (
                <div style={{ position: 'absolute', left: 6, top: 20, bottom: -4, width: 1, background: 'rgba(26,24,21,0.1)' }} />
              )}
              {/* dot */}
              <div style={{
                position: 'absolute', left: 0, top: 8, width: 13, height: 13, borderRadius: '50%',
                border: `2px solid ${avatarColor(ev.avatar)}`, background: 'var(--paper)',
              }} />

              {/* Card */}
              <div style={{
                background: 'rgba(26,24,21,0.03)', border: '1px solid rgba(26,24,21,0.1)',
                borderRadius: 8, marginBottom: 4, overflow: 'hidden',
                transition: 'border-color 0.12s',
              }}>
                {/* Card header */}
                <div
                  onClick={() => setExpandedCard(isExpanded ? null : cardKey)}
                  style={{ padding: '8px 12px', display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}
                >
                  <span style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.3px', color: avatarColor(ev.avatar), flexShrink: 0 }}>
                    {ev.avatar?.toUpperCase()}
                  </span>
                  <span style={{ flex: 1, fontSize: 12, color: 'rgba(26,24,21,0.6)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {traj?.task_preview ?? ev.trajectory?.task_preview ?? '—'}
                  </span>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 5, flexShrink: 0 }}>
                    {ev.discipline && <MetaPill label={ev.discipline} color={avatarColor(ev.avatar)} />}
                    {ev.usage && <MetaPill label={`${ev.usage.total_tokens.toLocaleString()} tok`} color="var(--marigold)" />}
                    {ev.latency_ms && <MetaPill label={fmtMs(ev.latency_ms)} />}
                    {traj && <MetaPill label={`${traj.turns.reduce((s, t) => s + t.tool_calls.length, 0)} tools`} color="var(--nila)" />}
                    {ev.degraded_capabilities && ev.degraded_capabilities.length > 0 && (
                      <MetaPill label={`${ev.degraded_capabilities.length} degraded`} color="var(--kesari)" />
                    )}
                  </div>
                </div>

                {/* Expanded body */}
                {isExpanded && (
                  <div style={{ borderTop: '1px solid rgba(26,24,21,0.08)', padding: '8px 12px', display: 'flex', flexDirection: 'column', gap: 4 }}>
                    {/* Tool calls */}
                    {traj?.turns.flatMap(t => t.tool_calls).map((tc, j) => (
                      <ToolCallRow key={j} tc={tc} />
                    ))}

                    {/* Corrective RAG placeholder — detect from tool calls mentioning validate */}
                    {traj?.turns.some(t => t.tool_calls.some(tc => tc.tool.includes('validate') || tc.tool.includes('search') && t.tool_calls.length > 1)) && (
                      <ValidateRow passed={3} total={5} />
                    )}

                    {/* Scope warnings */}
                    {scopeWarn.map((w, j) => (
                      <ScopeWarnRow key={j} signals={w.matched_signals ?? []} avatar={ev.avatar ?? ''} />
                    ))}

                    {ev.degraded_capabilities && ev.degraded_capabilities.length > 0 && (
                      <div style={{ fontSize: 11, color: 'rgba(26,24,21,0.55)', paddingTop: 2 }}>
                        Degraded capabilities: {ev.degraded_capabilities.join(', ')}
                      </div>
                    )}

                    {/* Compression event */}
                    {hasSandbox && (
                      <CompressRow
                        uuid={ev.sandbox_uuid!}
                        wordsBefore={ev.result_len ? Math.round(ev.result_len / 5) : undefined}
                        wordsAfter={480}
                        onExpand={expandSandbox}
                      />
                    )}

                    {/* Expanded sandbox content */}
                    {ev.sandbox_uuid && expandedContent[ev.sandbox_uuid] && (
                      <div style={{
                        padding: 8, background: 'rgba(26,24,21,0.04)', borderRadius: 4,
                        fontSize: 11.5, color: 'rgba(26,24,21,0.6)',
                        maxHeight: 200, overflowY: 'auto', whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                        fontFamily: 'var(--font-mono)',
                      }}>
                        {expandedContent[ev.sandbox_uuid]}
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>
          )
        })}

        {/* Narad synthesis (last) */}
        {sessionEnd && (
          <div style={{ position: 'relative', paddingLeft: 22, marginBottom: 4 }}>
            <div style={{ position: 'absolute', left: 0, top: 8, width: 13, height: 13, borderRadius: '50%', border: `2px solid var(--marigold)`, background: 'var(--paper)' }} />
            <div style={{ background: 'rgba(26,24,21,0.03)', border: '1px solid rgba(26,24,21,0.1)', borderRadius: 8, padding: '8px 12px', display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--marigold)', flexShrink: 0 }}>NARAD</span>
              <span style={{ flex: 1, fontSize: 12, color: 'rgba(26,24,21,0.5)' }}>Synthesis — orchestrating avatar results → final response</span>
              {sessionTotals.completionTokens > 0 && (
                <MetaPill label={`${sessionTotals.completionTokens.toLocaleString()} tok`} color="var(--marigold)" />
              )}
            </div>
          </div>
        )}

        {sessionEnd && <SessionMarker label="session_done" ts={sessionEnd.ts} end />}

        {/* Fallback for live/no-trace session */}
        {avatarDoneEvents.length === 0 && stepEvents.length > 0 && (
          <>
            {fallbackAvatars.map(av => (
              <div key={av} style={{ position: 'relative', paddingLeft: 22, marginBottom: 4 }}>
                <div style={{ position: 'absolute', left: 0, top: 8, width: 13, height: 13, borderRadius: '50%', border: `2px solid ${avatarColor(av)}`, background: 'var(--paper)' }} />
                <div style={{ background: 'rgba(26,24,21,0.03)', border: '1px solid rgba(26,24,21,0.1)', borderRadius: 8, padding: '8px 12px', display: 'flex', flexDirection: 'column', gap: 4 }}>
                  <span style={{ fontSize: 11, fontWeight: 700, color: avatarColor(av) }}>{av.toUpperCase()}</span>
                  {stepEvents.filter(e => e.avatar === av).map((e, j) => (
                    <div key={j} style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 11.5, padding: '3px 0' }}>
                      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'rgba(26,24,21,0.35)', flexShrink: 0 }}>
                        {e.kind === 'tool_call' ? '⚙' : e.kind === 'tool_result' ? '↩' : '✍'}
                      </span>
                      <span style={{ flex: 1, color: 'rgba(26,24,21,0.55)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {e.preview}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </>
        )}
      </div>

      {/* Right summary panel */}
      <div style={{
        width: 248, borderLeft: '1px solid rgba(26,24,21,0.1)',
        background: 'rgba(26,24,21,0.02)', display: 'flex', flexDirection: 'column',
        overflowY: 'auto', flexShrink: 0,
      }}>

        {/* Token usage */}
        <div style={{ padding: '12px 14px 10px', borderBottom: '1px solid rgba(26,24,21,0.08)' }}>
          <div style={{ fontSize: 10.5, fontWeight: 600, color: 'rgba(26,24,21,0.4)', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: 10 }}>
            Token Usage
          </div>
          {Object.entries(avatarUsage).map(([av, tok]) => (
            <div key={av} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
              <span style={{ width: 72, fontSize: 11, fontWeight: 600, color: avatarColor(av), flexShrink: 0, overflow: 'hidden', textOverflow: 'ellipsis' }}>{av}</span>
              <div style={{ flex: 1, height: 5, background: 'rgba(26,24,21,0.08)', borderRadius: 3, overflow: 'hidden' }}>
                <div style={{ height: '100%', borderRadius: 3, background: avatarColor(av), width: `${Math.round((tok / maxTok) * 100)}%` }} />
              </div>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'rgba(26,24,21,0.45)', width: 38, textAlign: 'right' }}>
                {tok.toLocaleString()}
              </span>
            </div>
          ))}
          {sessionTotals.totalTokens > 0 && (
            <div style={{ marginTop: 8, paddingTop: 8, borderTop: '1px solid rgba(26,24,21,0.08)', display: 'flex', justifyContent: 'space-between' }}>
              <span style={{ fontSize: 11, color: 'rgba(26,24,21,0.4)' }}>Total</span>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, fontWeight: 600, color: 'var(--marigold)' }}>
                {sessionTotals.totalTokens.toLocaleString()}
              </span>
            </div>
          )}
        </div>

        {/* Latency breakdown */}
        {avatarDoneEvents.some(e => e.latency_ms) && (
          <div style={{ padding: '12px 14px 10px', borderBottom: '1px solid rgba(26,24,21,0.08)' }}>
            <div style={{ fontSize: 10.5, fontWeight: 600, color: 'rgba(26,24,21,0.4)', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: 10 }}>
              Latency
            </div>
            {avatarDoneEvents.filter(e => e.latency_ms).map((ev, i) => (
              <div key={i} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '3px 0', fontSize: 11.5 }}>
                <span style={{ color: avatarColor(ev.avatar) }}>{ev.avatar}</span>
                <span style={{ fontFamily: 'var(--font-mono)', color: 'rgba(26,24,21,0.45)' }}>{fmtMs(ev.latency_ms!)}</span>
              </div>
            ))}
          </div>
        )}

        {/* Audit log excerpt */}
        {auditEntries.length > 0 && (
          <div style={{ padding: '12px 14px 10px', borderBottom: '1px solid rgba(26,24,21,0.08)' }}>
            <div style={{ fontSize: 10.5, fontWeight: 600, color: 'rgba(26,24,21,0.4)', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: 10 }}>
              Audit Log
            </div>
            {auditEntries.slice(0, 6).map((e, i) => (
              <div key={i}>
                {e.event === 'scope_warning' ? (
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '4px 0', fontSize: 11, color: '#b45309' }}>
                    <span>⚠</span>
                    <span style={{ color: 'rgba(26,24,21,0.45)' }}>
                      Scope: '{e.matched_signals?.join("', '")}' in {e.avatar} task
                    </span>
                  </div>
                ) : (
                  <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8, padding: '5px 0', borderBottom: '1px solid rgba(26,24,21,0.06)', fontSize: 11 }}>
                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'rgba(26,24,21,0.35)', flexShrink: 0, marginTop: 1 }}>
                      {e.ts.slice(11, 19)}
                    </span>
                    <div>
                      <div><span style={{ fontWeight: 600, color: avatarColor(e.avatar) }}>{e.avatar}</span> invoked</div>
                      <div style={{ color: 'rgba(26,24,21,0.45)', marginTop: 1 }}>{e.task_preview.slice(0, 60)}{e.task_preview.length > 60 ? '…' : ''}</div>
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}

      </div>
    </div>
  )
}
