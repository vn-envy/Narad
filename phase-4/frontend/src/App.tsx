import { lazy, Suspense, useEffect, useState } from 'react'
import { useAvatara } from './hooks/useAvatara'
import { useIsMobile } from './hooks/useIsMobile'
import { ChatPanel }            from './components/ChatPanel'
import { AwarenessBar }         from './components/AwarenessBar'
import { FamilyProfileGate }    from './components/FamilyProfileGate'
import { OnboardingFlow }       from './components/OnboardingFlow'
import {
  apiFetch,
  apiUrl,
  clearProfileSession,
  getProfileSession,
  setProfileSession,
  type FamilyProfile,
  type FamilyProfileSession,
  type OnboardingStatus,
  type RuntimeCapabilities,
  type WorkflowRun,
} from './lib/api'
import type { ChatAttachment } from './hooks/useAvatara'
import type { AppSurface, DashboardSurface } from './lib/surfaces'
import { Toaster }              from '@/components/ui/sonner'
import './index.css'

const LearningArtifactPanel = lazy(async () => {
  const mod = await import('./components/LearningArtifactPanel')
  return { default: mod.LearningArtifactPanel }
})

const NaradDashboard = lazy(async () => {
  const mod = await import('./components/NaradDashboard')
  return { default: mod.NaradDashboard }
})

const ToolWorkspacePanel = lazy(async () => {
  const mod = await import('./components/ToolWorkspacePanel')
  return { default: mod.ToolWorkspacePanel }
})

const VoiceMode = lazy(async () => {
  const mod = await import('./components/VoiceMode')
  return { default: mod.VoiceMode }
})

const ACTIVE_WORKFLOW_KEY = 'narad_active_workflow_run'

export default function App() {
  const [profileSession, setActiveProfileSession] = useState<FamilyProfileSession | null>(() => getProfileSession())
  const [checkingSession, setCheckingSession] = useState(Boolean(profileSession))

  useEffect(() => {
    if (!profileSession) {
      setCheckingSession(false)
      return
    }
    let cancelled = false
    apiFetch('/profiles/session')
      .then(async response => {
        if (!response.ok) throw new Error('Profile session expired')
        return response.json() as Promise<{ profile: FamilyProfile }>
      })
      .then(({ profile }) => {
        if (cancelled) return
        const refreshed = { ...profileSession, profile }
        setProfileSession(refreshed)
        setActiveProfileSession(refreshed)
      })
      .catch(() => {
        if (cancelled) return
        clearProfileSession()
        setActiveProfileSession(null)
      })
      .finally(() => {
        if (!cancelled) setCheckingSession(false)
      })
    return () => { cancelled = true }
  // Validate the persisted token once; profile switching remounts this shell.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  if (checkingSession) {
    return (
      <main className="family-gate" aria-label="Opening your private Narad profile">
        <div className="family-gate-glow family-gate-glow-one" />
        <div className="family-session-loading">
          <span className="family-session-pulse" />
          Opening your Narad
        </div>
      </main>
    )
  }

  if (!profileSession) {
    return <FamilyProfileGate onAuthenticated={setActiveProfileSession} />
  }

  return (
    <NaradSession
      key={profileSession.profile.user_id}
      profile={profileSession.profile}
      onSwitchProfile={() => {
        clearProfileSession()
        setActiveProfileSession(null)
      }}
    />
  )
}

function NaradSession({ profile, onSwitchProfile }: { profile: FamilyProfile; onSwitchProfile: () => void }) {
  const userId = profile.user_id
  const activeWorkflowKey = `${ACTIVE_WORKFLOW_KEY}:${userId}`
  const {
    messages, avatars, naradActive, streaming, error,
    currentSession, send, stop, stepEvents, sessionTotals,
    activeArtifactSession, clearArtifact,
    pendingToolUi, clearToolUi,
    andonAlert, clearSession,
    guidedSession, answerGuided, skipGuided, exitGuided,
  } = useAvatara(userId)

  const [activeSurface, setActiveSurface] = useState<AppSurface>('chat')
  const [voiceOpen, setVoiceOpen] = useState(false)
  const [capabilities, setCapabilities] = useState<RuntimeCapabilities | null>(null)
  const [activeWorkflow, setActiveWorkflow] = useState<WorkflowRun | null>(null)
  const [onboardingStatus, setOnboardingStatus] = useState<OnboardingStatus | null>(null)
  const [setupOpen, setSetupOpen] = useState(false)
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

  useEffect(() => {
    let cancelled = false
    apiFetch(apiUrl('/onboarding', { user_id: userId }))
      .then(response => response.ok ? response.json() as Promise<OnboardingStatus> : null)
      .then(status => {
        if (cancelled || !status) return
        setOnboardingStatus(status)
        setSetupOpen(status.needs_onboarding)
      })
      .catch(() => {
        // Setup must never block an otherwise usable local app.
      })
    return () => { cancelled = true }
  }, [userId])

  useEffect(() => {
    let cancelled = false
    let runId = ''
    try { runId = localStorage.getItem(activeWorkflowKey) || '' } catch { /* storage is optional */ }
    if (!runId) return
    apiFetch(apiUrl(`/workflow-runs/${runId}`, { user_id: userId }))
      .then(response => response.ok ? response.json() as Promise<WorkflowRun> : null)
      .then(run => {
        if (!cancelled && run) setActiveWorkflow(run)
      })
      .catch(() => {})
    return () => { cancelled = true }
  }, [activeWorkflowKey, userId])

  useEffect(() => {
    const handler = (event: Event) => {
      const detail = (event as CustomEvent<{ data?: { workflow_run_id?: string; run?: WorkflowRun } }>).detail
      const run = detail?.data?.run
      if (run) {
        if (!activeWorkflow || run.run_id === activeWorkflow.run_id) {
          setActiveWorkflow(run)
          try { localStorage.setItem(activeWorkflowKey, run.run_id) } catch { /* storage is optional */ }
        }
        return
      }
      const runId = detail?.data?.workflow_run_id
      if (!runId || (activeWorkflow && runId !== activeWorkflow.run_id)) return
      apiFetch(apiUrl(`/workflow-runs/${runId}`, { user_id: userId }))
        .then(response => response.ok ? response.json() as Promise<WorkflowRun> : null)
        .then(next => { if (next) setActiveWorkflow(next) })
        .catch(() => {})
    }
    window.addEventListener('narad:workflow-event', handler)
    return () => window.removeEventListener('narad:workflow-event', handler)
  }, [activeWorkflow, activeWorkflowKey, userId])

  const rememberWorkflow = (run: WorkflowRun | null) => {
    setActiveWorkflow(run)
    try {
      if (run) localStorage.setItem(activeWorkflowKey, run.run_id)
      else localStorage.removeItem(activeWorkflowKey)
    } catch { /* storage is optional */ }
  }

  const sendInContext = (query: string, attachments: ChatAttachment[] = []) => {
    void send(query, attachments, { workflowRunId: activeWorkflow?.run_id })
  }

  const continueWorkflow = (run: WorkflowRun, prompt: string) => {
    rememberWorkflow(run)
    setActiveSurface('chat')
    const nextPrompt = run.workflow_id === 'teach' && run.current_stage_id === 'diagnostic'
      ? `/teach me ${String(run.inputs.topic || run.title)}`
      : prompt
    void send(nextPrompt, [], { workflowRunId: run.run_id })
  }

  const activeSteps = Object.values(avatars).filter(a => a.state === 'active').length
  const chatVisible = activeSurface === 'chat'

  return (
    <>
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
                gridTemplateColumns: chatVisible && (activeArtifactSession || pendingToolUi)
                  ? 'minmax(0,1fr) minmax(360px, 440px) 72px'
                  : '1fr 72px',
              }),
          position: 'relative',
          zIndex: 1,
        }}
      >
        <div className="flex flex-col h-full min-w-0 overflow-hidden">
          {chatVisible ? (
            <ChatPanel
              userId={userId}
              profile={profile}
              onSwitchProfile={onSwitchProfile}
              messages={messages}
              avatars={avatars}
              streaming={streaming}
              error={error}
              onSend={sendInContext}
              stop={stop}
              onClear={clearSession}
              onOpenVoice={() => setVoiceOpen(true)}
              activeArtifact={activeArtifactSession}
              onCloseArtifact={clearArtifact}
              activeWorkflow={activeWorkflow}
              onOpenWorkflow={() => setActiveSurface('workspaces')}
              onLeaveWorkflow={() => rememberWorkflow(null)}
              guidedSession={guidedSession}
              onGuidedAnswer={answerGuided}
              onGuidedSkip={skipGuided}
              onGuidedExit={() => exitGuided()}
            />
          ) : (
            <Suspense fallback={<div className="h-full" style={{ background: 'var(--paper)' }} />}>
              <NaradDashboard
                surface={activeSurface as DashboardSurface}
                onSurfaceChange={setActiveSurface}
                onClose={() => setActiveSurface('chat')}
                avatars={avatars}
                naradActive={naradActive}
                streaming={streaming}
                stepEvents={stepEvents}
                sessionTotals={sessionTotals}
                currentSession={currentSession}
                userId={userId}
                profile={profile}
                onSwitchProfile={onSwitchProfile}
                andonAlert={andonAlert}
                capabilities={capabilities}
                activeWorkflowRunId={activeWorkflow?.run_id}
                onContinueWorkflow={continueWorkflow}
                onOpenSetup={() => setSetupOpen(true)}
              />
            </Suspense>
          )}
        </div>

        {chatVisible && (activeArtifactSession || pendingToolUi) && (
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
          activeSurface={activeSurface}
          onNavigate={setActiveSurface}
          horizontal={isMobile}
        />
      </div>

      {setupOpen && onboardingStatus && (
        <OnboardingFlow
          userId={userId}
          initialStatus={onboardingStatus}
          capabilities={capabilities}
          onFinished={(status, destination) => {
            setOnboardingStatus(status)
            setSetupOpen(false)
            setActiveSurface(destination)
          }}
        />
      )}

      {/* Voice mode — hands-free voice-first overlay */}
      {voiceOpen && (
        <Suspense fallback={null}>
          <VoiceMode
            open={voiceOpen}
            onClose={() => setVoiceOpen(false)}
            messages={messages}
            streaming={streaming}
            onSend={sendInContext}
          />
        </Suspense>
      )}

      <Toaster />
    </>
  )
}
