import { useState } from 'react'
import { X, ChevronDown, ChevronUp } from 'lucide-react'
import { CopilotKit } from '@copilotkit/react-core'
import { CopilotChat } from '@copilotkit/react-ui'
import '@copilotkit/react-ui/styles.css'
import { FlashcardArtifact } from './artifacts/FlashcardArtifact'
import { ConceptDiagramArtifact } from './artifacts/ConceptDiagramArtifact'

interface Props {
  topic: string
  artifactType: 'flashcards' | 'diagram'
  onClose: () => void
}

export function LearningArtifactPanel({ topic, artifactType, onClose }: Props) {
  const [chatOpen, setChatOpen] = useState(false)

  const label = artifactType === 'flashcards' ? 'Flashcards' : 'Concept Map'
  const shortTopic = topic.length > 22 ? topic.slice(0, 21) + '…' : topic

  return (
    <CopilotKit runtimeUrl="http://localhost:8123/copilotkit">
      <div className="flex flex-col h-full overflow-hidden" style={{ background: 'var(--paper)' }}>

        {/* ── Title bar ── */}
        <div
          className="flex items-center gap-2 px-3 flex-shrink-0"
          style={{
            height: 32,
            background: 'var(--kajal)',
            borderBottom: '1px solid rgba(252,250,242,0.07)',
          }}
        >
          {/* macOS-style close dot */}
          <button
            onClick={onClose}
            className="w-3 h-3 rounded-full flex-shrink-0 hover:opacity-80 transition-opacity"
            style={{ background: '#ff5f57' }}
            title="Close"
          />

          <span className="font-mono text-[9px] tracking-[0.12em] uppercase flex-1 text-center"
            style={{ color: 'rgba(252,250,242,0.50)', marginLeft: -18 }}>
            {label} — {shortTopic}
          </span>

          <button onClick={onClose} className="opacity-30 hover:opacity-60 transition-opacity">
            <X size={10} style={{ color: 'rgba(252,250,242,0.8)' }} />
          </button>
        </div>

        {/* ── Artifact content ── */}
        <div style={{ flex: '1 1 0', minHeight: 0, overflow: 'hidden' }}>
          {artifactType === 'flashcards'
            ? <FlashcardArtifact topic={topic} />
            : <ConceptDiagramArtifact topic={topic} />
          }
        </div>

        {/* ── Ask Krishna section ── */}
        <div
          className="flex-shrink-0"
          style={{ borderTop: '1px solid color-mix(in srgb, var(--kajal) 10%, transparent)' }}
        >
          {/* Collapse bar */}
          <button
            onClick={() => setChatOpen(o => !o)}
            className="flex items-center gap-1.5 w-full px-3"
            style={{
              height: 26,
              background: chatOpen ? 'var(--kajal)' : 'var(--speckle)',
              borderBottom: chatOpen
                ? '1px solid color-mix(in srgb, var(--kajal) 10%, transparent)'
                : 'none',
            }}
          >
            {chatOpen
              ? <ChevronUp size={10} style={{ color: 'rgba(252,250,242,0.55)' }} />
              : <ChevronDown size={10} style={{ color: 'rgba(45,42,38,0.45)' }} />
            }
            <span
              className="font-mono text-[9px] tracking-[0.10em] uppercase"
              style={{ color: chatOpen ? 'rgba(252,250,242,0.55)' : 'rgba(45,42,38,0.50)' }}
            >
              Ask Krishna
            </span>
          </button>

          {chatOpen && (
            <div style={{ height: 220, overflow: 'hidden' }}>
              <CopilotChat
                instructions={`You are Krishna, a master teacher in guru mode. The learner is studying: "${topic}". Answer concisely, referencing the ${artifactType === 'flashcards' ? 'flashcards' : 'concept diagram'} when helpful. Use the addFlashcard or addConcept actions if the learner asks you to add content to the artifact.`}
                labels={{
                  title: '',
                  placeholder: `Ask about ${shortTopic}…`,
                  stopGenerating: 'Stop',
                  regenerateResponse: 'Retry',
                }}
                className="copilot-narad"
              />
            </div>
          )}
        </div>

      </div>
    </CopilotKit>
  )
}
