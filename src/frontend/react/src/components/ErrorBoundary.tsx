import { Component, type ErrorInfo, type ReactNode } from 'react'
import { t } from '../i18n'

/**
 * The last line of defence between a thrown render and a black window.
 *
 * Without a boundary, React 18 unmounts the WHOLE tree on an uncaught render
 * error: the panels, the viewport and the dock all vanish and what is left is
 * the body background — a black rectangle with a live tab title, which reads as
 * "the app died" and carries no hint of why. Reported exactly that way: "I
 * clicked a patch and the screen went black."
 *
 * So the root renders through here, and a failure becomes a card with the
 * message and the stack instead of nothing. The state that caused it is still
 * gone (there is no safe way to keep rendering it), but the user can read what
 * happened and reload, and a screenshot of the card is a bug report.
 */
interface State {
  error: Error | null
}

export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // The console keeps the component stack; the card keeps the message.
    console.error('[dcdtuni] uncaught render error', error, info.componentStack)
  }

  render(): ReactNode {
    const { error } = this.state
    if (!error) return this.props.children
    return (
      <div
        style={{
          position: 'fixed',
          inset: 0,
          zIndex: 9999,
          background: '#161620',
          color: '#e5e5e5',
          fontFamily: '"Share Tech Mono", "Cascadia Code", "Fira Code", "Consolas", monospace',
          fontSize: 13,
          padding: 32,
          overflow: 'auto',
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
        }}
      >
        <div style={{ color: '#ff2244', fontSize: 15, marginBottom: 12 }}>
          {t('error.crash_title')}
        </div>
        <div style={{ color: '#ffb800', marginBottom: 12 }}>{String(error.message ?? error)}</div>
        <div style={{ opacity: 0.75, marginBottom: 16 }}>{error.stack ?? ''}</div>
        <button
          onClick={() => window.location.reload()}
          style={{
            background: '#1e1e28',
            color: '#00ff88',
            border: '1px solid rgba(0,255,136,0.4)',
            padding: '6px 14px',
            fontFamily: 'inherit',
            fontSize: 13,
            cursor: 'pointer',
          }}
        >
          {t('error.crash_reload')}
        </button>
      </div>
    )
  }
}
