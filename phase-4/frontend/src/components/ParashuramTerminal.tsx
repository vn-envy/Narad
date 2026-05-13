import { useEffect, useRef, useState } from 'react'
import { ScrollArea } from '@/components/ui/scroll-area'
import type { AvatarStatus, Message, StepEvent } from '../hooks/useAvatara'

interface Props {
  parashurama: AvatarStatus
  messages: Message[]
  streaming: boolean
  allAvatars?: Record<string, AvatarStatus>
  stepEvents?: StepEvent[]
  onClose?: () => void
  onMinimize?: () => void
  onExpand?: () => void
  minimized?: boolean
}

const AVATAR_DOT_COLORS: Record<string, string> = {
  narad:       '#F28E1C',
  Matsya:      '#065f46',
  Varaha:      '#c2410c',
  Narasimha:   '#dc2626',
  Rama:        '#92610a',
  Krishna:     '#1d4ed8',
  Buddha:      '#6d28d9',
  Parashurama: '#57534e',
  Vamana:      '#0369a1',
}

function ToolCallLine({ step }: { step: StepEvent }) {
  const dot = AVATAR_DOT_COLORS[step.avatar] ?? '#78716c'
  const isCall    = step.kind === 'tool_call'
  const isResult  = step.kind === 'tool_result'
  const isRouting = step.kind === 'text' && step.avatar === 'narad'
  const isText    = step.kind === 'text' && step.avatar !== 'narad'

  return (
    <div className="flex items-start gap-2 py-[3px] font-mono text-[11px] leading-snug">
      <span
        className="flex-shrink-0 mt-[4px] w-[6px] h-[6px] rounded-full"
        style={{ background: dot, boxShadow: `0 0 4px ${dot}55` }}
      />
      <span className="flex-1 min-w-0 break-words">
        {isRouting && (
          <span style={{ color: '#F28E1C', opacity: 0.80 }}>{step.preview}</span>
        )}
        {isCall && (
          <>
            <span style={{ color: 'rgba(160,164,154,0.45)' }}>{step.avatar}</span>
            <span style={{ color: '#F28E1C' }}> ▶ </span>
            <span style={{ color: '#a5f3fc' }}>{step.tool}</span>
            {step.preview && (
              <span style={{ color: 'rgba(245,235,215,0.38)' }}>({step.preview})</span>
            )}
          </>
        )}
        {isResult && (
          <>
            <span style={{ color: 'rgba(160,164,154,0.35)' }}>{step.avatar}</span>
            <span style={{ color: '#28C840' }}> ✓ </span>
            <span style={{ color: 'rgba(160,164,154,0.55)' }}>{step.tool} → </span>
            <span style={{ color: 'rgba(245,235,215,0.55)' }}>{step.preview}</span>
          </>
        )}
        {isText && (
          <>
            <span style={{ color: 'rgba(160,164,154,0.35)' }}>{step.avatar} </span>
            <span style={{ color: 'rgba(245,235,215,0.35)' }}>{step.preview}</span>
          </>
        )}
      </span>
    </div>
  )
}

export function ParashuramTerminal({
  parashurama,
  messages: _messages,
  streaming,
  allAvatars,
  stepEvents = [],
  onClose,
  onMinimize,
  onExpand,
  minimized = false,
}: Props) {
  const bottomRef = useRef<HTMLDivElement>(null)
  const [elapsedS, setElapsedS] = useState(0)

  const activeAvatar = allAvatars
    ? Object.entries(allAvatars).find(([, s]) => s.state === 'active')?.[0]
    : null

  const anyActive = streaming || !!activeAvatar

  // Tick every second while active so the terminal never looks frozen during
  // long avatar runs that emit no tool-call step events
  useEffect(() => {
    if (!anyActive) { setElapsedS(0); return }
    setElapsedS(0)
    const id = setInterval(() => setElapsedS(s => s + 1), 1000)
    return () => clearInterval(id)
  }, [anyActive, activeAvatar])

  // Autoscroll to latest step whenever new events arrive or streaming state changes
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [stepEvents.length, streaming])

  return (
    <div
      className="terminal-glow flex flex-col h-full overflow-hidden"
      style={{
        background: 'rgba(14,12,10,0.98)',
        backdropFilter: 'blur(16px)',
      }}
    >
      {/* Title bar with functional window controls */}
      <div
        className="flex items-center gap-2 px-4 flex-shrink-0 border-b"
        style={{
          borderColor: 'rgba(74,87,32,0.20)',
          background: 'rgba(20,18,15,0.80)',
          minHeight: 36,
          height: 36,
        }}
      >
        {/* macOS-style dots — now functional */}
        <div className="flex items-center gap-1.5">
          <button
            title="Close terminal"
            onClick={onClose}
            className="inline-block w-2.5 h-2.5 rounded-full hover:brightness-125 transition-all"
            style={{ background: '#FF5F57' }}
          />
          <button
            title="Minimize terminal"
            onClick={onMinimize}
            className="inline-block w-2.5 h-2.5 rounded-full hover:brightness-125 transition-all"
            style={{ background: '#FEBC2E' }}
          />
          <button
            title="Expand terminal"
            onClick={onExpand}
            className="inline-block w-2.5 h-2.5 rounded-full hover:brightness-125 transition-all"
            style={{ background: '#28C840' }}
          />
        </div>

        <span
          className="ml-2 font-mono text-[10px] tracking-[0.12em] uppercase"
          style={{ color: 'rgba(160,164,154,0.55)' }}
        >
          {activeAvatar ? `${activeAvatar.toUpperCase()} — EXECUTING` : 'AVATARA — DARSHAN TERMINAL'}
        </span>

        {anyActive && (
          <span
            className="ml-auto font-mono text-[9px] px-2 py-px rounded"
            style={{
              color: '#28C840',
              background: 'rgba(40,200,64,0.10)',
              animation: 'pulse 1.5s ease-in-out infinite',
            }}
          >
            ● {activeAvatar ?? 'ROUTING'}
          </span>
        )}
        {!anyActive && stepEvents.length > 0 && (
          <span className="ml-auto font-mono text-[9px]" style={{ color: 'rgba(160,164,154,0.30)' }}>
            {stepEvents.length} steps
          </span>
        )}
      </div>

      {/* Minimized: just the title bar */}
      {minimized && <div style={{ flex: 1 }} />}

      {/* Terminal body — backend activities only */}
      {!minimized && (
        <div style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
          <ScrollArea style={{ height: '100%' }}>
            <div className="px-4 py-3 font-mono text-[12px] leading-relaxed">

              {/* Prompt line */}
              <div className="mb-2 flex items-center gap-0">
                <span style={{ color: 'rgba(74,87,32,0.80)' }}>narad</span>
                <span style={{ color: 'rgba(160,164,154,0.40)' }}>@</span>
                <span style={{ color: '#F28E1C' }}>avatara</span>
                <span style={{ color: 'rgba(160,164,154,0.40)' }}> ~ </span>
                <span style={{ color: 'rgba(255,255,255,0.40)' }}>$</span>
              </div>

              {/* Live step log — routing + tool calls + completions */}
              {stepEvents.length > 0 && (
                <div className="mb-2 border-l border-dashed pl-3"
                  style={{ borderColor: 'rgba(74,87,32,0.25)' }}>
                  {stepEvents.map(step => (
                    <ToolCallLine key={step.id} step={step} />
                  ))}
                  {/* Live cursor while active */}
                  {anyActive && (
                    <div className="flex items-center gap-1.5 mt-1 py-[2px]">
                      <span
                        className="inline-block w-[6px] h-[6px] rounded-full"
                        style={{ background: '#F28E1C', animation: 'pulse 1.2s ease-in-out infinite' }}
                      />
                      <span
                        className="font-mono text-[10px]"
                        style={{ color: 'rgba(242,142,28,0.55)' }}
                      >
                        {activeAvatar ? `${activeAvatar} executing…` : 'routing…'}
                        {elapsedS > 0 && (
                          <span style={{ color: 'rgba(242,142,28,0.35)', marginLeft: 4 }}>
                            ({elapsedS}s)
                          </span>
                        )}
                      </span>
                    </div>
                  )}
                </div>
              )}

              {/* First-run spinner — no steps yet, agent just started */}
              {anyActive && stepEvents.length === 0 && (
                <div className="flex items-center gap-2 mt-1 border-l border-dashed pl-3"
                  style={{ borderColor: 'rgba(74,87,32,0.25)' }}>
                  <span
                    className="font-mono text-[11px]"
                    style={{ color: 'rgba(242,142,28,0.50)', animation: 'pulse 1.2s ease-in-out infinite' }}
                  >
                    → routing…{elapsedS > 0 && <span style={{ opacity: 0.5 }}> ({elapsedS}s)</span>}
                  </span>
                  <span
                    className="inline-block w-[8px] h-[13px]"
                    style={{ background: '#F28E1C', opacity: 0.80, animation: 'blink 1s step-end infinite' }}
                  />
                </div>
              )}

              {/* Exit line — shown after all steps complete */}
              {!anyActive && stepEvents.length > 0 && (
                <div className="mt-1 pt-2" style={{ borderTop: '1px solid rgba(74,87,32,0.12)' }}>
                  <span style={{ color: 'rgba(40,200,64,0.50)' }}>[exit 0]</span>
                  {parashurama?.latencyMs && (
                    <span style={{ color: 'rgba(160,164,154,0.30)' }}>
                      {' '}completed in {(parashurama.latencyMs / 1000).toFixed(2)}s
                    </span>
                  )}
                </div>
              )}

              {/* Idle — no activity yet this session */}
              {!anyActive && stepEvents.length === 0 && (
                <div className="flex items-center gap-1 mt-1" style={{ color: 'rgba(160,164,154,0.30)' }}>
                  <span>ready</span>
                  <span
                    className="inline-block w-[8px] h-[13px]"
                    style={{ background: 'rgba(160,164,154,0.30)', animation: 'blink 1s step-end infinite' }}
                  />
                </div>
              )}

              <div ref={bottomRef} />
            </div>
          </ScrollArea>
        </div>
      )}
    </div>
  )
}
