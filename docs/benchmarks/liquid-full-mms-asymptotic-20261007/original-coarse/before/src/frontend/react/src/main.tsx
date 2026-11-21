import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { App } from './App'
import { ErrorBoundary } from './components/ErrorBoundary'
import './automation/contract'
import './styles.css'

const host = document.getElementById('root')
if (!host) throw new Error('#root missing from index.html')

// `window.__dcdtuni` — the automation contract. Installed by importing the
// module (see automation/contract.ts for what is on it and why the names are
// the legacy's).
//
// NOT dev-gated any more. It was, on the reasoning that production has no
// business exposing an editing API — right for a bare store handle, wrong here:
// the production build is exactly what the head browser drives, and
// /api/browser/exec-js can already evaluate arbitrary JS in the page, so the
// gate bought no safety and cost the whole 3-agent pipeline.

createRoot(host).render(
  <StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>,
)
