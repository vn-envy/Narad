import { useEffect, useState, useCallback } from 'react'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'
import { ZigzagBank } from './Motifs'
import { toast } from 'sonner'
import { cn } from '@/lib/utils'

const API = 'http://localhost:8000'

interface Sutra {
  id: string
  ts: string
  avatar: string
  query: string
  result: string
  score: number
  score_reason: string
  status: 'pending' | 'active' | 'reverted'
  cooldown_remaining: string | null
}

interface Sankalpa {
  id: string
  ts: string
  user_id: string
  avatar: string
  pattern_type: 'style' | 'preference' | 'domain' | 'workflow'
  content: string
  evidence: string
  confidence: number
  source_count: number
  status: 'pending' | 'active' | 'reverted'
  cooldown_remaining: string | null
}

const AVATAR_COLOURS: Record<string, string> = {
  Matsya:      '#065f46',
  Varaha:      '#c2410c',
  Narasimha:   '#c2410c',
  Rama:        '#2d2a26',
  Krishna:     '#065f46',
  Buddha:      '#92610a',
  Parashurama: '#57534e',
  __global__:  '#78716c',
}

const PATTERN_TYPE_LABELS: Record<string, string> = {
  style:      'Style',
  preference: 'Preference',
  domain:     'Domain',
  workflow:   'Workflow',
}

function scoreColorClass(score: number) {
  if (score >= 0.85) return 'text-[#065f46]'
  if (score >= 0.75) return 'text-[#92610a]'
  return 'text-[#c2410c]'
}


// ── Shared UI helpers ─────────────────────────────────────────────────────────

function SectionLabel({ label, count, accent, dim }: {
  label: string; count: number; accent: string; dim?: boolean
}) {
  return (
    <div className={cn('flex items-center gap-1.5 mt-0.5', dim && 'opacity-50')}>
      <span className="label-section" style={{ color: accent }}>{label}</span>
      <span className="text-chip px-1.5 py-px rounded" style={{ background: accent, color: 'var(--paper)' }}>
        {count}
      </span>
    </div>
  )
}

function Arrow() {
  return <span className="text-kajal opacity-25 text-[12px] self-start mt-2.5 flex-shrink-0">→</span>
}

function PipeStep({ icon, color, label, sub }: { icon: string; color: string; label: string; sub: string }) {
  return (
    <div className="flex flex-col items-center gap-0.5 min-w-[52px]">
      <span className="text-[14px] leading-none" style={{ color }}>{icon}</span>
      <span className="text-chip uppercase" style={{ color }}>{label}</span>
      <span className="font-mono text-[8px] text-kajal opacity-45 text-center leading-tight">{sub}</span>
    </div>
  )
}


// ── Sutra legend ──────────────────────────────────────────────────────────────

function SutraLegend() {
  return (
    <div
      className="px-3.5 py-2 pb-1.5 border-b flex-shrink-0"
      style={{ background: 'var(--speckle)', borderColor: 'color-mix(in srgb, var(--kajal) 10%, transparent)' }}
    >
      <div className="flex items-start justify-between gap-1 mb-1.5">
        <PipeStep icon="◎" color="#78716c" label="Session ends" sub="Tapas scores 0–1" />
        <Arrow />
        <PipeStep icon="⏳" color="#c2410c" label="Pending" sub="24h cooldown" />
        <Arrow />
        <PipeStep icon="●" color="#065f46" label="Active" sub="Injected into prompts" />
        <Arrow />
        <PipeStep icon="✕" color="#c2410c" label="Reverted" sub="Never injected" />
      </div>

      <Separator className="my-1.5 opacity-20" />

      <div className="flex flex-col gap-[3px]">
        <div className="flex items-center gap-1.5">
          <span className="text-chip px-1.5 py-px rounded organic-border whitespace-nowrap"
            style={{ background: 'rgba(6,95,70,0.08)', color: '#065f46', borderColor: 'rgba(6,95,70,0.30)' }}>
            score ≥ 0.75
          </span>
          <span className="font-body text-[10px] opacity-65 leading-tight" style={{ color: 'var(--kajal)' }}>Tapas promotes to Sutra — enters cooldown</span>
        </div>
        <div className="flex items-center gap-1.5">
          <span className="text-chip px-1.5 py-px rounded organic-border whitespace-nowrap"
            style={{ background: 'rgba(45,42,38,0.06)', color: 'var(--kajal)', borderColor: 'rgba(45,42,38,0.25)' }}>
            Accept
          </span>
          <span className="font-body text-[10px] opacity-65 leading-tight" style={{ color: 'var(--kajal)' }}>Skip cooldown — starts influencing responses now</span>
        </div>
        <div className="flex items-center gap-1.5">
          <span className="text-chip px-1.5 py-px rounded organic-border whitespace-nowrap"
            style={{ background: 'rgba(194,65,12,0.08)', color: 'var(--sindoor)', borderColor: 'rgba(194,65,12,0.30)' }}>
            Revert
          </span>
          <span className="font-body text-[10px] opacity-65 leading-tight" style={{ color: 'var(--kajal)' }}>Reject permanently — removed from all future context</span>
        </div>
      </div>
    </div>
  )
}


// ── Sankalpa legend ───────────────────────────────────────────────────────────

function SankalpаLegend() {
  return (
    <div
      className="px-3.5 py-2 pb-1.5 border-b flex-shrink-0"
      style={{ background: 'var(--speckle)', borderColor: 'color-mix(in srgb, var(--kajal) 10%, transparent)' }}
    >
      <div className="flex items-start justify-between gap-1 mb-1.5">
        <PipeStep icon="◉" color="#78716c" label="5 sessions" sub="Per avatar" />
        <Arrow />
        <PipeStep icon="⏳" color="#c2410c" label="Pending" sub="24h cooldown" />
        <Arrow />
        <PipeStep icon="●" color="#78716c" label="Active" sub="Style injected" />
        <Arrow />
        <PipeStep icon="✕" color="#c2410c" label="Reverted" sub="Suppressed" />
      </div>

      <Separator className="my-1.5 opacity-20" />

      <span className="font-body text-[10px] opacity-60 leading-tight block" style={{ color: 'var(--kajal)' }}>
        Patterns about <em>how you work</em> — tone, format, domain — extracted automatically and injected as style context.
      </span>
    </div>
  )
}


// ── Sutra card ────────────────────────────────────────────────────────────────

function SutraCard({ sutra, actioning, onAccept, onRevert, showActions, dim }: {
  sutra: Sutra
  actioning: boolean
  onAccept?: () => void
  onRevert?: () => void
  showActions?: boolean
  dim?: boolean
}) {
  const avatarColor = AVATAR_COLOURS[sutra.avatar] ?? 'var(--kajal)'

  return (
    <div className={cn(
      'folk-card folk-shadow rounded px-3.5 py-3 transition-opacity',
      `avatar-glass-${sutra.avatar.toLowerCase()}`,
      dim && 'opacity-40'
    )}>
      <div className="flex items-center gap-1.5 mb-1">
        <Badge
          variant="avatar"
          className="text-chip px-[7px] rounded"
          style={{ background: avatarColor, color: 'var(--paper)' }}
        >
          {sutra.avatar}
        </Badge>
        <span className={cn('font-mono text-[9px] font-bold', scoreColorClass(sutra.score))}>
          {sutra.score.toFixed(2)}
        </span>
        <span
          className="font-mono text-[8px] ml-auto opacity-60 tracking-wide"
          style={{
            color: sutra.status === 'active' ? '#065f46'
                 : sutra.status === 'pending' ? '#c2410c'
                 : 'var(--kajal)',
          }}
        >
          {sutra.status === 'pending' && sutra.cooldown_remaining
            ? `⏳ ${sutra.cooldown_remaining}`
            : sutra.status.toUpperCase()}
        </span>
      </div>

      <p className="font-body text-[11px] leading-[1.4] font-medium m-0" style={{ color: 'var(--kajal)' }}>
        {sutra.query.slice(0, 110)}{sutra.query.length > 110 ? '…' : ''}
      </p>
      <p className="font-body text-[10px] opacity-55 leading-tight mt-0.5 mb-0" style={{ color: 'var(--kajal)' }}>
        {sutra.score_reason}
      </p>

      {showActions && !dim && (
        <div className="flex gap-1.5 mt-2">
          {onAccept && (
            <Button
              size="sm"
              disabled={actioning}
              onClick={onAccept}
              className="h-[22px] text-[9px] px-2.5 label-hero border-0 hover:opacity-90 rounded"
              style={{ background: 'var(--kajal)', color: 'var(--paper)' }}
            >
              Accept ▲
            </Button>
          )}
          {onRevert && (
            <Button
              size="sm"
              variant="outline"
              disabled={actioning}
              onClick={onRevert}
              className="h-[22px] text-[9px] px-2.5 label-hero hover:bg-sindoor/10 rounded organic-border"
              style={{ borderColor: 'rgba(194,65,12,0.40)', color: 'var(--sindoor)' }}
            >
              Revert ↩
            </Button>
          )}
        </div>
      )}

      {!showActions && sutra.status === 'active' && !dim && onRevert && (
        <div className="mt-1.5">
          <Button
            size="sm"
            variant="outline"
            disabled={actioning}
            onClick={onRevert}
            className="h-5 text-[9px] px-2 label-hero opacity-70 hover:opacity-100 hover:bg-sindoor/10 rounded organic-border"
            style={{ borderColor: 'rgba(194,65,12,0.35)', color: 'var(--sindoor)' }}
          >
            Revert
          </Button>
        </div>
      )}
    </div>
  )
}


// ── Sankalpa card ─────────────────────────────────────────────────────────────

const PATTERN_COLOURS: Record<string, string> = {
  style:      '#065f46',
  preference: '#065f46',
  domain:     '#78716c',
  workflow:   '#57534e',
}

function SankalpаCard({ sankalpa, actioning, onAccept, onRevert, showActions, dim }: {
  sankalpa: Sankalpa
  actioning: boolean
  onAccept?: () => void
  onRevert?: () => void
  showActions?: boolean
  dim?: boolean
}) {
  const avatarColor   = AVATAR_COLOURS[sankalpa.avatar] ?? '#78716c'
  const patternColor  = PATTERN_COLOURS[sankalpa.pattern_type] ?? '#78716c'
  const displayAvatar = sankalpa.avatar === '__global__' ? 'Global' : sankalpa.avatar

  return (
    <div className={cn(
      'folk-card folk-shadow rounded px-3.5 py-3 transition-opacity',
      dim && 'opacity-40'
    )}
      style={{ borderLeft: `2px solid ${avatarColor}50` }}
    >
      <div className="flex items-center gap-1.5 mb-1.5">
        <Badge
          variant="avatar"
          className="text-chip px-[7px] rounded"
          style={{ background: avatarColor, color: 'var(--paper)' }}
        >
          {displayAvatar}
        </Badge>
        <span
          className="text-chip px-[6px] py-px rounded organic-border font-mono"
          style={{ color: patternColor, borderColor: `${patternColor}50`, background: `${patternColor}10` }}
        >
          {PATTERN_TYPE_LABELS[sankalpa.pattern_type] ?? sankalpa.pattern_type}
        </span>
        <span
          className="font-mono text-[8px] ml-auto opacity-60 tracking-wide"
          style={{
            color: sankalpa.status === 'active' ? '#065f46'
                 : sankalpa.status === 'pending' ? '#c2410c'
                 : 'var(--kajal)',
          }}
        >
          {sankalpa.status === 'pending' && sankalpa.cooldown_remaining
            ? `⏳ ${sankalpa.cooldown_remaining}`
            : sankalpa.status.toUpperCase()}
        </span>
      </div>

      <p className="font-body text-[11px] leading-[1.4] font-medium m-0" style={{ color: 'var(--kajal)' }}>
        {sankalpa.content}
      </p>

      {sankalpa.evidence && (
        <p className="font-mono text-[9px] opacity-40 leading-tight mt-1 mb-0 italic" style={{ color: 'var(--kajal)' }}>
          "{sankalpa.evidence.slice(0, 80)}{sankalpa.evidence.length > 80 ? '…' : ''}"
        </p>
      )}

      <div className="flex items-center gap-2 mt-1">
        <span className="font-mono text-[8px] opacity-40" style={{ color: 'var(--kajal)' }}>
          {Math.round(sankalpa.confidence * 100)}% confidence · {sankalpa.source_count} sessions
        </span>
      </div>

      {showActions && !dim && (
        <div className="flex gap-1.5 mt-2">
          {onAccept && (
            <Button
              size="sm"
              disabled={actioning}
              onClick={onAccept}
              className="h-[22px] text-[9px] px-2.5 label-hero border-0 hover:opacity-90 rounded"
              style={{ background: 'var(--kajal)', color: 'var(--paper)' }}
            >
              Accept ▲
            </Button>
          )}
          {onRevert && (
            <Button
              size="sm"
              variant="outline"
              disabled={actioning}
              onClick={onRevert}
              className="h-[22px] text-[9px] px-2.5 label-hero hover:bg-sindoor/10 rounded organic-border"
              style={{ borderColor: 'rgba(194,65,12,0.40)', color: 'var(--sindoor)' }}
            >
              Revert ↩
            </Button>
          )}
        </div>
      )}

      {!showActions && sankalpa.status === 'active' && !dim && onRevert && (
        <div className="mt-1.5">
          <Button
            size="sm"
            variant="outline"
            disabled={actioning}
            onClick={onRevert}
            className="h-5 text-[9px] px-2 label-hero opacity-70 hover:opacity-100 hover:bg-sindoor/10 rounded organic-border"
            style={{ borderColor: 'rgba(194,65,12,0.35)', color: 'var(--sindoor)' }}
          >
            Revert
          </Button>
        </div>
      )}
    </div>
  )
}


// ── Main panel ────────────────────────────────────────────────────────────────

interface KarmaSummary {
  total_events: number
  by_action: Record<string, number>
  recent: Array<{ ts: string; avatar: string; action: string; detail?: string }>
}

interface MsgUsage {
  role: string
  text: string
  usage?: {
    promptTokens: number
    completionTokens: number
    totalTokens: number
    tokPerSec?: number
    synthDurationMs?: number
  }
}

function MetricsTab({ sutras, karma, sessionTotals, messages }: {
  sutras: Sutra[]
  karma: KarmaSummary | null
  sessionTotals?: { promptTokens: number; completionTokens: number; totalTokens: number }
  messages?: MsgUsage[]
}) {
  if (!karma && sutras.length === 0 && !sessionTotals?.totalTokens) {
    return (
      <p className="font-body text-[11px] opacity-45 text-center py-6 leading-relaxed px-2" style={{ color: 'var(--kajal)' }}>
        No data yet. Run some queries to populate metrics.
      </p>
    )
  }

  // Sutra scores by avatar
  const byAvatar: Record<string, number[]> = {}
  for (const s of sutras) {
    if (!byAvatar[s.avatar]) byAvatar[s.avatar] = []
    byAvatar[s.avatar].push(s.score)
  }
  const avatarScores = Object.entries(byAvatar).map(([name, scores]) => ({
    name,
    avg: scores.reduce((a, b) => a + b, 0) / scores.length,
    count: scores.length,
  })).sort((a, b) => b.avg - a.avg)

  const maxAvg = Math.max(...avatarScores.map(a => a.avg), 1)

  // Karma by action
  const karmaEntries = karma
    ? Object.entries(karma.by_action).sort((a, b) => b[1] - a[1])
    : []
  const maxKarma = Math.max(...karmaEntries.map(e => e[1]), 1)

  // Sutra score distribution buckets
  const buckets = { '0.0–0.4': 0, '0.4–0.6': 0, '0.6–0.8': 0, '0.8–1.0': 0 }
  for (const s of sutras) {
    if (s.score < 0.4) buckets['0.0–0.4']++
    else if (s.score < 0.6) buckets['0.4–0.6']++
    else if (s.score < 0.8) buckets['0.6–0.8']++
    else buckets['0.8–1.0']++
  }
  const maxBucket = Math.max(...Object.values(buckets), 1)

  const barBase = 'h-1.5 rounded-full transition-all'

  // Token metrics derived from messages
  const assistantMsgs = (messages ?? []).filter(m => m.role === 'assistant' && m.usage)
  const avgTokPerSec = assistantMsgs.length > 0
    ? assistantMsgs.reduce((sum, m) => sum + (m.usage?.tokPerSec ?? 0), 0) / assistantMsgs.filter(m => m.usage?.tokPerSec).length
    : null
  const avgTokPerSecVal = assistantMsgs.filter(m => m.usage?.tokPerSec).length > 0 ? avgTokPerSec : null
  const promptPct = sessionTotals?.totalTokens
    ? (sessionTotals.promptTokens / sessionTotals.totalTokens) * 100
    : 0
  const completionPct = sessionTotals?.totalTokens
    ? (sessionTotals.completionTokens / sessionTotals.totalTokens) * 100
    : 0

  return (
    <div className="flex flex-col gap-4 px-3.5 py-3">

      {/* Token usage */}
      {sessionTotals && sessionTotals.totalTokens > 0 && (
        <div>
          <p className="font-mono text-[9px] tracking-widest mb-1.5 opacity-60" style={{ color: 'var(--kajal)' }}>
            TOKEN USAGE ({sessionTotals.totalTokens.toLocaleString()} total this session)
          </p>
          <div className="flex flex-col gap-1.5 mb-2">
            <div className="flex items-center gap-2">
              <span className="font-mono text-[9px] w-[72px] opacity-70" style={{ color: 'var(--kajal)' }}>Prompt</span>
              <div className="flex-1 bg-black/5 rounded-full h-1.5">
                <div className="h-1.5 rounded-full transition-all" style={{ width: `${promptPct}%`, background: 'var(--kajal)' }} />
              </div>
              <span className="font-mono text-[9px] opacity-60 w-[42px] text-right" style={{ color: 'var(--kajal)' }}>
                {sessionTotals.promptTokens.toLocaleString()}
              </span>
            </div>
            <div className="flex items-center gap-2">
              <span className="font-mono text-[9px] w-[72px] opacity-70" style={{ color: 'var(--kajal)' }}>Completion</span>
              <div className="flex-1 bg-black/5 rounded-full h-1.5">
                <div className="h-1.5 rounded-full transition-all" style={{ width: `${completionPct}%`, background: 'var(--marigold)' }} />
              </div>
              <span className="font-mono text-[9px] opacity-60 w-[42px] text-right" style={{ color: 'var(--kajal)' }}>
                {sessionTotals.completionTokens.toLocaleString()}
              </span>
            </div>
          </div>
          {avgTokPerSecVal != null && !isNaN(avgTokPerSecVal) && (
            <div className="flex items-center gap-2">
              <span className="font-mono text-[9px] opacity-55" style={{ color: 'var(--kajal)' }}>Avg throughput</span>
              <span className="font-mono text-[9px] font-medium" style={{ color: 'var(--marigold)' }}>
                {Math.round(avgTokPerSecVal).toLocaleString()} tok/s
              </span>
            </div>
          )}
          {assistantMsgs.length > 0 && (
            <div className="flex flex-col gap-0.5 mt-2">
              {assistantMsgs.slice(-5).map((m, i) => (
                <div key={i} className="flex items-center gap-2">
                  <span className="font-mono text-[8px] opacity-40 flex-1 truncate" style={{ color: 'var(--kajal)' }}>
                    {m.text.slice(0, 40)}{m.text.length > 40 ? '…' : ''}
                  </span>
                  <span className="font-mono text-[8px] opacity-55 flex-shrink-0" style={{ color: 'var(--kajal)' }}>
                    {m.usage!.totalTokens.toLocaleString()} tok
                    {m.usage!.tokPerSec ? ` · ${m.usage!.tokPerSec} tok/s` : ''}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Sutra score by avatar */}
      {avatarScores.length > 0 && (
        <div>
          <p className="font-mono text-[9px] tracking-widest mb-1.5 opacity-60" style={{ color: 'var(--kajal)' }}>
            AVG SUTRA SCORE BY AVATAR
          </p>
          <div className="flex flex-col gap-1">
            {avatarScores.map(a => (
              <div key={a.name} className="flex items-center gap-2">
                <span className="font-mono text-[9px] w-[72px] truncate opacity-70" style={{ color: 'var(--kajal)' }}>
                  {a.name}
                </span>
                <div className="flex-1 bg-black/5 rounded-full h-1.5">
                  <div
                    className={barBase}
                    style={{
                      width: `${(a.avg / maxAvg) * 100}%`,
                      background: a.avg >= 0.7 ? 'var(--marigold)' : a.avg >= 0.5 ? '#78716c' : '#dc2626',
                    }}
                  />
                </div>
                <span className="font-mono text-[9px] opacity-60 w-[26px] text-right" style={{ color: 'var(--kajal)' }}>
                  {a.avg.toFixed(2)}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Score distribution */}
      {sutras.length > 0 && (
        <div>
          <p className="font-mono text-[9px] tracking-widest mb-1.5 opacity-60" style={{ color: 'var(--kajal)' }}>
            SCORE DISTRIBUTION ({sutras.length} sutras)
          </p>
          <div className="flex flex-col gap-1">
            {Object.entries(buckets).map(([label, count]) => (
              <div key={label} className="flex items-center gap-2">
                <span className="font-mono text-[9px] w-[52px] opacity-70" style={{ color: 'var(--kajal)' }}>{label}</span>
                <div className="flex-1 bg-black/5 rounded-full h-1.5">
                  <div
                    className={barBase}
                    style={{
                      width: `${(count / maxBucket) * 100}%`,
                      background: label === '0.8–1.0' ? 'var(--marigold)' : label === '0.6–0.8' ? '#78716c' : '#dc2626',
                    }}
                  />
                </div>
                <span className="font-mono text-[9px] opacity-60 w-[16px] text-right" style={{ color: 'var(--kajal)' }}>
                  {count}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Karma events */}
      {karmaEntries.length > 0 && (
        <div>
          <p className="font-mono text-[9px] tracking-widest mb-1.5 opacity-60" style={{ color: 'var(--kajal)' }}>
            KARMA EVENTS ({karma?.total_events ?? 0} total)
          </p>
          <div className="flex flex-col gap-1">
            {karmaEntries.slice(0, 8).map(([action, count]) => (
              <div key={action} className="flex items-center gap-2">
                <span className="font-mono text-[9px] w-[80px] truncate opacity-70" style={{ color: 'var(--kajal)' }}>
                  {action}
                </span>
                <div className="flex-1 bg-black/5 rounded-full h-1.5">
                  <div
                    className={barBase}
                    style={{ width: `${(count / maxKarma) * 100}%`, background: 'var(--kajal)' }}
                  />
                </div>
                <span className="font-mono text-[9px] opacity-60 w-[24px] text-right" style={{ color: 'var(--kajal)' }}>
                  {count}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Recent karma */}
      {karma && karma.recent.length > 0 && (
        <div>
          <p className="font-mono text-[9px] tracking-widest mb-1.5 opacity-60" style={{ color: 'var(--kajal)' }}>
            RECENT EVENTS
          </p>
          <div className="flex flex-col gap-0.5">
            {karma.recent.slice(0, 6).map((e, i) => (
              <div key={i} className="flex items-center gap-2 py-0.5">
                <span className="font-mono text-[8px] opacity-40 w-[40px] truncate" style={{ color: 'var(--kajal)' }}>
                  {e.avatar?.slice(0, 4) ?? '—'}
                </span>
                <span className="font-mono text-[9px] opacity-70 flex-1 truncate" style={{ color: 'var(--kajal)' }}>
                  {e.action}
                </span>
                <span className="font-mono text-[8px] opacity-35" style={{ color: 'var(--kajal)' }}>
                  {new Date(e.ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

export function SutraPanel({
  userId = 'default',
  messages,
  sessionTotals,
}: {
  userId?: string
  messages?: MsgUsage[]
  sessionTotals?: { promptTokens: number; completionTokens: number; totalTokens: number }
}) {
  const [tab, setTab]               = useState<'sutras' | 'sankalpa' | 'metrics'>('sutras')
  const [sutras, setSutras]         = useState<Sutra[]>([])
  const [sankalpas, setSankalpas]   = useState<Sankalpa[]>([])
  const [karma, setKarma]           = useState<KarmaSummary | null>(null)
  const [loading, setLoading]       = useState(true)
  const [actioning, setActioning]   = useState<string | null>(null)

  const loadSutras = useCallback(async () => {
    try {
      const res = await fetch(`${API}/sutras`)
      const data = await res.json()
      setSutras(data.sutras ?? [])
    } catch { /* server not ready */ }
  }, [])

  const loadSankalpas = useCallback(async () => {
    try {
      const res = await fetch(`${API}/sankalpa?user_id=${userId}`)
      const data = await res.json()
      setSankalpas(data.sankalpas ?? [])
    } catch { /* server not ready */ }
  }, [userId])

  const loadKarma = useCallback(async () => {
    try {
      const res = await fetch(`${API}/karma`)
      const data = await res.json()
      setKarma(data)
    } catch { /* server not ready */ }
  }, [])

  const loadAll = useCallback(async () => {
    setLoading(true)
    await Promise.all([loadSutras(), loadSankalpas(), loadKarma()])
    setLoading(false)
  }, [loadSutras, loadSankalpas, loadKarma])

  useEffect(() => {
    loadAll()
    const t = setInterval(loadAll, 30_000)
    return () => clearInterval(t)
  }, [loadAll])

  async function actSutra(id: string, action: 'accept' | 'revert') {
    setActioning(id)
    try {
      const res = await fetch(`${API}/sutras/${id}/${action}`, { method: 'POST' })
      if (!res.ok) throw new Error(await res.text())
      toast.success(action === 'accept' ? 'Sutra accepted — active immediately' : 'Sutra reverted')
      await loadSutras()
    } catch (e) {
      toast.error(`Failed: ${e}`)
    } finally {
      setActioning(null)
    }
  }

  async function actSankalpa(id: string, action: 'accept' | 'revert') {
    setActioning(id)
    try {
      const res = await fetch(`${API}/sankalpa/${id}/${action}?user_id=${userId}`, { method: 'POST' })
      if (!res.ok) throw new Error(await res.text())
      toast.success(action === 'accept' ? 'Style pattern active immediately' : 'Pattern reverted — will not be injected')
      await loadSankalpas()
    } catch (e) {
      toast.error(`Failed: ${e}`)
    } finally {
      setActioning(null)
    }
  }

  const pendingSutras   = sutras.filter(s => s.status === 'pending')
  const activeSutras    = sutras.filter(s => s.status === 'active')
  const revertedSutras  = sutras.filter(s => s.status === 'reverted')

  const pendingSankalpa  = sankalpas.filter(s => s.status === 'pending')
  const activeSankalpa   = sankalpas.filter(s => s.status === 'active')
  const revertedSankalpa = sankalpas.filter(s => s.status === 'reverted')

  const sutraActiveCount    = activeSutras.length
  const sankalpаActiveCount = activeSankalpa.length

  return (
    <div className="flex flex-col h-full overflow-hidden" style={{ background: 'var(--paper)' }}>

      {/* Header + tab bar */}
      <div
        className="flex-shrink-0 relative overflow-hidden"
        style={{ background: 'var(--kajal)' }}
      >
        {/* Title row */}
        <div className="flex items-center gap-2 px-3.5 pt-2.5 pb-1">
          <span
            className="label-hero text-[16px] leading-none"
            style={{ color: 'var(--paper)' }}
          >
            {tab === 'sutras' ? 'सूत्र  SUTRAS' : tab === 'sankalpa' ? 'संकल्प  SANKALPA' : '📊  METRICS'}
          </span>
          {!loading && tab === 'sutras' && sutraActiveCount > 0 && (
            <span
              className="ml-auto font-mono text-[9px] px-1.5 py-px rounded organic-border"
              style={{ color: 'rgba(6,95,70,0.85)', background: 'rgba(6,95,70,0.15)', borderColor: 'rgba(6,95,70,0.35)' }}
            >
              ● {sutraActiveCount} injecting
            </span>
          )}
          {!loading && tab === 'sankalpa' && sankalpаActiveCount > 0 && (
            <span
              className="ml-auto font-mono text-[9px] px-1.5 py-px rounded organic-border"
              style={{ color: 'rgba(120,113,108,0.85)', background: 'rgba(120,113,108,0.20)', borderColor: 'rgba(120,113,108,0.40)' }}
            >
              ● {sankalpаActiveCount} styling
            </span>
          )}
        </div>

        {/* Tab bar */}
        <div className="flex px-3.5 pb-0 gap-0">
          {(['sutras', 'sankalpa', 'metrics'] as const).map(t => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className="flex items-center gap-1.5 px-3 py-1.5 text-[10px] font-mono tracking-wide transition-all border-b-2 mr-1"
              style={{
                color: tab === t ? 'var(--paper)' : 'rgba(252,250,242,0.45)',
                borderBottomColor: tab === t ? 'var(--marigold)' : 'transparent',
                background: 'transparent',
              }}
            >
              {t === 'sutras' ? 'SUTRAS' : t === 'sankalpa' ? 'SANKALPA' : 'METRICS'}
              {t === 'sutras' && pendingSutras.length > 0 && (
                <span className="font-mono text-[8px] px-1 rounded"
                  style={{ background: 'rgba(194,65,12,0.30)', color: 'var(--marigold)' }}>
                  {pendingSutras.length}
                </span>
              )}
              {t === 'sankalpa' && pendingSankalpa.length > 0 && (
                <span className="font-mono text-[8px] px-1 rounded"
                  style={{ background: 'rgba(194,65,12,0.30)', color: 'var(--marigold)' }}>
                  {pendingSankalpa.length}
                </span>
              )}
            </button>
          ))}
        </div>

        {/* Zigzag motif bottom edge */}
        <div className="absolute bottom-0 left-0 w-full overflow-hidden" style={{ height: 10, opacity: 0.10 }}>
          <ZigzagBank color="var(--paper)" className="w-full" />
        </div>
      </div>

      {/* Legend — swaps with tab */}
      {tab === 'sutras' ? <SutraLegend /> : tab === 'sankalpa' ? <SankalpаLegend /> : null}

      {/* Scrollable list */}
      <div style={{ flex: 1, minHeight: 0, overflow: 'hidden', background: 'var(--paper)' }}>
        <ScrollArea style={{ height: '100%' }}>
          <div className="px-3.5 py-2.5 flex flex-col gap-1.5">

            {loading && (
              <p className="font-body text-[11px] opacity-45 text-center py-6" style={{ color: 'var(--kajal)' }}>Loading…</p>
            )}

            {/* ── SUTRAS TAB ── */}
            {!loading && tab === 'sutras' && (
              <>
                {sutras.length === 0 && (
                  <p className="font-body text-[11px] opacity-45 text-center py-6 leading-relaxed px-2" style={{ color: 'var(--kajal)' }}>
                    No sutras yet. Run some queries — Tapas will promote high-quality responses here.
                  </p>
                )}

                {pendingSutras.length > 0 && (
                  <SectionLabel label="Pending Review" count={pendingSutras.length} accent="#c2410c" />
                )}
                {pendingSutras.map(s => (
                  <SutraCard key={s.id} sutra={s} actioning={actioning === s.id}
                    onAccept={() => actSutra(s.id, 'accept')}
                    onRevert={() => actSutra(s.id, 'revert')}
                    showActions />
                ))}

                {activeSutras.length > 0 && (
                  <>
                    {pendingSutras.length > 0 && <Separator className="my-0.5 opacity-20" />}
                    <SectionLabel label="Injected into Prompts" count={activeSutras.length} accent="#065f46" />
                  </>
                )}
                {activeSutras.map(s => (
                  <SutraCard key={s.id} sutra={s} actioning={actioning === s.id}
                    onRevert={() => actSutra(s.id, 'revert')} showActions={false} />
                ))}

                {revertedSutras.length > 0 && (
                  <>
                    <Separator className="my-0.5 opacity-20" />
                    <SectionLabel label="Reverted" count={revertedSutras.length} accent="var(--kajal)" dim />
                  </>
                )}
                {revertedSutras.map(s => (
                  <SutraCard key={s.id} sutra={s} actioning={false} dim />
                ))}
              </>
            )}

            {/* ── METRICS TAB ── */}
            {!loading && tab === 'metrics' && (
              <MetricsTab sutras={sutras} karma={karma} sessionTotals={sessionTotals} messages={messages} />
            )}

            {/* ── SANKALPA TAB ── */}
            {!loading && tab === 'sankalpa' && (
              <>
                {sankalpas.length === 0 && (
                  <p className="font-body text-[11px] opacity-45 text-center py-6 leading-relaxed px-2" style={{ color: 'var(--kajal)' }}>
                    No style patterns yet. After every 5 sessions with an avatar, Sankalpa extracts how you work and injects it automatically.
                  </p>
                )}

                {pendingSankalpa.length > 0 && (
                  <SectionLabel label="Pending Review" count={pendingSankalpa.length} accent="#c2410c" />
                )}
                {pendingSankalpa.map(s => (
                  <SankalpаCard key={s.id} sankalpa={s} actioning={actioning === s.id}
                    onAccept={() => actSankalpa(s.id, 'accept')}
                    onRevert={() => actSankalpa(s.id, 'revert')}
                    showActions />
                ))}

                {activeSankalpa.length > 0 && (
                  <>
                    {pendingSankalpa.length > 0 && <Separator className="my-0.5 opacity-20" />}
                    <SectionLabel label="Shaping Your Responses" count={activeSankalpa.length} accent="#78716c" />
                  </>
                )}
                {activeSankalpa.map(s => (
                  <SankalpаCard key={s.id} sankalpa={s} actioning={actioning === s.id}
                    onRevert={() => actSankalpa(s.id, 'revert')} showActions={false} />
                ))}

                {revertedSankalpa.length > 0 && (
                  <>
                    <Separator className="my-0.5 opacity-20" />
                    <SectionLabel label="Reverted" count={revertedSankalpa.length} accent="var(--kajal)" dim />
                  </>
                )}
                {revertedSankalpa.map(s => (
                  <SankalpаCard key={s.id} sankalpa={s} actioning={false} dim />
                ))}
              </>
            )}

          </div>
        </ScrollArea>
      </div>
    </div>
  )
}
