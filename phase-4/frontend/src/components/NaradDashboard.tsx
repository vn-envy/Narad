import { useEffect, useState, type ReactNode } from 'react'
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
import { MadhubaniBorder } from './MadhubaniBorder'
import { KunjiTab } from './KunjiTab'
import { WorkflowPathsPanel } from './WorkflowPathsPanel'
import { ProfileBadge } from './ProfileBadge'

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
}

const SURFACE_META: Record<DashboardSurface, {
  eyebrow: string
  label: string
  description: string
}> = {
  workspaces: {
    eyebrow: 'कर्म',
    label: 'Workflows',
    description: 'Purpose-built paths that retain progress, evidence, and the next useful action.',
  },
  memory: {
    eyebrow: 'स्मृति',
    label: 'Memory',
    description: 'Recall, commitments, and provenance from work Narad has already done with you.',
  },
  system: {
    eyebrow: 'दृष्टि',
    label: 'System',
    description: 'Runtime health, models, connections, and traces.',
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
      aria-label="Surface sections"
      style={{
        display: 'flex',
        gap: 3,
        padding: 3,
        borderRadius: 10,
        background: 'rgba(45,42,38,0.055)',
        border: '1px solid rgba(45,42,38,0.08)',
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
              padding: '6px 10px',
              border: 0,
              borderRadius: 7,
              background: active ? 'var(--paper)' : 'transparent',
              color: active ? 'var(--kajal)' : 'rgba(45,42,38,0.5)',
              boxShadow: active ? '0 1px 3px rgba(45,42,38,0.09)' : 'none',
              fontSize: 11.5,
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
      aria-label={`${meta.label} surface`}
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
      <MadhubaniBorder height={24} />

      <header
        style={{
          minHeight: 54,
          padding: isMobile ? '9px 12px' : '9px 16px',
          display: 'flex',
          alignItems: 'center',
          gap: 12,
          flexShrink: 0,
          background: 'linear-gradient(180deg, rgba(45,42,38,0.99), rgba(38,35,32,0.99))',
        }}
      >
        <button
          type="button"
          onClick={onClose}
          title="Return to Chat"
          style={{
            border: 0,
            background: 'transparent',
            color: 'var(--sindoor)',
            fontFamily: 'var(--font-hero)',
            fontSize: isMobile ? 14 : 16,
            fontWeight: 700,
            cursor: 'pointer',
            whiteSpace: 'nowrap',
          }}
        >
          NARAD.OS
        </button>

        {!isMobile && (
          <div style={{ flex: 1, maxWidth: 560 }}>
            <SearchBar userId={userId} onNavigate={navigateFromSearch} tone="dark" />
          </div>
        )}

        <ProfileBadge profile={profile} onSwitch={onSwitchProfile} compact={isMobile} />

        <div
          aria-live="polite"
          style={{
            marginLeft: 'auto',
            display: 'flex',
            alignItems: 'center',
            gap: 7,
            padding: isMobile ? 5 : '5px 9px',
            borderRadius: 999,
            border: '1px solid rgba(252,250,242,0.13)',
            color: 'rgba(252,250,242,0.6)',
            fontSize: 10.5,
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
          {!isMobile && (streaming
            ? 'Narad working'
            : capabilities
              ? `${capabilities.build.runtime_mode} · ${capabilities.issue_count} issue${capabilities.issue_count === 1 ? '' : 's'}`
              : 'Connecting')}
        </div>

        <button
          type="button"
          onClick={onClose}
          aria-label="Return to Chat"
          style={{
            minWidth: 34,
            height: 30,
            padding: isMobile ? '0 8px' : '0 11px',
            borderRadius: 8,
            border: '1px solid rgba(252,250,242,0.14)',
            background: 'rgba(252,250,242,0.07)',
            color: 'rgba(252,250,242,0.72)',
            fontSize: 11,
            fontWeight: 600,
            cursor: 'pointer',
          }}
        >
          {isMobile ? '←' : '← Chat'}
        </button>
      </header>

      <div
        style={{
          padding: isMobile ? '10px 12px' : '11px 16px',
          display: 'flex',
          alignItems: isMobile ? 'stretch' : 'center',
          flexDirection: isMobile ? 'column' : 'row',
          gap: isMobile ? 9 : 16,
          flexShrink: 0,
          borderBottom: '1px solid rgba(45,42,38,0.08)',
          background: 'linear-gradient(180deg, rgba(252,250,242,0.98), rgba(247,242,230,0.9))',
        }}
      >
        <div style={{ minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
            <span style={{ fontFamily: 'var(--font-deva)', fontSize: 13, color: 'var(--sindoor)' }}>{meta.eyebrow}</span>
            <h1 style={{ fontFamily: 'var(--font-hero)', fontSize: 20, lineHeight: 1, color: 'var(--kajal)' }}>{meta.label}</h1>
          </div>
          {!isMobile && (
            <p style={{ marginTop: 4, fontSize: 11.5, color: 'rgba(45,42,38,0.5)' }}>{meta.description}</p>
          )}
        </div>

        {surface === 'system' && (
          <div style={{ marginLeft: isMobile ? 0 : 'auto' }}>
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
      </div>

      {surface === 'system' && andonAlert && (
        <div
          style={{
            flexShrink: 0,
            padding: '8px 16px',
            background: 'rgba(194,65,12,0.07)',
            borderBottom: '1px solid rgba(194,65,12,0.14)',
            color: 'rgba(45,42,38,0.68)',
            fontSize: 11.5,
          }}
        >
          <strong style={{ color: 'var(--kesari)' }}>Needs attention · {andonAlert.avatar}</strong>
          <span style={{ marginLeft: 8 }}>{andonAlert.trigger}</span>
        </div>
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

      <MadhubaniBorder position="bottom" height={22} />
    </main>
  )
}
