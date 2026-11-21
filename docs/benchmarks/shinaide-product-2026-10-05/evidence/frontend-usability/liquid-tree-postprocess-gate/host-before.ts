/**
 * Where the liquid post-processing chart lives in the DOM, and the globals that
 * open it.
 *
 * Its own React root on `document.body`, for the reason `solverMonitorHost.ts`
 * gives: the window outlives whatever opened it. The legacy appends the same
 * window straight to `document.body` (js/liquid_solve_monitor.js:927) and calls
 * it from the model tree, which is on a DIFFERENT left tab from the
 * post-processing panel — so a window parented to either would be torn down by
 * the tab switch that follows the click.
 *
 * `window.lsmShowPostProcessWindow` and `window._lsmOnTreeClickPostProcess` keep
 * the legacy names because the legacy tree calls them by name, and the same two
 * lines therefore wire either app.
 */

import { createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { LiquidPostprocessWindow } from '../components/LiquidPostprocessWindow'
import {
  openPpWindow,
  ppWindowState,
  resolveTreeClick,
  type PpTarget,
} from './liquidPostprocess'
import { isLiquidModeActive } from './liquidModeState'
import * as store from '../projectStore'

const HOST_ID = 'lsm-monitor-host'

let root: Root | null = null

export function mountLiquidPostprocessHost(): void {
  installGlobals()
  if (typeof document === 'undefined' || !document.body) return
  // The host ELEMENT is the record of "already mounted", not the module
  // variable: a hot reload resets the variable while the div is still in the
  // page, and a second `createRoot` on it stacks a duplicate window.
  if (document.getElementById(HOST_ID)) return
  const host = document.createElement('div')
  host.id = HOST_ID
  document.body.appendChild(host)
  root = createRoot(host)
  root.render(createElement(LiquidPostprocessWindow))
}

function installGlobals() {
  const w = window as unknown as Record<string, unknown>

  /**
   * `lsmShowPostProcessWindow(objectType, objectId, objectLabel)`.
   *
   * The legacy signature, so a caller written against either app works. The
   * `cp` form takes the compiled edge id directly — the caller already knows it
   * — while `_lsmOnTreeClickPostProcess` below is the one that has to derive it
   * from a rack and a server.
   */
  w.lsmShowPostProcessWindow = (objectType: string, objectId: string, objectLabel?: string) => {
    const label = objectLabel || objectId
    const target: PpTarget =
      objectType === 'cdu'
        ? { kind: 'cdu', label }
        : { kind: 'cp', label, edgeId: objectId, rackId: '', serverId: '', cpName: objectId }
    void openPpWindow(target)
  }

  /**
   * `_lsmOnTreeClickPostProcess(modelId, equipmentId)`.
   *
   * One argument in the legacy, which then reads the equipment out of the
   * global `uiState.selection`; passed in here because this app's selection is
   * React state and there is no global to read.
   *
   * Gated on liquid mode. The legacy also gates on the post-processing TAB, and
   * that gate makes the whole handler dead there — the model tree is hidden on
   * that tab (`_applyLeftTabUI`, STL_generate.html:19320), so the click it waits
   * for cannot happen. Dropping that half is what makes the chart reachable at
   * all; the liquid-mode gate is what keeps it from popping up over an
   * air-cooling project.
   */
  w._lsmOnTreeClickPostProcess = (modelId: string, equipmentId?: string | null) => {
    if (!isLiquidModeActive()) return
    const project = store.getProject()
    if (!project) return
    const model = store.findConstruction(project, modelId)?.model ?? null
    const target = resolveTreeClick({
      model: model as { uid: string; kind?: string; meta?: { name?: string } } | null,
      equipmentId,
    })
    if (!target) return
    void openPpWindow(target)
  }

  /**
   * A dev-only read handle on the chart's state, like `window.__dcdtuni`
   * (main.tsx:16).
   *
   * The series behind the canvas are otherwise unreachable: the chart is
   * pixels, the legend carries only labels, and importing this module from the
   * console gets a SECOND instance — Vite gives an HMR-updated module a `?t=`
   * URL, so a dynamic import resolves to a different module record with its own
   * empty store. Verifying that the React chart plots the same numbers as the
   * legacy one means reading the numbers, and this is where they are.
   */
  if (import.meta.env.DEV) w.__dcdtuniLiquidPp = { state: ppWindowState }
}
