import { useAvatara } from './hooks/useAvatara'
import { ChatPanel }   from './components/ChatPanel'
import { DarshanPanel } from './components/DarshanPanel'
import './tokens.css'

export default function App() {
  const { messages, avatars, naradActive, streaming, error, currentSession, send } = useAvatara()

  return (
    <>
      {/* Keyframe animations */}
      <style>{`
        @keyframes bounce {
          0%, 80%, 100% { transform: translateY(0); opacity: 0.4; }
          40%            { transform: translateY(-5px); opacity: 1; }
        }
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50%       { opacity: 0.4; }
        }
        /* Squiggle motifs (background decoration) */
        .squiggle {
          position: fixed;
          font-size: 18px;
          opacity: 0.12;
          color: var(--nila);
          pointer-events: none;
          z-index: 0;
          user-select: none;
        }
      `}</style>

      {/* Background squiggle marks — 5 scattered, per motif rules */}
      <span className="squiggle" style={{ top: '12%',  left: '28%'  }}>〜</span>
      <span className="squiggle" style={{ top: '38%',  left: '54%'  }}>〜</span>
      <span className="squiggle" style={{ top: '67%',  left: '22%'  }}>∿</span>
      <span className="squiggle" style={{ top: '82%',  left: '48%'  }}>〜</span>
      <span className="squiggle" style={{ top: '22%',  left: '78%'  }}>∿</span>

      {/* Zigzag band — top edge accent */}
      <div style={{
        position: 'fixed',
        top: 0,
        left: 0,
        right: 0,
        height: 4,
        background: 'repeating-linear-gradient(90deg, var(--marigold) 0, var(--marigold) 8px, var(--kesari) 8px, var(--kesari) 16px)',
        zIndex: 100,
      }} />

      {/* Two-panel layout */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: '1fr 360px',
        height: '100vh',
        paddingTop: 4,
        position: 'relative',
        zIndex: 1,
      }}>
        <ChatPanel
          messages={messages}
          streaming={streaming}
          error={error}
          onSend={send}
        />
        <DarshanPanel
          avatars={avatars}
          naradActive={naradActive}
          streaming={streaming}
          currentSession={currentSession}
        />
      </div>
    </>
  )
}
