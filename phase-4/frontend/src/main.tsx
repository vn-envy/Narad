import { StrictMode, Component } from 'react'
import type { ErrorInfo, ReactNode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'

class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null }
  static getDerivedStateFromError(error: Error) { return { error } }
  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('React error boundary caught:', error, info)
  }
  render() {
    if (this.state.error) {
      const err = this.state.error as Error
      return (
        <div style={{ padding: 32, fontFamily: 'monospace', background: '#2d2a26', color: '#fcfaf2', minHeight: '100vh' }}>
          <h2 style={{ color: '#e05a2b', marginBottom: 16 }}>⚠ Runtime Error</h2>
          <pre style={{ color: '#fcd34d', whiteSpace: 'pre-wrap', marginBottom: 16 }}>{err.message}</pre>
          <pre style={{ color: '#A0A49A', fontSize: 11, whiteSpace: 'pre-wrap' }}>{err.stack}</pre>
        </div>
      )
    }
    return this.props.children
  }
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>
)
