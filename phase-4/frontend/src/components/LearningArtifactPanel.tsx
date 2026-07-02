import { X } from 'lucide-react'
import type { ActiveArtifactSession } from '../hooks/useAvatara'
import { FlashcardArtifact } from './artifacts/FlashcardArtifact'
import { ConceptDiagramArtifact } from './artifacts/ConceptDiagramArtifact'

interface Props {
  artifact: ActiveArtifactSession
  onClose: () => void
}

function artifactLabel(kind: ActiveArtifactSession['artifactType']): string {
  return kind === 'flashcards' ? 'Flashcards' : 'Concept Map'
}

export function LearningArtifactPanel({ artifact, onClose }: Props) {
  const label = artifactLabel(artifact.artifactType)
  const shortTopic = artifact.topic.length > 28 ? `${artifact.topic.slice(0, 27)}…` : artifact.topic

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
        <span
          className="font-mono text-[9px] tracking-[0.12em] uppercase flex-1 text-center"
          style={{ color: 'rgba(252,250,242,0.52)', marginLeft: -18 }}
        >
          {label} — {shortTopic}
        </span>
        <button onClick={onClose} className="opacity-30 hover:opacity-60 transition-opacity">
          <X size={10} style={{ color: 'rgba(252,250,242,0.8)' }} />
        </button>
      </div>

      <div className="px-4 py-3 border-b" style={{ borderColor: 'rgba(45,42,38,0.08)', background: 'rgba(45,42,38,0.03)' }}>
        <div className="flex items-center gap-2 flex-wrap">
          <span className="font-deva text-[15px]" style={{ color: 'var(--marigold)', fontFamily: 'var(--font-deva)' }}>अभ्यास</span>
          <span className="text-[13px] font-semibold" style={{ color: 'var(--kajal)' }}>
            {artifact.topic}
          </span>
        </div>
        <p className="mt-1 text-[12px] leading-relaxed" style={{ color: 'rgba(45,42,38,0.66)' }}>
          This artifact is native to Narad. Keep refining it from the main chat with explicit edit prompts.
        </p>
        <div className="mt-2 flex flex-wrap gap-2 font-mono text-[10px]" style={{ color: 'rgba(45,42,38,0.46)' }}>
          <span>version {artifact.version}</span>
          <span>workspace: {artifact.workspaceId}</span>
          {artifact.recordIds.length > 0 && <span>records: {artifact.recordIds.join(', ')}</span>}
        </div>
      </div>

      <div style={{ flex: '1 1 0', minHeight: 0, overflow: 'hidden' }}>
        {artifact.artifactType === 'flashcards' ? (
          <FlashcardArtifact topic={artifact.topic} doc={artifact.doc} />
        ) : (
          <ConceptDiagramArtifact topic={artifact.topic} doc={artifact.doc} />
        )}
      </div>
    </div>
  )
}
