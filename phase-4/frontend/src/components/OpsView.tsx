import { useEffect, useState } from 'react'

// ── Types ──────────────────────────────────────────────────────────────────────

interface AndonEvent {
  id: string
  ts: string
  avatar: string
  trigger: string
  session_id: string
  task_preview: string
  result_preview: string
}

interface AndonStats {
  period_days: number
  total: number
  by_avatar: Record<string, number>
  by_class: Record<string, number>
}

interface FiveS {
  session_files: { count: number; oldest_days: number; reclaimable_mb: number; stale: number }
  artifacts: { count: number; orphaned: number; reclaimable_mb: number }
  weak_sessions: { count: number; stale: number }
  '5s_score': number
  last_shine: string | null
  total_reclaimable_mb: number
}

interface QualityReport {
  generated_at: string
  report: string
  metrics: Record<string, unknown>
}

const TRIGGER_COLOURS: Record<string, string> = {
  CONNECTION:   '#c2410c',
  EMPTY_RESULT: '#92610a',
  TIMEOUT:      '#7f1d1d',
  TOOL_ERROR:   '#c2410c',
  QUALITY:      '#78716c',
}

// ── Section wrapper ────────────────────────────────────────────────────────────

function Section({
  title,
  devanagari,
  children,
  defaultOpen = true,
}: {
  title: string
  devanagari: string
  children: React.ReactNode
  defaultOpen?: boolean
}) {
  const [open, setOpen] = useState(defaultOpen)

  return (
    <div style={{ borderBottom: '1px solid rgba(252,250,242,0.08)' }}>
      <button
        className="flex items-center justify-between w-full px-4 py-3 text-left transition-colors hover:bg-white/5"
        onClick={() => setOpen(v => !v)}
      >
        <div className="flex items-baseline gap-2">
          <span className="text-[12px] font-semibold" style={{ color: 'rgba(252,250,242,0.85)' }}>
            {title}
          </span>
          <span className="text-[11px] opacity-40" style={{ color: 'rgba(252,250,242,0.5)' }}>
            {devanagari}
          </span>
        </div>
        <span style={{ color: 'rgba(252,250,242,0.3)', fontSize: 12 }}>
          {open ? '▾' : '▸'}
        </span>
      </button>
      {open && <div className="pb-4">{children}</div>}
    </div>
  )
}

// ── Andon Section ─────────────────────────────────────────────────────────────

function AndonSection({ andonAlert }: { andonAlert: { avatar: string; trigger: string } | null }) {
  const [log, setLog] = useState<AndonEvent[]>([])
  const [stats, setStats] = useState<AndonStats | null>(null)

  useEffect(() => {
    fetch('/andon/log').then(r => r.json()).then(d => setLog(d.events || []))
    fetch('/andon/stats').then(r => r.json()).then(d => setStats(d))
  }, [])

  // Refresh when a new alert arrives
  useEffect(() => {
    if (!andonAlert) return
    fetch('/andon/log').then(r => r.json()).then(d => setLog(d.events || []))
    fetch('/andon/stats').then(r => r.json()).then(d => setStats(d))
  }, [andonAlert])

  return (
    <Section title="Andon Alerts" devanagari="जागृति">
      <div className="px-4">
        {/* Stats row */}
        {stats && (
          <div className="flex gap-3 mb-3 flex-wrap">
            <StatChip label="7-day total" value={String(stats.total)} />
            {Object.entries(stats.by_class).map(([cls, n]) => (
              <StatChip key={cls} label={cls} value={String(n)} colour={TRIGGER_COLOURS[cls]} />
            ))}
          </div>
        )}

        {/* Alert timeline */}
        {log.length === 0 ? (
          <div className="text-[11px] opacity-40" style={{ color: 'rgba(252,250,242,0.4)' }}>
            No Andon alerts yet — quality is good.
          </div>
        ) : (
          <div className="flex flex-col gap-2 max-h-48 overflow-y-auto">
            {log.map(ev => (
              <div
                key={ev.id}
                className="rounded px-3 py-2 text-[11px]"
                style={{ background: 'rgba(252,250,242,0.04)', border: '1px solid rgba(252,250,242,0.07)' }}
              >
                <div className="flex items-center gap-2 mb-1">
                  <span
                    className="text-[9px] font-bold px-1.5 py-0.5 rounded"
                    style={{
                      background: `${TRIGGER_COLOURS[ev.trigger] || '#57534e'}33`,
                      color: TRIGGER_COLOURS[ev.trigger] || '#78716c',
                    }}
                  >
                    {ev.trigger}
                  </span>
                  <span className="font-semibold" style={{ color: 'rgba(252,250,242,0.75)' }}>
                    {ev.avatar}
                  </span>
                  <span className="ml-auto font-mono text-[9px] opacity-40">
                    {new Date(ev.ts).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
                  </span>
                </div>
                <div className="opacity-50 truncate">{ev.task_preview}</div>
              </div>
            ))}
          </div>
        )}
      </div>
    </Section>
  )
}

// ── 5S Section ────────────────────────────────────────────────────────────────

function FiveSSection() {
  const [report, setReport] = useState<FiveS | null>(null)
  const [loading, setLoading] = useState(false)
  const [shineResult, setShineResult] = useState<string | null>(null)

  useEffect(() => {
    fetch('/5s/report').then(r => r.json()).then(d => setReport(d))
  }, [])

  const runShine = async (dryRun: boolean) => {
    if (!dryRun && !window.confirm(
      `This will permanently delete old session files and artifact directories.\n\n` +
      `Estimated reclaim: ${report?.total_reclaimable_mb ?? '?'} MB\n\nProceed?`
    )) return

    setLoading(true)
    try {
      const r = await fetch(`/5s/shine?dry_run=${dryRun}`, { method: 'POST' })
      const d = await r.json()
      setShineResult(
        dryRun
          ? `Dry run: ${d.session_candidates} sessions + ${d.artifact_candidates} artifacts reclaimable (${d.reclaimable_mb} MB)`
          : `Cleaned: freed ${d.freed_mb} MB (${d.deleted_sessions} sessions, ${d.deleted_artifacts} artifacts)`
      )
      // Refresh report
      fetch('/5s/report').then(r => r.json()).then(d => setReport(d))
    } finally {
      setLoading(false)
    }
  }

  return (
    <Section title="File System Health" devanagari="शुद्धि" defaultOpen={false}>
      <div className="px-4">
        {report ? (
          <>
            {/* Score meter */}
            <div className="flex items-center gap-3 mb-4">
              <div className="flex-1">
                <div className="flex justify-between mb-1">
                  <span className="text-[10px] font-mono opacity-50" style={{ color: 'rgba(252,250,242,0.5)' }}>
                    5S Score
                  </span>
                  <span className="text-[12px] font-bold" style={{ color: scoreColour(report['5s_score']) }}>
                    {(report['5s_score'] * 100).toFixed(0)}%
                  </span>
                </div>
                <div className="rounded-full h-2 overflow-hidden" style={{ background: 'rgba(252,250,242,0.1)' }}>
                  <div
                    className="h-full rounded-full transition-all"
                    style={{
                      width: `${report['5s_score'] * 100}%`,
                      background: scoreColour(report['5s_score']),
                    }}
                  />
                </div>
              </div>
            </div>

            {/* File stats table */}
            <table className="w-full text-[11px] mb-4" style={{ borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ color: 'rgba(252,250,242,0.35)', fontSize: 9 }}>
                  <th className="text-left pb-1 font-mono uppercase tracking-wider">Category</th>
                  <th className="text-right pb-1 font-mono uppercase tracking-wider">Count</th>
                  <th className="text-right pb-1 font-mono uppercase tracking-wider">Stale</th>
                  <th className="text-right pb-1 font-mono uppercase tracking-wider">Reclaim</th>
                </tr>
              </thead>
              <tbody>
                <FileRow label="Sessions" data={report.session_files} />
                <FileRow label="Artifacts" data={{ count: report.artifacts.count, stale: report.artifacts.orphaned, reclaimable_mb: report.artifacts.reclaimable_mb }} />
              </tbody>
            </table>

            {report.last_shine && (
              <div className="text-[10px] font-mono opacity-30 mb-3"
                style={{ color: 'rgba(252,250,242,0.4)' }}>
                Last shine: {new Date(report.last_shine).toLocaleDateString()}
              </div>
            )}

            {shineResult && (
              <div className="mb-3 text-[11px] px-3 py-2 rounded"
                style={{ background: 'rgba(6,95,70,0.2)', color: '#6ee7b7', border: '1px solid rgba(6,95,70,0.3)' }}>
                {shineResult}
              </div>
            )}

            <div className="flex gap-2">
              <button
                onClick={() => runShine(true)}
                disabled={loading}
                className="px-3 py-1.5 rounded text-[11px] font-semibold transition-colors disabled:opacity-40"
                style={{ background: 'rgba(252,250,242,0.08)', color: 'rgba(252,250,242,0.7)', border: '1px solid rgba(252,250,242,0.12)' }}
              >
                Run Dry Scan
              </button>
              <button
                onClick={() => runShine(false)}
                disabled={loading || report.total_reclaimable_mb === 0}
                className="px-3 py-1.5 rounded text-[11px] font-semibold transition-colors disabled:opacity-40"
                style={{ background: 'rgba(194,65,12,0.15)', color: '#c2410c', border: '1px solid rgba(194,65,12,0.25)' }}
              >
                Shine ({report.total_reclaimable_mb} MB)
              </button>
            </div>
          </>
        ) : (
          <div className="text-[11px] opacity-40" style={{ color: 'rgba(252,250,242,0.4)' }}>
            Loading 5S report…
          </div>
        )}
      </div>
    </Section>
  )
}

function FileRow({ label, data }: { label: string; data: { count: number; stale: number; reclaimable_mb: number } }) {
  return (
    <tr style={{ borderBottom: '1px solid rgba(252,250,242,0.05)', color: 'rgba(252,250,242,0.7)' }}>
      <td className="py-1.5">{label}</td>
      <td className="text-right py-1.5 font-mono">{data.count}</td>
      <td className="text-right py-1.5 font-mono" style={{ color: data.stale > 0 ? '#FFC837' : 'rgba(252,250,242,0.4)' }}>
        {data.stale}
      </td>
      <td className="text-right py-1.5 font-mono" style={{ color: data.reclaimable_mb > 100 ? '#c2410c' : 'rgba(252,250,242,0.4)' }}>
        {data.reclaimable_mb} MB
      </td>
    </tr>
  )
}

function scoreColour(score: number): string {
  if (score >= 0.85) return '#6ee7b7'
  if (score >= 0.65) return '#FFC837'
  return '#c2410c'
}

// ── DMAIC Section ─────────────────────────────────────────────────────────────

function DMAICSection() {
  const [report, setReport] = useState<QualityReport | null>(null)
  const [generating, setGenerating] = useState(false)

  useEffect(() => {
    fetch('/quality/report').then(r => {
      if (r.ok) return r.json()
      return null
    }).then(d => d && setReport(d))
  }, [])

  const generate = async () => {
    setGenerating(true)
    try {
      const r = await fetch('/quality/report', { method: 'POST' })
      const d = await r.json()
      setReport(d)
    } finally {
      setGenerating(false)
    }
  }

  return (
    <Section title="DMAIC Report" devanagari="विवेक" defaultOpen={false}>
      <div className="px-4">
        <button
          onClick={generate}
          disabled={generating}
          className="mb-4 px-3 py-1.5 rounded text-[11px] font-semibold transition-colors disabled:opacity-40"
          style={{ background: 'rgba(146,97,10,0.2)', color: '#FFC837', border: '1px solid rgba(146,97,10,0.3)' }}
        >
          {generating ? 'Generating…' : 'Generate Report'}
        </button>

        {report ? (
          <div>
            <div className="text-[10px] font-mono opacity-30 mb-3"
              style={{ color: 'rgba(252,250,242,0.4)' }}>
              Generated: {new Date(report.generated_at).toLocaleString()}
            </div>
            <div
              className="text-[12px] leading-relaxed whitespace-pre-wrap max-h-80 overflow-y-auto"
              style={{ color: 'rgba(252,250,242,0.75)' }}
            >
              {report.report}
            </div>
          </div>
        ) : !generating ? (
          <div className="text-[11px] opacity-40" style={{ color: 'rgba(252,250,242,0.4)' }}>
            No report yet — click Generate to run Buddha's DMAIC analysis.
          </div>
        ) : null}
      </div>
    </Section>
  )
}

// ── StatChip ──────────────────────────────────────────────────────────────────

function StatChip({ label, value, colour }: { label: string; value: string; colour?: string }) {
  return (
    <div
      className="flex flex-col items-center px-2 py-1 rounded"
      style={{
        background: colour ? `${colour}20` : 'rgba(252,250,242,0.06)',
        border: `1px solid ${colour ? `${colour}33` : 'rgba(252,250,242,0.08)'}`,
        minWidth: 40,
      }}
    >
      <span className="text-[13px] font-bold" style={{ color: colour || 'rgba(252,250,242,0.8)' }}>
        {value}
      </span>
      <span className="text-[9px] uppercase tracking-wider opacity-50"
        style={{ color: colour || 'rgba(252,250,242,0.5)' }}>
        {label}
      </span>
    </div>
  )
}

// ── OpsView (combined) ────────────────────────────────────────────────────────

interface OpsProps {
  andonAlert: { avatar: string; trigger: string } | null
}

export function OpsView({ andonAlert }: OpsProps) {
  return (
    <div className="flex flex-col h-full overflow-y-auto">
      <AndonSection andonAlert={andonAlert} />
      <FiveSSection />
      <DMAICSection />
    </div>
  )
}
