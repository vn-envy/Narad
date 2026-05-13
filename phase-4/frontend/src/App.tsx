import { useState } from 'react'
import { useAvatara } from './hooks/useAvatara'
import type { KanbanUpdatePayload } from './hooks/useAvatara'
import { ChatPanel }            from './components/ChatPanel'
import { ProjectsPanel }        from './components/ProjectsPanel'
import { LearningArtifactPanel } from './components/LearningArtifactPanel'
import { AwarenessBar }         from './components/AwarenessBar'
import { DarshanDashboard }     from './components/DarshanDashboard'
import { TooltipProvider }      from '@/components/ui/tooltip'
import { Toaster }              from '@/components/ui/sonner'
import './index.css'

const USER_ID = 'default'

export default function App() {
  const {
    messages, avatars, naradActive, streaming, error,
    currentSession, send, stop, stepEvents, sessionTotals,
    pendingArtifact, clearArtifact,
    kanbanUpdate, andonAlert,
  } = useAvatara(USER_ID)

  const [darshanOpen, setDarshanOpen] = useState(false)
  const [leftPanelOpen, setLeftPanelOpen] = useState(true)

  const activeSteps = Object.values(avatars).filter(a => a.state === 'active').length

  return (
    <TooltipProvider>
      {/* Noise texture overlay */}
      <div className="noise-overlay" />

      <div
        className="grid h-screen overflow-hidden"
        style={{
          gridTemplateColumns: leftPanelOpen ? '260px 1fr 72px' : '40px 1fr 72px',
          transition: 'grid-template-columns 0.2s ease',
          position: 'relative',
          zIndex: 1,
        }}
      >
        <ProjectsPanel open={leftPanelOpen} onToggle={() => setLeftPanelOpen(v => !v)} />

        <div className="flex flex-col h-full overflow-hidden">
          <ChatPanel
            messages={messages}
            avatars={avatars}
            streaming={streaming}
            error={error}
            onSend={send}
            stop={stop}
          />

          {/* Learning Artifact panel — inline in chat column */}
          {pendingArtifact && (
            <div style={{ flex: '0 0 320px', minHeight: 0, overflow: 'hidden', borderTop: '1px solid color-mix(in srgb, var(--kajal) 10%, transparent)' }}>
              <LearningArtifactPanel
                topic={pendingArtifact.topic}
                artifactType={pendingArtifact.artifactType}
                onClose={clearArtifact}
              />
            </div>
          )}
        </div>

        {/* Right column — AwarenessBar (72px) */}
        <AwarenessBar
          avatars={avatars}
          totalTokens={sessionTotals.totalTokens}
          activeSteps={activeSteps}
          onOpenDarshan={() => setDarshanOpen(true)}
        />
      </div>

      {/* Darshan Dashboard drawer — mounted at root level */}
      <DarshanDashboard
        open={darshanOpen}
        onClose={() => setDarshanOpen(false)}
        avatars={avatars}
        naradActive={naradActive}
        streaming={streaming}
        messages={messages}
        stepEvents={stepEvents}
        sessionTotals={sessionTotals}
        currentSession={currentSession}
        userId={USER_ID}
        kanbanUpdate={kanbanUpdate}
        andonAlert={andonAlert}
      />

      <Toaster />
    </TooltipProvider>
  )
}
