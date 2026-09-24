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

// A path binds to one chat thread, never to the device: a message carries
// workflow_run_id only inside the thread the path was continued in.
interface WorkflowBinding {
  runId: string
  sessionId: string | null
}

function loadWorkflowBinding(key: string): WorkflowBinding | null {
  try {
    const stored = JSON.parse(localStorage.getItem(key) || 'null') as Partial<WorkflowBinding> | null
    if (!stored?.runId) return null
    return { runId: String(stored.runId), sessionId: stored.sessionId ? String(stored.sessionId) : null }
  } catch {
    // Storage is optional; a bare run id from the old device-wide binding is dropped.
    return null
  }
}

function storeWorkflowBinding(key: string, binding: WorkflowBinding | null) {
  try {
    if (binding) localStorage.setItem(key, JSON.stringify(binding))
    else localStorage.removeItem(key)
  } catch { /* storage is optional */ }
}

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
    andonAlert, clearSession, resumeSession,
    guidedSession, answerGuided, skipGuided, exitGuided,
  } = useAvatara(userId)

  const [activeSurface, setActiveSurface] = useState<AppSurface>('chat')
  const [voiceOpen, setVoiceOpen] = useState(false)
  const [capabilities, setCapabilities] = useState<RuntimeCapabilities | null>(null)
  const [activeWorkflow, setActiveWorkflow] = useState<WorkflowRun | null>(null)
  const [workflowBinding, setWorkflowBinding] = useState<WorkflowBinding | null>(() => loadWorkflowBinding(activeWorkflowKey))
  const [pendingContinue, setPendingContinue] = useState<{ runId: string; prompt: string; sessionId: string } | null>(null)
  const [onboardingStatus, setOnboardingStatus] = useState<OnboardingStatus | null>(null)
  const [setupOpen, setSetupOpen] = useState(false)
  const isMobile = useIsMobile()
  // The chat thread the next message goes to (null for a fresh, unsent thread).
  const threadSessionId = currentSession?.sessionId
    ?? [...messages].reverse().find(message => message.sessionId)?.sessionId
    ?? null
  const bindingRunId = workflowBinding?.runId ?? null
  // The bound path applies only inside its own thread.
  const threadWorkflowRunId = workflowBinding && (!workflowBinding.sessionId || workflowBinding.sessionId === threadSessionId)
    ? workflowBinding.runId
    : null

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
    if (!bindingRunId) return
    apiFetch(apiUrl(`/workflow-runs/${bindingRunId}`, { user_id: userId }))
      .then(response => response.ok ? response.json() as Promise<WorkflowRun> : null)
      .then(run => {
        if (!cancelled && run) setActiveWorkflow(run)
      })
      .catch(() => {})
    return () => { cancelled = true }
  }, [bindingRunId, userId])

  // A path continued in a fresh thread adopts that thread once its id is known.
  useEffect(() => {
    if (!workflowBinding || workflowBinding.sessionId || !threadSessionId) return
    const adopted = { runId: workflowBinding.runId, sessionId: threadSessionId }
    setWorkflowBinding(adopted)
    storeWorkflowBinding(activeWorkflowKey, adopted)
  }, [workflowBinding, threadSessionId, activeWorkflowKey])

  useEffect(() => {
    const handler = (event: Event) => {
      // Runtime events refresh the bound path; they never bind a new one.
      if (!bindingRunId) return
      const detail = (event as CustomEvent<{ data?: { workflow_run_id?: string; run?: WorkflowRun } }>).detail
      const run = detail?.data?.run
      if (run) {
        if (run.run_id === bindingRunId) setActiveWorkflow(run)
        return
      }
      if (detail?.data?.workflow_run_id !== bindingRunId) return
      apiFetch(apiUrl(`/workflow-runs/${bindingRunId}`, { user_id: userId }))
        .then(response => response.ok ? response.json() as Promise<WorkflowRun> : null)
        .then(next => { if (next) setActiveWorkflow(next) })
        .catch(() => {})
    }
    window.addEventListener('narad:workflow-event', handler)
    return () => window.removeEventListener('narad:workflow-event', handler)
  }, [bindingRunId, userId])

  // Send a continuation only once the path's own thread has been restored.
  useEffect(() => {
    if (!pendingContinue || streaming || pendingContinue.sessionId !== threadSessionId) return
    setPendingContinue(null)
    void send(pendingContinue.prompt, [], { workflowRunId: pendingContinue.runId })
  }, [pendingContinue, send, streaming, threadSessionId])

  const rememberWorkflow = (binding: WorkflowBinding | null, run: WorkflowRun | null = null) => {
    setWorkflowBinding(binding)
    setActiveWorkflow(run)
    storeWorkflowBinding(activeWorkflowKey, binding)
  }

  const sendInContext = (query: string, attachments: ChatAttachment[] = []) => {
    void send(query, attachments, { workflowRunId: threadWorkflowRunId })
  }

  const startNewChat = () => {
    clearSession()
    rememberWorkflow(null)
  }

  const continueWorkflow = async (run: WorkflowRun, prompt: string) => {
    setActiveSurface('chat')
    const nextPrompt = run.workflow_id === 'teach' && run.current_stage_id === 'diagnostic'
      ? `/teach me ${String(run.inputs.topic || run.title)}`
      : prompt
    // A path keeps living in the thread it was continued in: reopen that thread
    // instead of pulling the path into whichever conversation is open.
    if (run.session_id && run.session_id !== threadSessionId && await resumeSession(run.session_id)) {
      rememberWorkflow({ runId: run.run_id, sessionId: run.session_id }, run)
      setPendingContinue({ runId: run.run_id, prompt: nextPrompt, sessionId: run.session_id })
      return
    }
    rememberWorkflow({ runId: run.run_id, sessionId: threadSessionId }, run)
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
              onClear={startNewChat}
              onOpenVoice={() => setVoiceOpen(true)}
              activeArtifact={activeArtifactSession}
              onCloseArtifact={clearArtifact}
              activeWorkflow={threadWorkflowRunId && activeWorkflow?.run_id === threadWorkflowRunId ? activeWorkflow : null}
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
            onSend={(query, replyLanguage) =>
              void send(query, [], { workflowRunId: threadWorkflowRunId, replyLanguage })}
          />
        </Suspense>
      )}

      <Toaster />
    </>
  )
}
