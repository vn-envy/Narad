import { useEffect, useState, type ReactNode } from 'react'
import { ArrowLeft } from 'lucide-react'
import { useIsMobile } from '../hooks/useIsMobile'
import type {
  AndonAlertPayload,
  AvatarName,
  AvatarStatus,
  SessionInfo,
  StepEvent,
} from '../hooks/useAvatara'
import type { FamilyProfile, RuntimeCapabilities, WorkflowRun } from '@/lib/api'
import type { AppSurface, DashboardSurface } from '@/lib/surfaces'
import { SearchBar } from './SearchBar'
import { TracesTab } from './TracesTab'
import { MemoryTab } from './MemoryTab'
import { ObservabilityDeck } from './ObservabilityDeck'
import { KunjiTab } from './KunjiTab'
import { WorkflowPathsPanel } from './WorkflowPathsPanel'
import { ProfileBadge } from './ProfileBadge'
import { YouPanel } from './YouPanel'
import { ActivityPanel } from './ActivityPanel'
import { Bindu } from './pulli'

type SystemSection = 'status' | 'trace' | 'models'

interface Props {
  surface: DashboardSurface
  onSurfaceChange: (surface: AppSurface) => void
  onClose: () => void
  avatars: Record<AvatarName, AvatarStatus>
  naradActive: boolean
  streaming: boolean
  stepEvents: StepEvent[]
  sessionTotals: { promptTokens: number; completionTokens: number; totalTokens: number; costUsd: number }
  currentSession: SessionInfo | null
  userId: string
  profile: FamilyProfile
  onSwitchProfile: () => void
  andonAlert: AndonAlertPayload | null
  capabilities: RuntimeCapabilities | null
  activeWorkflowRunId?: string | null
  onContinueWorkflow: (run: WorkflowRun, prompt: string) => void
  onOpenSetup: () => void
  /** Activity item to highlight (a notification tap). */
  focusEventId?: string | null
  /** Open a same-origin deep link such as "/?approval=<id>". */
  onOpenUrl: (url: string) => void
  /** Open one path on the Paths surface. */
  onOpenRun: (runId: string) => void
  onUnreadChange: (count: number) => void
  /** Start voice mode (from You). */
  onOpenVoice: () => void
}

const SURFACE_META: Record<DashboardSurface, {
  eyebrow: string
  label: string
  description: string
}> = {
  activity: {
    eyebrow: 'सूचना',
    label: 'Activity',
    description: 'What needs you, what is running, and what Narad has finished.',
  },
  workspaces: {
    eyebrow: 'कर्म',
    label: 'Paths',
    description: 'Purpose-built paths that retain progress, evidence, and the next useful action.',
  },
  you: {
    eyebrow: 'आप',
    label: 'You',
    description: 'Your notifications, voice, privacy and sign-in.',
  },
  memory: {
    eyebrow: 'स्मृति',
    label: 'Memory',
    description: 'Recall, commitments, and provenance from work Narad has already done with you.',
  },
  system: {
    eyebrow: 'दृष्टि',
    label: 'System',
    description: 'Runtime health, models, connections and traces.',
  },
}

function SectionNav<T extends string>({
  value,
  items,
  onChange,
}: {
  value: T
  items: Array<{ id: T; label: string }>
  onChange: (value: T) => void
}) {
  return (
    <div
      role="tablist"
      aria-label="Sections"
      style={{
        display: 'flex',
        gap: 3,
        padding: 3,
        borderRadius: 12,
        background: 'var(--ink-05)',
        border: '1px solid var(--ink-08)',
        overflowX: 'auto',
      }}
    >
      {items.map(item => {
        const active = item.id === value
        return (
          <button
            key={item.id}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => onChange(item.id)}
            style={{
              flex: '1 0 auto',
              minHeight: 44,
              padding: '0 14px',
              border: 0,
              borderRadius: 9,
              background: active ? 'var(--paper)' : 'transparent',
              color: active ? 'var(--kajal)' : 'var(--ink-55)',
              boxShadow: active ? '0 1px 3px var(--ink-12)' : 'none',
              fontSize: 13.5,
              fontWeight: active ? 700 : 500,
              whiteSpace: 'nowrap',
              cursor: 'pointer',
            }}
          >
            {item.label}
          </button>
        )
      })}
    </div>
  )
}

function SurfaceFrame({ children }: { children: ReactNode }) {
  return (
    <div style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
      {children}
    </div>
  )
}

/** A phone screen's top bar: the same frosted chrome as Chat's, with Bindu
 *  and the dot wordmark. The screen's own title sits in its content. Memory
 *  and System are opened from You, so they get Back and their name instead. */
function PhoneHeader({ surface, onBack, working }: { surface: DashboardSurface; onBack?: () => void; working: boolean }) {
  const meta = SURFACE_META[surface]
  return (
    <header className="screen-header chrome-frost relative overflow-hidden" style={onBack ? { paddingLeft: 4 } : undefined}>
      {onBack && (
        <button type="button" className="n-icon-btn" onClick={onBack} aria-label="Back to You">
          <ArrowLeft size={20} />
        </button>
      )}
      <Bindu mood={working ? 'thinking' : 'calm'} size={40} />
      <span className="font-dot" style={{ fontSize: 23, lineHeight: 1, color: 'var(--on-chrome)' }}>
        {onBack ? meta.label.toUpperCase() : 'NARAD'}
      </span>
    </header>
  )
}

export function NaradDashboard({
  surface,
  onSurfaceChange,
  onClose,
  avatars,
  naradActive,
  streaming,
  stepEvents,
  sessionTotals,
  currentSession,
  userId,
  profile,
  onSwitchProfile,
  andonAlert,
  capabilities,
  activeWorkflowRunId,
  onContinueWorkflow,
  onOpenSetup,
  focusEventId,
  onOpenUrl,
  onOpenRun,
  onUnreadChange,
  onOpenVoice,
}: Props) {
  const [systemSection, setSystemSection] = useState<SystemSection>('status')
  const isMobile = useIsMobile()
  const meta = SURFACE_META[surface]

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [onClose])

  const navigateFromSearch = (destination: string) => {
    if (destination === 'memory' || destination === 'sutra' || destination === 'sutras') {
      onSurfaceChange('memory')
      return
    }
    if (destination === 'workflow' || destination === 'workflows') {
      onSurfaceChange('workspaces')
      return
    }
    setSystemSection(destination === 'audit' || destination === 'session' ? 'trace' : 'status')
    onSurfaceChange('system')
  }

  return (
    <main
      aria-label={meta.label}
      style={{
        height: '100%',
        minHeight: 0,
        overflow: 'hidden',
        background: 'var(--paper)',
        display: 'flex',
        flexDirection: 'column',
        fontFamily: 'var(--font-body)',
      }}
    >
      {isMobile ? (
        <PhoneHeader
          surface={surface}
          working={naradActive || streaming}
          onBack={surface === 'memory' || surface === 'system' ? () => onSurfaceChange('you') : undefined}
        />
      ) : (
        <>
          <header
            style={{
              minHeight: 54,
              padding: '9px 16px',
              display: 'flex',
              alignItems: 'center',
              gap: 12,
              flexShrink: 0,
              background: 'var(--chrome)',
            }}
          >
            <button
              type="button"
              onClick={onClose}
              title="Return to Chat"
              style={{
                border: 0,
                background: 'transparent',
                color: '#e8773f',
                fontFamily: 'var(--font-hero)',
                fontSize: 16,
                fontWeight: 700,
                cursor: 'pointer',
                whiteSpace: 'nowrap',
                minHeight: 44,
              }}
            >
              NARAD.OS
            </button>

            <div style={{ flex: 1, maxWidth: 560 }}>
              <SearchBar userId={userId} onNavigate={navigateFromSearch} tone="dark" />
            </div>

            <ProfileBadge profile={profile} onSwitch={onSwitchProfile} />

            <div
              aria-live="polite"
              style={{
                marginLeft: 'auto',
                display: 'flex',
                alignItems: 'center',
                gap: 7,
                padding: '5px 9px',
                borderRadius: 999,
                border: '1px solid rgba(var(--rgb-on-chrome),0.13)',
                color: 'rgba(var(--rgb-on-chrome),0.66)',
                fontSize: 11.5,
                whiteSpace: 'nowrap',
              }}
            >
              <span
                style={{
                  width: 7,
                  height: 7,
                  borderRadius: 999,
                  background: naradActive || streaming ? 'var(--sindoor)' : capabilities?.status === 'healthy' ? 'var(--tulsi)' : 'var(--haldi)',
                  boxShadow: naradActive || streaming ? '0 0 0 3px rgba(194,65,12,0.2)' : 'none',
                }}
              />
              {streaming
                ? 'Narad working'
                : capabilities
                  ? `${capabilities.build.runtime_mode} · ${capabilities.issue_count} issue${capabilities.issue_count === 1 ? '' : 's'}`
                  : 'Connecting'}
            </div>

            <button
              type="button"
              onClick={onClose}
              style={{
                minHeight: 40,
                padding: '0 12px',
                borderRadius: 8,
                border: '1px solid rgba(var(--rgb-on-chrome),0.14)',
                background: 'rgba(var(--rgb-on-chrome),0.07)',
                color: 'rgba(var(--rgb-on-chrome),0.78)',
                fontSize: 12,
                fontWeight: 600,
                cursor: 'pointer',
              }}
            >
              Back to Chat
            </button>
          </header>

          <div
            style={{
              padding: '11px 16px',
              display: 'flex',
              alignItems: 'center',
              gap: 16,
              flexShrink: 0,
              borderBottom: '1px solid var(--line)',
              background: 'var(--surface-2)',
            }}
          >
            <div style={{ minWidth: 0 }}>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
                <span style={{ fontFamily: 'var(--font-deva)', fontSize: 14, color: 'var(--sindoor)' }}>{meta.eyebrow}</span>
                <h1 style={{ fontFamily: 'var(--font-hero)', fontSize: 21, lineHeight: 1, color: 'var(--kajal)' }}>{meta.label}</h1>
              </div>
              <p style={{ marginTop: 4, fontSize: 12.5, color: 'var(--ink-55)' }}>{meta.description}</p>
            </div>
          </div>
        </>
      )}

      {surface === 'system' && (
        <div style={{ padding: isMobile ? '10px 12px' : '10px 16px', flexShrink: 0, borderBottom: '1px solid var(--line)' }}>
          <SectionNav
            value={systemSection}
            onChange={setSystemSection}
            items={[
              { id: 'status', label: 'Status' },
              { id: 'trace', label: 'Trace' },
              { id: 'models', label: 'Connections' },
            ]}
          />
        </div>
      )}

      {surface === 'system' && andonAlert && (
        <div
          role="status"
          style={{
            flexShrink: 0,
            padding: '8px 16px',
            background: 'rgba(var(--rgb-sindoor),0.07)',
            borderBottom: '1px solid rgba(var(--rgb-sindoor),0.14)',
            color: 'var(--ink-70)',
            fontSize: 13,
          }}
        >
          <strong style={{ color: 'var(--kesari)' }}>Needs attention · {andonAlert.avatar}</strong>
          <span style={{ marginLeft: 8 }}>{andonAlert.trigger}</span>
        </div>
      )}

      {surface === 'activity' && (
        <SurfaceFrame>
          <ActivityPanel
            userId={userId}
            focusEventId={focusEventId}
            onOpenUrl={onOpenUrl}
            onOpenRun={onOpenRun}
            onUnreadChange={onUnreadChange}
          />
        </SurfaceFrame>
      )}

      {surface === 'workspaces' && (
        <SurfaceFrame>
          <WorkflowPathsPanel
            userId={userId}
            streaming={streaming}
            activeRunId={activeWorkflowRunId}
            onContinue={onContinueWorkflow}
          />
        </SurfaceFrame>
      )}

      {surface === 'you' && (
        <SurfaceFrame>
          <YouPanel
            profile={profile}
            onSignOut={onSwitchProfile}
            onOpenVoice={onOpenVoice}
            onOpenSurface={onSurfaceChange}
          />
        </SurfaceFrame>
      )}

      {surface === 'memory' && (
        <SurfaceFrame>
          <MemoryTab userId={userId} />
        </SurfaceFrame>
      )}

      {surface === 'system' && (
        <SurfaceFrame>
          {systemSection === 'status' && (
            <ObservabilityDeck
              open
              avatars={avatars}
              currentSession={currentSession}
              stepEvents={stepEvents}
              sessionTotals={sessionTotals}
              capabilities={capabilities}
              metricsOnly
            />
          )}
          {systemSection === 'trace' && (
            <TracesTab
              currentSession={currentSession}
              stepEvents={stepEvents}
              sessionTotals={sessionTotals}
              userId={userId}
            />
          )}
          {systemSection === 'models' && <KunjiTab onOpenSetup={onOpenSetup} />}
        </SurfaceFrame>
      )}

    </main>
  )
}
