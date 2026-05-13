import { useState } from 'react'
import { X } from 'lucide-react'
import type { AvatarName, AvatarStatus, Message, SessionInfo, StepEvent, KanbanUpdatePayload, AndonAlertPayload } from '../hooks/useAvatara'
import { DarshanPanel }       from './DarshanPanel'
import { ParashuramTerminal } from './ParashuramTerminal'
import { SutraPanel }         from './SutraPanel'
import { ProjectsPanel }      from './ProjectsPanel'
import { KanbanBoardView }    from './KanbanBoardView'
import { OpsView }            from './OpsView'
import { MadhubaniBorder }    from './MadhubaniBorder'

// ── Tab definitions ────────────────────────────────────────────────────────────

type TabId = 'live' | 'kanban' | 'sutras' | 'memory' | 'ops'

const TABS: { id: TabId; label: string; devanagari: string }[] = [
  { id: 'live',   label: 'Live',    devanagari: 'दर्शन'     },
  { id: 'kanban', label: 'Kanban',  devanagari: 'कार्यक्रम' },
  { id: 'sutras', label: 'Sutras',  devanagari: 'सूत्र'     },
  { id: 'memory', label: 'Memory',  devanagari: 'स्मृति'    },
  { id: 'ops',    label: 'Ops',     devanagari: '⚙'         },
]

// ── Props ──────────────────────────────────────────────────────────────────────

interface Props {
  open: boolean
  onClose: () => void
  avatars: Record<AvatarName, AvatarStatus>
  naradActive: boolean
  streaming: boolean
  messages: Message[]
  stepEvents: StepEvent[]
  sessionTotals: { promptTokens: number; completionTokens: number; totalTokens: number }
  currentSession: SessionInfo | null
  userId: string
  kanbanUpdate: KanbanUpdatePayload | null
  andonAlert: AndonAlertPayload | null
}

// ── Component ─────────────────────────────────────────────────────────────────

export function DarshanDashboard({
  open,
  onClose,
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
}: Props) {
  const [activeTab, setActiveTab] = useState<TabId>('live')

  // Flash Ops tab badge when andon fires
  const [andonFlash, setAndonFlash] = useState(false)
  if (andonAlert && activeTab !== 'ops' && !andonFlash) {
    setAndonFlash(true)
    setTimeout(() => setAndonFlash(false), 5000)
  }

  if (!open) return null

  return (
    /* Overlay backdrop */
    <div
      className="fixed inset-0 z-40 flex items-stretch justify-end"
      style={{ background: 'rgba(0,0,0,0.4)' }}
      onClick={e => { if (e.target === e.currentTarget) onClose() }}
    >
      {/* Drawer */}
      <div
        className="relative flex flex-col h-full overflow-hidden"
        style={{
          width: 760,
          background: 'var(--kajal, #2d2a26)',
          borderLeft: '1.5px solid rgba(252,250,242,0.10)',
        }}
        onClick={e => e.stopPropagation()}
      >
        {/* Madhubani border */}
        <MadhubaniBorder />

        {/* Header */}
        <div
          className="flex items-center justify-between px-5 py-3 flex-shrink-0"
          style={{ borderBottom: '1px solid rgba(252,250,242,0.08)', position: 'relative', zIndex: 1 }}
        >
          <div className="flex items-baseline gap-2">
            <span
              className="font-bold tracking-tight"
              style={{ color: '#FFC837', fontSize: 15, fontFamily: 'Space Grotesk, sans-serif' }}
            >
              Darshan
            </span>
            <span
              className="text-sm"
              style={{ color: 'rgba(252,250,242,0.35)', fontFamily: 'Space Grotesk, sans-serif' }}
            >
              दर्शन
            </span>
            <span
              className="ml-2 text-[10px] font-mono uppercase tracking-widest"
              style={{ color: 'rgba(252,250,242,0.25)' }}
            >
              Dashboard
            </span>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded transition-colors hover:bg-white/10"
            style={{ color: 'rgba(252,250,242,0.4)' }}
          >
            <X size={14} />
          </button>
        </div>

        {/* Tab bar */}
        <div
          className="flex items-end gap-0 flex-shrink-0 px-4"
          style={{
            borderBottom: '1px solid rgba(252,250,242,0.08)',
            position: 'relative',
            zIndex: 1,
          }}
        >
          {TABS.map(tab => {
            const isActive = activeTab === tab.id
            const hasFlash = tab.id === 'ops' && andonFlash

            return (
              <button
                key={tab.id}
                onClick={() => { setActiveTab(tab.id); if (tab.id === 'ops') setAndonFlash(false) }}
                className="flex flex-col items-center px-4 py-2.5 transition-colors relative"
                style={{
                  borderBottom: isActive ? '2px solid #FFC837' : '2px solid transparent',
                  marginBottom: -1,
                }}
              >
                <span
                  className="text-[11px] font-semibold"
                  style={{
                    color: isActive ? '#FFC837' : 'rgba(252,250,242,0.45)',
                    fontFamily: 'Space Grotesk, sans-serif',
                  }}
                >
                  {tab.label}
                </span>
                <span
                  className="text-[9px]"
                  style={{ color: isActive ? 'rgba(255,200,55,0.55)' : 'rgba(252,250,242,0.2)' }}
                >
                  {tab.devanagari}
                </span>
                {/* Andon flash badge */}
                {hasFlash && (
                  <span
                    className="absolute top-1 right-1 w-2 h-2 rounded-full animate-pulse"
                    style={{ background: '#c2410c' }}
                  />
                )}
              </button>
            )
          })}
        </div>

        {/* Tab content */}
        <div className="flex-1 min-h-0 overflow-hidden" style={{ position: 'relative', zIndex: 1 }}>
          {activeTab === 'live' && (
            <div className="flex h-full">
              <div className="flex-1 min-w-0 overflow-hidden" style={{ borderRight: '1px solid rgba(252,250,242,0.06)' }}>
                <DarshanPanel
                  avatars={avatars}
                  naradActive={naradActive}
                  streaming={streaming}
                  currentSession={currentSession}
                />
              </div>
              <div className="flex-1 min-w-0 overflow-hidden">
                <ParashuramTerminal
                  parashurama={avatars.Parashurama}
                  messages={messages}
                  streaming={streaming}
                  allAvatars={avatars}
                  stepEvents={stepEvents}
                />
              </div>
            </div>
          )}

          {activeTab === 'kanban' && (
            <KanbanBoardView
              sessionId={currentSession?.sessionId ?? null}
              kanbanUpdate={kanbanUpdate}
            />
          )}

          {activeTab === 'sutras' && (
            <div className="h-full overflow-y-auto">
              <SutraPanel
                userId={userId}
                messages={messages}
                sessionTotals={sessionTotals}
              />
            </div>
          )}

          {activeTab === 'memory' && (
            <div className="h-full overflow-hidden">
              <ProjectsPanel open={true} onToggle={() => {}} />
            </div>
          )}

          {activeTab === 'ops' && (
            <OpsView andonAlert={andonAlert} />
          )}
        </div>
      </div>
    </div>
  )
}
