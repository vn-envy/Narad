import { useEffect, useRef, useState } from 'react'
import { useIsMobile } from '../../hooks/useIsMobile'

interface SplitPaneProps {
  storageKey: string
  left: React.ReactNode
  right?: React.ReactNode
  defaultRightWidth?: number
  minLeftWidth?: number
  minRightWidth?: number
  rightCollapsedLabel?: string
}

interface PersistedLayout {
  rightWidth: number
  collapsed: boolean
}

export function SplitPane({
  storageKey,
  left,
  right,
  defaultRightWidth = 380,
  minLeftWidth = 360,
  minRightWidth = 280,
  rightCollapsedLabel = 'Expand side panel',
}: SplitPaneProps) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const draggingRef = useRef(false)
  const [rightWidth, setRightWidth] = useState(defaultRightWidth)
  const [collapsed, setCollapsed] = useState(false)
  const isMobile = useIsMobile()

  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(storageKey)
      if (!raw) return
      const parsed = JSON.parse(raw) as PersistedLayout
      if (typeof parsed.rightWidth === 'number') setRightWidth(parsed.rightWidth)
      if (typeof parsed.collapsed === 'boolean') setCollapsed(parsed.collapsed)
    } catch {
      // ignore corrupted layout preferences
    }
  }, [storageKey])

  useEffect(() => {
    try {
      const payload: PersistedLayout = { rightWidth, collapsed }
      window.localStorage.setItem(storageKey, JSON.stringify(payload))
    } catch {
      // ignore storage failures
    }
  }, [storageKey, rightWidth, collapsed])

  useEffect(() => {
    // Pointer events: one code path for mouse AND touch drags.
    const onMove = (event: PointerEvent) => {
      if (!draggingRef.current || !containerRef.current) return
      const rect = containerRef.current.getBoundingClientRect()
      const nextRight = rect.right - event.clientX
      const maxRight = Math.max(minRightWidth, rect.width - minLeftWidth)
      const clamped = Math.min(Math.max(nextRight, minRightWidth), maxRight)
      setCollapsed(false)
      setRightWidth(clamped)
    }
    const onUp = () => {
      draggingRef.current = false
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    window.addEventListener('pointercancel', onUp)
    return () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      window.removeEventListener('pointercancel', onUp)
    }
  }, [minLeftWidth, minRightWidth])

  if (!right) {
    return (
      <div
        style={{
          flex: 1,
          height: '100%',
          minHeight: 0,
          minWidth: 0,
          display: 'flex',
          flexDirection: 'column',
          overflowX: 'hidden',
          overflowY: 'auto',
          overscrollBehaviorY: 'contain',
          WebkitOverflowScrolling: 'touch',
          touchAction: 'pan-y',
          scrollbarGutter: 'stable',
        }}
      >
        {left}
      </div>
    )
  }

  // Phone: stack vertically — left pane on top, right pane as a bottom
  // section (no drag handle; the collapse toggle still works).
  if (isMobile) {
    return (
      <div style={{ flex: 1, minWidth: 0, minHeight: 0, height: '100%', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        <div
          style={{
            flex: 1,
            minHeight: 0,
            overflowX: 'hidden',
            overflowY: 'auto',
            overscrollBehaviorY: 'contain',
            WebkitOverflowScrolling: 'touch',
            touchAction: 'pan-y',
          }}
        >
          {left}
        </div>
        {!collapsed ? (
          <div
            style={{
              height: '42%',
              minHeight: 160,
              position: 'relative',
              overflowX: 'hidden',
              overflowY: 'auto',
              overscrollBehaviorY: 'contain',
              WebkitOverflowScrolling: 'touch',
              touchAction: 'pan-y',
              background: 'rgba(252,250,242,0.92)',
              borderTop: '1px solid rgba(26,24,21,0.10)',
              flexShrink: 0,
            }}
          >
            <button
              type="button"
              onClick={() => setCollapsed(true)}
              style={{
                position: 'absolute',
                top: 8,
                right: 10,
                zIndex: 2,
                width: 28,
                height: 28,
                borderRadius: 999,
                border: '1px solid rgba(26,24,21,0.12)',
                background: 'rgba(252,250,242,0.88)',
                color: 'rgba(26,24,21,0.55)',
                cursor: 'pointer',
              }}
              aria-label="Collapse bottom panel"
              title="Collapse bottom panel"
            >
              ↓
            </button>
            {right}
          </div>
        ) : (
          <button
            type="button"
            onClick={() => setCollapsed(false)}
            aria-label={rightCollapsedLabel}
            title={rightCollapsedLabel}
            style={{
              height: 32,
              flexShrink: 0,
              borderTop: '1px solid rgba(26,24,21,0.08)',
              background: 'linear-gradient(180deg, rgba(252,250,242,0.92), rgba(243,239,225,0.88))',
              color: 'var(--kajal)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: 6,
              cursor: 'pointer',
              fontSize: 11,
            }}
          >
            ↑ <span style={{ opacity: 0.6 }}>{rightCollapsedLabel}</span>
          </button>
        )}
      </div>
    )
  }

  return (
    <div ref={containerRef} style={{ flex: 1, minWidth: 0, minHeight: 0, height: '100%', display: 'flex', overflow: 'hidden' }}>
      <div
        style={{
          flex: 1,
          minWidth: minLeftWidth,
          minHeight: 0,
          overflowX: 'hidden',
          overflowY: 'auto',
          overscrollBehaviorY: 'contain',
          WebkitOverflowScrolling: 'touch',
          touchAction: 'pan-y',
          scrollbarGutter: 'stable',
        }}
      >
        {left}
      </div>

      {!collapsed && (
        <>
          <div
            role="separator"
            aria-orientation="vertical"
            onPointerDown={() => {
              draggingRef.current = true
              document.body.style.cursor = 'col-resize'
              document.body.style.userSelect = 'none'
            }}
            style={{
              width: 10,
              cursor: 'col-resize',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              background: 'linear-gradient(180deg, rgba(26,24,21,0.02), rgba(26,24,21,0.06), rgba(26,24,21,0.02))',
              borderLeft: '1px solid rgba(26,24,21,0.04)',
              borderRight: '1px solid rgba(26,24,21,0.04)',
              flexShrink: 0,
              touchAction: 'none',
            }}
            title="Drag to resize"
          >
            <div
              style={{
                width: 3,
                height: 46,
                borderRadius: 999,
                background: 'linear-gradient(180deg, rgba(242,142,28,0.1), rgba(45,42,38,0.18), rgba(242,142,28,0.1))',
              }}
            />
          </div>

          <aside
            style={{
              width: rightWidth,
              minWidth: minRightWidth,
              maxWidth: '52vw',
              minHeight: 0,
              overflowX: 'hidden',
              overflowY: 'auto',
              overscrollBehaviorY: 'contain',
              WebkitOverflowScrolling: 'touch',
              touchAction: 'pan-y',
              scrollbarGutter: 'stable',
              position: 'relative',
              background: 'rgba(252,250,242,0.92)',
              borderLeft: '1px solid rgba(26,24,21,0.08)',
            }}
          >
            <button
              type="button"
              onClick={() => setCollapsed(true)}
              style={{
                position: 'absolute',
                top: 10,
                right: 10,
                zIndex: 2,
                width: 26,
                height: 26,
                borderRadius: 999,
                border: '1px solid rgba(26,24,21,0.12)',
                background: 'rgba(252,250,242,0.88)',
                color: 'rgba(26,24,21,0.55)',
                cursor: 'pointer',
              }}
              aria-label="Collapse side panel"
              title="Collapse side panel"
            >
              →
            </button>
            {right}
          </aside>
        </>
      )}

      {collapsed && (
        <button
          type="button"
          onClick={() => setCollapsed(false)}
          aria-label={rightCollapsedLabel}
          title={rightCollapsedLabel}
          style={{
            width: 34,
            flexShrink: 0,
            borderLeft: '1px solid rgba(26,24,21,0.08)',
            background: 'linear-gradient(180deg, rgba(252,250,242,0.92), rgba(243,239,225,0.88))',
            color: 'var(--kajal)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            cursor: 'pointer',
          }}
        >
          ←
        </button>
      )}
    </div>
  )
}
