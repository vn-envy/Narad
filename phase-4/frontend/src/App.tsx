import { lazy, Suspense, useEffect, useState } from 'react'
import { useAvatara } from './hooks/useAvatara'
import { useIsMobile } from './hooks/useIsMobile'
import { ChatPanel }            from './components/ChatPanel'
import { AwarenessBar }         from './components/AwarenessBar'
import { NaradDashboard }      from './components/NaradDashboard'
import { apiFetch, type RuntimeCapabilities } from './lib/api'
import { TooltipProvider }      from '@/components/ui/tooltip'
import { Toaster }              from '@/components/ui/sonner'
import './index.css'

const LearningArtifactPanel = lazy(async () => {
  const mod = await import('./components/LearningArtifactPanel')
  return { default: mod.LearningArtifactPanel }
})

const ToolWorkspacePanel = lazy(async () => {
  const mod = await import('./components/ToolWorkspacePanel')
  return { default: mod.ToolWorkspacePanel }
})

const USER_ID = 'default'

export default function App() {
  const {
    messages, avatars, naradActive, streaming, error,
    currentSession, send, stop, stepEvents, sessionTotals,
    activeArtifactSession, clearArtifact,
    pendingToolUi, clearToolUi,
    kanbanUpdate, andonAlert, clearSession, resumeSession,
  } = useAvatara(USER_ID)

  const [darshanOpen, setDarshanOpen] = useState(false)
  const [capabilities, setCapabilities] = useState<RuntimeCapabilities | null>(null)
  const isMobile = useIsMobile()

  useEffect(() => {
    let cancelled = false
    apiFetch('/capabilities')
      .then(res => res.ok ? res.json() : null)
      .then(data => {
        if (!cancelled) setCapabilities(data)
      })
      .catch(() => {
        if (!cancelled) setCapabilities(null)
      })
    return () => { cancelled = true }
  }, [])

  const activeSteps = Object.values(avatars).filter(a => a.state === 'active').length

  return (
    <TooltipProvider>
      {/* Noise texture overlay */}
      <div className="noise-overlay" />

      <div
        className="grid h-screen overflow-hidden"
        style={{
          ...(isMobile
            ? {
                // Phone: chat on top, AwarenessBar as bottom bar. The side
                // panel becomes a full-screen sheet (rendered below).
                gridTemplateRows: 'minmax(0,1fr) auto',
                gridTemplateColumns: '1fr',
              }
            : {
                gridTemplateColumns: activeArtifactSession || pendingToolUi
                  ? 'minmax(0,1fr) minmax(360px, 440px) 72px'
                  : '1fr 72px',
              }),
          position: 'relative',
          zIndex: 1,
        }}
      >
        <div className="flex flex-col h-full overflow-hidden">
          <ChatPanel
            messages={messages}
            avatars={avatars}
            streaming={streaming}
            error={error}
            onSend={send}
            stop={stop}
            onClear={clearSession}
            activeArtifact={activeArtifactSession}
            onCloseArtifact={clearArtifact}
          />
        </div>

        {(activeArtifactSession || pendingToolUi) && (
          <div
            className={isMobile ? 'overflow-hidden' : 'h-full overflow-hidden border-l'}
            style={
              isMobile
                ? { position: 'fixed', inset: 0, zIndex: 40, background: 'var(--paper)' }
                : { borderColor: 'color-mix(in srgb, var(--kajal) 10%, transparent)', background: 'var(--paper)' }
            }
          >
            <Suspense
              fallback={
                <div
                  className="flex h-full items-center justify-center text-sm"
                  style={{ color: 'rgba(45,42,38,0.55)', background: 'var(--paper)' }}
                >
                  Loading workspace…
                </div>
              }
            >
              {activeArtifactSession ? (
                <LearningArtifactPanel
                  artifact={activeArtifactSession}
                  onClose={clearArtifact}
                />
              ) : pendingToolUi ? (
                <ToolWorkspacePanel
                  toolUi={pendingToolUi}
                  onClose={clearToolUi}
                />
              ) : null}
            </Suspense>
          </div>
        )}

        {/* AwarenessBar — right rail on desktop, bottom bar on phone */}
        <AwarenessBar
          avatars={avatars}
          activeSteps={activeSteps}
          onOpenDarshan={() => setDarshanOpen(true)}
          horizontal={isMobile}
        />
      </div>

      {/* Narad Dashboard — unified cultural-core workspace overlay */}
      <NaradDashboard
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
        capabilities={capabilities}
        onResumeSession={resumeSession}
      />

      <Toaster />
    </TooltipProvider>
  )
}
