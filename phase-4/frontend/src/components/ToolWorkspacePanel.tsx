import { ExternalLink, X } from 'lucide-react'
import type { PendingToolUi, ToolArtifact } from '../hooks/useAvatara'

interface Props {
  toolUi: PendingToolUi
  onClose: () => void
}

function ArtifactPreview({ artifact }: { artifact: ToolArtifact | null }) {
  if (!artifact?.url) {
    return (
      <div className="flex items-center justify-center h-full text-[12px]" style={{ color: 'rgba(45,42,38,0.45)' }}>
        No preview available yet.
      </div>
    )
  }
  const lower = artifact.url.toLowerCase()
  if (lower.endsWith('.mp4')) {
    return <video src={artifact.url} controls className="w-full h-full object-contain rounded" style={{ background: 'rgba(45,42,38,0.08)' }} />
  }
  if (lower.endsWith('.wav') || lower.endsWith('.mp3')) {
    return (
      <div className="flex items-center justify-center h-full px-4">
        <audio src={artifact.url} controls className="w-full" />
      </div>
    )
  }
  if (lower.endsWith('.html')) {
    return <iframe src={artifact.url} title={artifact.label} className="w-full h-full rounded border-0" style={{ background: 'white' }} />
  }
  return (
    <div className="flex items-center justify-center h-full">
      <a
        href={artifact.url}
        target="_blank"
        rel="noopener noreferrer"
        className="inline-flex items-center gap-1 text-[12px] underline"
        style={{ color: 'var(--sindoor)' }}
      >
        Open artifact <ExternalLink size={12} />
      </a>
    </div>
  )
}

export function ToolWorkspacePanel({ toolUi, onClose }: Props) {
  const title = toolUi.ui?.title ?? `${toolUi.tool} workspace`
  const summary = toolUi.ui?.summary ?? toolUi.summary
  const primaryArtifact =
    (toolUi.ui?.primary_artifact_label
      ? toolUi.artifacts.find(item => item.label === toolUi.ui?.primary_artifact_label)
      : undefined)
    ?? toolUi.artifacts.find(item => item.url?.match(/\.(html|mp4|wav|mp3)$/i))
    ?? toolUi.artifacts[0]
    ?? null

  return (
    <div className="flex flex-col h-full overflow-hidden" style={{ background: 'var(--paper)' }}>
      <div
        className="flex items-center gap-2 px-3 flex-shrink-0"
        style={{
          height: 34,
          background: 'var(--kajal)',
          borderBottom: '1px solid rgba(252,250,242,0.07)',
        }}
      >
        <button
          onClick={onClose}
          className="w-3 h-3 rounded-full flex-shrink-0 hover:opacity-80 transition-opacity"
          style={{ background: '#ff5f57' }}
          title="Close"
        />
        <span className="font-mono text-[9px] tracking-[0.12em] uppercase flex-1 text-center" style={{ color: 'rgba(252,250,242,0.52)', marginLeft: -18 }}>
          {toolUi.avatar} · {toolUi.tool}
        </span>
        <button onClick={onClose} className="opacity-30 hover:opacity-60 transition-opacity">
          <X size={10} style={{ color: 'rgba(252,250,242,0.8)' }} />
        </button>
      </div>

      <div className="grid min-h-0 flex-1" style={{ gridTemplateRows: 'auto 1fr auto' }}>
        <div className="px-4 py-3 border-b" style={{ borderColor: 'rgba(45,42,38,0.08)' }}>
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-deva text-[15px]" style={{ color: 'var(--marigold)', fontFamily: 'var(--font-deva)' }}>दर्शन</span>
            <span className="text-[13px] font-semibold" style={{ color: 'var(--kajal)' }}>{title}</span>
            {toolUi.requiresConfirmation && (
              <span className="text-[10px] font-mono px-2 py-0.5 rounded-full" style={{ color: 'var(--kesari)', background: 'rgba(194,65,12,0.10)' }}>
                confirm first
              </span>
            )}
          </div>
          <p className="mt-1 text-[12px] leading-relaxed" style={{ color: 'rgba(45,42,38,0.64)' }}>
            {summary}
          </p>
        </div>

        <div style={{ minHeight: 0, overflow: 'hidden' }}>
          <ArtifactPreview artifact={primaryArtifact} />
        </div>

        <div className="px-4 py-3 overflow-y-auto border-t" style={{ borderColor: 'rgba(45,42,38,0.08)', maxHeight: '42%' }}>
          {(toolUi.ui?.sections ?? []).length > 0 && (
            <div className="mb-4">
              {(toolUi.ui?.sections ?? []).map(section => (
                <div key={section.title} className="mb-3">
                  <div className="font-mono text-[10px] uppercase tracking-[0.12em]" style={{ color: 'rgba(45,42,38,0.46)' }}>
                    {section.title}
                  </div>
                  <p className="text-[12px] leading-relaxed mt-1" style={{ color: 'rgba(45,42,38,0.72)' }}>
                    {section.body}
                  </p>
                </div>
              ))}
            </div>
          )}

          <div className="mb-4">
            <div className="font-mono text-[10px] uppercase tracking-[0.12em] mb-2" style={{ color: 'rgba(45,42,38,0.46)' }}>
              Artifacts
            </div>
            <div className="space-y-2">
              {toolUi.artifacts.length === 0 && (
                <div className="text-[12px]" style={{ color: 'rgba(45,42,38,0.42)' }}>
                  No artifacts attached.
                </div>
              )}
              {toolUi.artifacts.map(item => (
                <div key={`${item.label}-${item.url ?? item.path ?? item.type}`} className="rounded-lg px-3 py-2" style={{ border: '1px solid rgba(45,42,38,0.09)', background: 'rgba(255,255,255,0.42)' }}>
                  <div className="flex items-center justify-between gap-2">
                    <div>
                      <div className="text-[12px] font-semibold" style={{ color: 'var(--kajal)' }}>{item.label}</div>
                      <div className="text-[11px]" style={{ color: 'rgba(45,42,38,0.48)' }}>{item.type}</div>
                    </div>
                    {item.url && (
                      <a href={item.url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-[11px] underline" style={{ color: 'var(--sindoor)' }}>
                        open <ExternalLink size={11} />
                      </a>
                    )}
                  </div>
                  {item.description && (
                    <p className="text-[11px] mt-1 leading-relaxed" style={{ color: 'rgba(45,42,38,0.64)' }}>
                      {item.description}
                    </p>
                  )}
                </div>
              ))}
            </div>
          </div>

          <div>
            <div className="font-mono text-[10px] uppercase tracking-[0.12em] mb-2" style={{ color: 'rgba(45,42,38,0.46)' }}>
              Citations
            </div>
            <div className="space-y-2">
              {toolUi.citations.length === 0 && (
                <div className="text-[12px]" style={{ color: 'rgba(45,42,38,0.42)' }}>
                  No citations attached.
                </div>
              )}
              {toolUi.citations.map(item => (
                <a
                  key={`${item.url}-${item.title}`}
                  href={item.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="block rounded-lg px-3 py-2"
                  style={{ border: '1px solid rgba(45,42,38,0.09)', background: 'rgba(255,255,255,0.34)' }}
                >
                  <div className="text-[12px] font-semibold underline" style={{ color: 'var(--sindoor)' }}>
                    {item.title}
                  </div>
                  {item.source && (
                    <div className="text-[10px] mt-0.5 uppercase tracking-[0.08em]" style={{ color: 'rgba(45,42,38,0.45)' }}>
                      {item.source}
                    </div>
                  )}
                  {item.snippet && (
                    <div className="text-[11px] mt-1 leading-relaxed" style={{ color: 'rgba(45,42,38,0.66)' }}>
                      {item.snippet}
                    </div>
                  )}
                </a>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
