import { lazy, Suspense, useCallback, useEffect, useState } from 'react'
import { toast } from 'sonner'
import { useAvatara } from './hooks/useAvatara'
import { useIsMobile } from './hooks/useIsMobile'
import { ChatPanel }            from './components/ChatPanel'
import { AwarenessBar }         from './components/AwarenessBar'
import { FamilyProfileGate }    from './components/FamilyProfileGate'
import { HostOfflineBanner, HostOfflineScreen } from './components/HostOffline'
import { OnboardingFlow }       from './components/OnboardingFlow'
import { HostUnreachableError, isUnreachableStatus } from './lib/host-status'
import { disablePush, fetchInbox, setAppBadge, syncPushSubscription } from './lib/notifications'
import { OPEN_URL_EVENT, PUSH_EVENT, type PushPayload } from './lib/pwa'
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

/** Deep links the app handles itself; anything else (e.g. ?approval=) is left in the URL. */
const APP_LINK_PARAMS = ['activity', 'path']

export default function App() {
  const [profileSession, setActiveProfileSession] = useState<FamilyProfileSession | null>(() => getProfileSession())
  const [checkingSession, setCheckingSession] = useState(Boolean(profileSession))
  const [hostOffline, setHostOffline] = useState(false)

  useEffect(() => {
    if (!profileSession) {
      setCheckingSession(false)
      return
    }
    let cancelled = false
    apiFetch('/profiles/session')
      .catch(() => { throw new HostUnreachableError() })
      .then(async response => {
        if (isUnreachableStatus(response.status)) throw new HostUnreachableError()
        if (!response.ok) throw new Error('Profile session expired')
        return response.json() as Promise<{ profile: FamilyProfile }>
      })
      .then(({ profile }) => {
        if (cancelled) return
        const refreshed = { ...profileSession, profile }
        setProfileSession(refreshed)
        setActiveProfileSession(refreshed)
      })
      .catch(error => {
        if (cancelled) return
        // An asleep Mac is not a signed-out person: keep the session.
        if (error instanceof HostUnreachableError) {
          setHostOffline(true)
          return
        }
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

  if (hostOffline) {
    return <HostOfflineScreen />
  }

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
        // Someone else may use this phone next: it stops getting this
        // profile's notifications (quickly, even if the Mac is slow to answer).
        const signedOut = profileSession.profile.user_id
        const forget = Promise.race([
          disablePush(signedOut).catch(() => undefined),
          new Promise(resolve => window.setTimeout(resolve, 1500)),
        ])
        void forget.finally(() => {
          clearProfileSession()
          setActiveProfileSession(null)
        })
      }}
    />
  )
}

/** Read and strip the app's own deep-link params, leaving others (?approval=) in place. */
function takeAppLink(): { activity: string | null; path: string | null } {
  const url = new URL(window.location.href)
  const link = { activity: url.searchParams.get('activity'), path: url.searchParams.get('path') }
  if (APP_LINK_PARAMS.some(name => url.searchParams.has(name))) {
    for (const name of APP_LINK_PARAMS) url.searchParams.delete(name)
    window.history.replaceState(window.history.state, '', url.pathname + url.search + url.hash)
  }
  return link
}

function NaradSession({ profile, onSwitchProfile }: { profile: FamilyProfile; onSwitchProfile: () => void }) {
  const userId = profile.user_id
  const activeWorkflowKey = `${ACTIVE_WORKFLOW_KEY}:${userId}`
  const {
    messages, avatars, naradActive, streaming, liveAnswer, error,
    currentSession, send, stop, stepEvents, sessionTotals,
    activeArtifactSession, clearArtifact,
    pendingToolUi, clearToolUi,
    andonAlert, clearSession, resumeSession,
    guidedSession, answerGuided, skipGuided, exitGuided,
  } = useAvatara(userId)

  const [initialLink] = useState(takeAppLink)
  const [activeSurface, setActiveSurface] = useState<AppSurface>(() =>
    initialLink.activity !== null ? 'activity' : initialLink.path ? 'workspaces' : 'chat')
  const [focusEventId, setFocusEventId] = useState<string | null>(initialLink.activity)
  const [focusRunId, setFocusRunId] = useState<string | null>(initialLink.path)
  const [unread, setUnread] = useState(0)
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

  // ── Activity: unread badge, deep links, pushes while the app is open ──────

  const refreshUnread = useCallback(async () => {
    try {
      setUnread((await fetchInbox(1)).unread)
    } catch {
      // The offline banner covers an unreachable Mac.
    }
  }, [])

  useEffect(() => {
    void refreshUnread()
    void syncPushSubscription(userId)
    const onVisible = () => {
      if (document.visibilityState !== 'visible') return
      void refreshUnread()
      void syncPushSubscription(userId)
    }
    const timer = window.setInterval(() => void refreshUnread(), 60_000)
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [refreshUnread, userId])

  useEffect(() => { setAppBadge(unread) }, [unread])

  const openRun = useCallback((runId: string) => {
    setFocusRunId(runId)
    setActiveSurface('workspaces')
  }, [])

  /** A same-origin deep link: Activity and Paths open in place; anything else
   *  (an approval card) is offered to its handler first, else loaded fresh. */
  const openUrl = useCallback((url: string) => {
    const target = new URL(url, window.location.origin)
    if (target.origin !== window.location.origin) return
    if (target.searchParams.has('activity')) {
      setFocusEventId(target.searchParams.get('activity'))
      setActiveSurface('activity')
      return
    }
    if (target.searchParams.get('path')) {
      openRun(target.searchParams.get('path') as string)
      return
    }
    const link = new CustomEvent('narad:deeplink', { detail: { url: target.pathname + target.search }, cancelable: true })
    if (!window.dispatchEvent(link)) {
      setActiveSurface('chat')
      return
    }
    window.location.assign(target.pathname + target.search + target.hash)
  }, [openRun])

  useEffect(() => {
    const onOpen = (event: Event) => {
      const url = (event as CustomEvent<{ url?: string }>).detail?.url
      if (url) openUrl(url)
    }
    const onPush = (event: Event) => {
      const payload = (event as CustomEvent<PushPayload>).detail
      if (typeof payload?.unread === 'number') setUnread(payload.unread)
      else void refreshUnread()
      if (!payload?.id || document.visibilityState !== 'visible') return
      // The app is open, so the service worker showed no banner: show it here,
      // with the real title from the inbox rather than the lock-screen text.
      fetchInbox(20)
        .then(({ items }) => {
          const item = items.find(entry => entry.id === payload.id)
          toast(item?.title || payload.title, {
            description: (item?.body || payload.body).slice(0, 140),
            action: { label: 'Open', onClick: () => openUrl(payload.url) },
          })
        })
        .catch(() => undefined)
    }
    window.addEventListener(OPEN_URL_EVENT, onOpen)
    window.addEventListener(PUSH_EVENT, onPush)
    return () => {
      window.removeEventListener(OPEN_URL_EVENT, onOpen)
      window.removeEventListener(PUSH_EVENT, onPush)
    }
  }, [openUrl, refreshUnread])

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
      <HostOfflineBanner />

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
              liveAnswer={liveAnswer}
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
                activeWorkflowRunId={focusRunId ?? activeWorkflow?.run_id}
                onContinueWorkflow={continueWorkflow}
                onOpenSetup={() => setSetupOpen(true)}
                focusEventId={focusEventId}
                onOpenUrl={openUrl}
                onOpenRun={openRun}
                onUnreadChange={setUnread}
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
          onNavigate={surface => {
            setFocusEventId(null)
            setFocusRunId(null)
            setActiveSurface(surface)
          }}
          horizontal={isMobile}
          unread={unread}
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
