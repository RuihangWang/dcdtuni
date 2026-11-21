/**
 * Where the floating monitor lives in the DOM.
 *
 * Its own React root on `document.body`, not a node inside the solver panel.
 * The panel unmounts the moment the user leaves the solver tab, and a chart
 * that vanished with it would throw away the residual history of a run still in
 * progress — the legacy window is appended to `document.body` for exactly this
 * reason (STL_generate.html:25190).
 *
 * A portal out of the panel would not do either: a portal's CONTENT is torn
 * down with the component that renders it, so only the DOM position moves.
 */

import { createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { SolverMonitorWindow } from '../components/SolverMonitorWindow'

const HOST_ID = 'sm-monitor-host'

let root: Root | null = null

export function mountSolverMonitorHost(): void {
  if (root) return
  if (typeof document === 'undefined' || !document.body) return
  // The host element, not the module variable, is the record of "already
  // mounted". A hot reload re-executes this module with `root` back to null
  // while the div it created is still in the page, and calling `createRoot` on
  // it a second time stacks a whole extra monitor window on top of the first —
  // which is exactly what happened while this was being built.
  if (document.getElementById(HOST_ID)) return
  const host = document.createElement('div')
  host.id = HOST_ID
  document.body.appendChild(host)
  root = createRoot(host)
  root.render(createElement(SolverMonitorWindow))
}
