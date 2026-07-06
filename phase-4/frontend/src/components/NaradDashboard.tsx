import { useEffect, useMemo, useState } from 'react'
import { useIsMobile } from '../hooks/useIsMobile'
import type {
  AndonAlertPayload,
  AvatarName,
  AvatarStatus,
  KanbanUpdatePayload,
  Message,
  SessionInfo,
  StepEvent,
} from '../hooks/useAvatara'
import type { RuntimeCapabilities } from '@/lib/api'
import { apiFetch } from '@/lib/api'
import { SearchBar } from './SearchBar'
import { TracesTab } from './TracesTab'
import { MemoryTab } from './MemoryTab'
import { DarshanPanel } from './DarshanPanel'
import { ObservabilityDeck } from './ObservabilityDeck'
import { MadhubaniBorder } from './MadhubaniBorder'
import { SplitPane } from './ui/split-pane'
import { KarmaWorkspaceTab } from './KarmaWorkspaceTab'
import { TapasyaTab } from './TapasyaTab'
import { AVATAR_ABBREV, AVATAR_COLOURS, AVATAR_NAMES, DEVA } from '@/lib/avatara-constants'

type TabId = 'darshan' | 'karma' | 'smriti' | 'divyadrishti' | 'tapasya'

interface Props {
  open: boolean
  onClose: () => void
  onResumeSession: (sessionId: string) => Promise<boolean>
  avatars: Record<AvatarName, AvatarStatus>
  naradActive: boolean
  streaming: boolean
  messages: Message[]
  stepEvents: StepEvent[]
  sessionTotals: { promptTokens: number; completionTokens: number; totalTokens: number; costUsd: number }
  currentSession: SessionInfo | null
  userId: string
  kanbanUpdate: KanbanUpdatePayload | null
  andonAlert: AndonAlertPayload | null
  capabilities: RuntimeCapabilities | null
}

const TABS: Array<{ id: TabId; label: string; icon: string; sanskrit: string }> = [
  { id: 'darshan', label: 'Darshan', icon: '◉', sanskrit: 'दर्शन' },
  { id: 'karma', label: 'Karma', icon: '◆', sanskrit: 'कर्म' },
  { id: 'smriti', label: 'Smriti', icon: '◎', sanskrit: 'स्मृति' },
  { id: 'divyadrishti', label: 'DivyaDrishti', icon: '◈', sanskrit: 'दिव्यदृष्टि' },
  { id: 'tapasya', label: 'Tapasya', icon: '✦', sanskrit: 'तपस्या' },
]

function headerSummary(tab: TabId): string {
  switch (tab) {
    case 'darshan':
      return 'Live activity and immediate traces stay together so Narad feels present, not buried in separate tools.'
    case 'karma':
      return 'Projects, Karya boards, and the recent record of action live in one operational surface.'
    case 'smriti':
      return 'Retained memories, commitments, provenance, and approved learnings stay in one memory plane.'
    case 'divyadrishti':
      return 'Metrics, runtime health, architecture scorecards, and capability visibility belong here and nowhere else.'
    case 'tapasya':
      return 'Tapas, Swapna, and self-evolution stay in one refinement chamber with explicit review and learning controls.'
  }
}

function sectionRailTitle(tab: TabId): string {
  switch (tab) {
    case 'darshan':
      return 'Expand trace visibility'
    case 'karma':
      return 'Expand Karma record'
    case 'smriti':
      return 'Expand Smriti context'
    case 'divyadrishti':
      return 'Expand metrics visibility'
    case 'tapasya':
      return 'Expand Tapasya visibility'
  }
}

export function NaradDashboard({
  open,
  onClose,
  onResumeSession,
  avatars,
  naradActive,
  streaming,
  messages,
  stepEvents,
  sessionTotals,
  currentSession,
  userId,
  kanbanUpdate,
  andonAlert,
  capabilities,
}: Props) {
  const [activeTab, setActiveTab] = useState<TabId>('darshan')
  const [andonCount, setAndonCount] = useState(0)
  const isMobile = useIsMobile()

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [onClose])

  useEffect(() => {
    if (!open) return
    apiFetch('/andon/stats')
      .then(response => (response.ok ? response.json() : {}))
      .then((data: { total?: number }) => setAndonCount(data.total ?? 0))
      .catch(() => {})
  }, [open, kanbanUpdate])

  const activeMeta = useMemo(() => TABS.find(tab => tab.id === activeTab) ?? TABS[0], [activeTab])
  void onResumeSession
  void messages

  if (!open) return null

  const badgeMap: Partial<Record<TabId, number>> = {
    karma: andonCount > 0 ? andonCount : 0,
    divyadrishti: capabilities?.issue_count ?? 0,
    tapasya: capabilities?.degraded_capability_count ?? 0,
  }

  let primaryContent: React.ReactNode = null
  let secondaryContent: React.ReactNode | undefined
  let defaultRightWidth = 360

  if (activeTab === 'darshan') {
    primaryContent = (
      <div style={{ height: '100%', minHeight: 0, overflow: 'auto' }}>
        <DarshanPanel
          avatars={avatars}
          naradActive={naradActive}
          streaming={streaming}
          currentSession={currentSession}
        />
      </div>
    )
    secondaryContent = (
      <TracesTab
        currentSession={currentSession}
        stepEvents={stepEvents}
        sessionTotals={sessionTotals}
        userId={userId}
      />
    )
    defaultRightWidth = 500
  } else if (activeTab === 'karma') {
    primaryContent = (
      <KarmaWorkspaceTab
        userId={userId}
        currentSession={currentSession}
        streaming={streaming}
      />
    )
    secondaryContent = undefined
  } else if (activeTab === 'smriti') {
    primaryContent = <MemoryTab userId={userId} />
    secondaryContent = undefined
  } else if (activeTab === 'divyadrishti') {
    primaryContent = (
      <ObservabilityDeck
        open={open}
        avatars={avatars}
        currentSession={currentSession}
        stepEvents={stepEvents}
        sessionTotals={sessionTotals}
        capabilities={capabilities}
        metricsOnly
      />
    )
    secondaryContent = undefined
  } else if (activeTab === 'tapasya') {
    primaryContent = <TapasyaTab userId={userId} />
    secondaryContent = undefined
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 50,
        background: 'var(--paper)',
        display: 'flex',
        flexDirection: 'column',
        fontFamily: 'var(--font-body)',
      }}
    >
      <MadhubaniBorder height={32} />

      <div
        style={{
          background: 'linear-gradient(180deg, rgba(45,42,38,0.98) 0%, rgba(38,35,32,0.98) 100%)',
          padding: '10px 16px 8px',
          display: 'flex',
          alignItems: 'center',
          gap: 14,
          flexShrink: 0,
          position: 'relative',
          overflow: 'hidden',
        }}
      >
        <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--sindoor)', letterSpacing: -0.4, whiteSpace: 'nowrap', fontFamily: 'var(--font-deva)' }}>
          नारद <span style={{ color: 'rgba(252,250,242,0.45)', fontWeight: 400, fontFamily: 'var(--font-body)' }}>/ Dashboard</span>
        </div>

        <div style={{ flex: 1, minWidth: isMobile ? 0 : 280, maxWidth: 560 }}>
          <SearchBar
            userId={userId}
            onNavigate={nav => {
              if (nav === 'memory') setActiveTab('smriti')
              else if (nav === 'kanban' || nav === 'projects' || nav === 'ops') setActiveTab('karma')
              else if (nav === 'sutras' || nav === 'sutra') setActiveTab('tapasya')
              else if (nav === 'audit') setActiveTab('darshan')
            }}
          />
        </div>

        {/* Avatar chips — hidden on phones; the AwarenessBar already shows presence */}
        <div style={{ display: isMobile ? 'none' : 'flex', gap: 6, marginLeft: 'auto' }}>
          {AVATAR_NAMES.map(name => {
            const status = avatars[name]
            const active = status?.state === 'active'
            const colour = AVATAR_COLOURS[name]
            return (
              <div
                key={name}
                title={`${name}${status?.discipline ? ` · ${status.discipline}` : ''}`}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 5,
                  padding: '4px 9px',
                  borderRadius: 999,
                  fontSize: 11,
                  fontWeight: 600,
                  border: `1px solid ${active ? `${colour}80` : 'rgba(252,250,242,0.15)'}`,
                  background: active ? `${colour}22` : 'rgba(252,250,242,0.06)',
                  color: active ? colour : 'rgba(252,250,242,0.45)',
                }}
              >
                <span style={{ fontFamily: 'var(--font-deva)' }}>{DEVA[name]}</span>
                <span>{AVATAR_ABBREV[name] ?? name.slice(0, 2)}</span>
              </div>
            )
          })}
        </div>

        <button
          onClick={onClose}
          aria-label="Close dashboard"
          style={{
            background: 'rgba(252,250,242,0.08)',
            border: '1px solid rgba(252,250,242,0.15)',
            borderRadius: 8,
            color: 'rgba(252,250,242,0.6)',
            fontSize: 16,
            width: 30,
            height: 30,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            cursor: 'pointer',
            flexShrink: 0,
          }}
        >
          ✕
        </button>
      </div>

      <div
        style={{
          padding: '8px 16px',
          borderBottom: '1px solid rgba(45,42,38,0.08)',
          background: 'linear-gradient(180deg, rgba(252,250,242,0.98), rgba(247,242,230,0.92))',
          flexShrink: 0,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <span style={{ fontFamily: 'var(--font-deva)', fontSize: 13, color: 'var(--sindoor)' }}>{activeMeta.sanskrit}</span>
            <span style={{ fontSize: 12.5, color: 'rgba(45,42,38,0.58)', fontWeight: 600 }}>{activeMeta.label}</span>
          </div>
          <div style={{ display: isMobile ? 'none' : 'block', fontSize: 12, color: 'rgba(45,42,38,0.5)', lineHeight: 1.5 }}>
            {headerSummary(activeTab)}
          </div>
          <div style={{ display: isMobile ? 'none' : 'block', marginLeft: 'auto', fontSize: 11.5, color: 'rgba(45,42,38,0.45)' }}>
            {capabilities ? `${capabilities.build.runtime_mode} mode · ${capabilities.issue_count} issue${capabilities.issue_count === 1 ? '' : 's'}` : 'Runtime contract pending'}
          </div>
        </div>
      </div>

      <div
        style={{
          background: 'var(--paper)',
          padding: '0 16px',
          display: 'flex',
          flexShrink: 0,
          overflowX: 'auto',
        }}
      >
        {TABS.map(tab => {
          const isActive = tab.id === activeTab
          const badge = badgeMap[tab.id]
          return (
            <button
              key={tab.id}
              type="button"
              onClick={() => setActiveTab(tab.id)}
              style={{
                padding: '12px 14px 11px',
                fontSize: 12.5,
                fontWeight: 600,
                cursor: 'pointer',
                color: isActive ? 'var(--sindoor)' : 'rgba(45,42,38,0.48)',
                borderBottom: `2px solid ${isActive ? 'var(--sindoor)' : 'transparent'}`,
                display: 'flex',
                alignItems: 'center',
                gap: 6,
                whiteSpace: 'nowrap',
                background: 'transparent',
              }}
            >
              <span style={{ fontSize: 12 }}>{tab.icon}</span>
              {tab.label}
              {badge !== undefined && badge > 0 && (
                <span
                  style={{
                    minWidth: 16,
                    height: 16,
                    padding: '0 4px',
                    borderRadius: 999,
                    background: isActive ? 'rgba(194,65,12,0.14)' : 'rgba(45,42,38,0.08)',
                    color: isActive ? 'var(--sindoor)' : 'rgba(45,42,38,0.42)',
                    fontSize: 10,
                    display: 'inline-flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                  }}
                >
                  {badge}
                </span>
              )}
            </button>
          )
        })}
      </div>

      <MadhubaniBorder position="bottom" height={28} />

      <div style={{ flex: 1, minHeight: 0, overflow: 'hidden', background: 'linear-gradient(180deg, rgba(252,250,242,0.95), rgba(246,242,231,0.88))' }}>
        <SplitPane
          storageKey={`narad.dashboard.${activeTab}.layout`}
          left={primaryContent}
          right={secondaryContent}
          defaultRightWidth={defaultRightWidth}
          minLeftWidth={activeTab === 'darshan' ? 320 : 420}
          minRightWidth={activeTab === 'darshan' ? 360 : 300}
          rightCollapsedLabel={sectionRailTitle(activeTab)}
        />
      </div>

      {activeTab === 'darshan' && andonAlert && (
        <div
          style={{
            flexShrink: 0,
            padding: '10px 16px',
            background: 'rgba(229,90,31,0.08)',
            borderTop: '1px solid rgba(229,90,31,0.18)',
            color: 'rgba(45,42,38,0.7)',
            fontSize: 12.5,
          }}
        >
          <strong style={{ color: 'var(--kesari)' }}>Andon alert · {andonAlert.avatar}</strong>
          <span style={{ marginLeft: 8 }}>{andonAlert.trigger}</span>
          {andonAlert.task_preview && (
            <span style={{ marginLeft: 8, color: 'rgba(45,42,38,0.5)' }}>
              — {andonAlert.task_preview.slice(0, 90)}
            </span>
          )}
        </div>
      )}
    </div>
  )
}
