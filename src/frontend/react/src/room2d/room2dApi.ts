/**
 * `window.room2d` — the 2D editor's API, in the shape the rest of the toolchain
 * already calls.
 *
 * This is not a convenience. `room2d.enterEditor()` / `setAnnotations(true)` /
 * `zoomToFit()` / `exportCanvasPNG(1920, 1080)` is the Builder step of the
 * 3-agent SOP (`.claude/commands/dc-builder.md:328`) and of
 * `paper_experiment/batch_cfd_pipeline.py:378`, both of which drive the page
 * through `/api/browser/exec-js`. The React app has to answer the same four
 * calls or the topview it produces cannot be compared with the legacy's.
 *
 * `enterEditor` has to work with the editor CLOSED, which App alone can do —
 * except that App's state is not reachable from here. So this module mounts the
 * editor itself, into its own React root, when nothing else has: the store is a
 * module singleton, so a tree mounted beside App edits the same project App is
 * showing. `flushSync` makes the mount synchronous, because the SOP's next two
 * calls are in the same page eval.
 */

import type { ReactNode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { flushSync } from 'react-dom'
import {
  clearPending,
  getHandle,
  ifEditor,
  isActive,
  withEditor,
  type Room2DHandle,
} from './room2dRegistry'
import { isRotationSnapOn, setRotationSnap } from './rotationSnapBridge'

type Mounter = (onClose: () => void) => ReactNode

let mounter: Mounter | null = null
let root: Root | null = null
let host: HTMLElement | null = null

/**
 * Hand this module the editor element factory.
 *
 * Called from the component's own module rather than imported from it: the
 * component imports THIS file for its side effect, and importing it back would
 * make a cycle whose evaluation order decides whether `window.room2d` exists.
 */
export function registerMounter(fn: Mounter): void {
  mounter = fn
}

function unmountStandalone(): void {
  if (!root) return
  const dying = root
  const node = host
  root = null
  host = null
  // Unmounting from inside a React commit throws; the editor's own onClose runs
  // during one, so the teardown is deferred by a tick.
  setTimeout(() => {
    dying.unmount()
    node?.remove()
  }, 0)
}

/**
 * Open the editor.
 *
 * A no-op when one is already up, exactly as the legacy's `if (_active) return`
 * (`room_2d_editor.js:145`) — a second editor over the first would register its
 * handle and leave the visible one unreachable.
 */
export function enterEditor(): void {
  if (isActive()) return
  if (typeof document === 'undefined') return
  if (!mounter) {
    // The component module has not been evaluated, so there is nothing to
    // mount. Silence here would look exactly like a mount that failed.
    console.error('[room2d] enterEditor called before the editor module loaded')
    return
  }
  const node = document.createElement('div')
  node.id = 'room2d-standalone-host'
  document.body.appendChild(node)
  host = node
  root = createRoot(node)
  // The editor's own close must tear down THIS root directly, not go back
  // through `exitEditor` — that would call the handle's `exit`, which calls
  // `onClose` again, and the two would recurse until the stack gave out. The
  // Apply button takes exactly that path.
  const element = mounter(() => unmountStandalone())
  // Synchronous, because `room2d.setAnnotations(true)` and `zoomToFit()` follow
  // in the SAME eval. Without the flush they would run against an editor that
  // has not registered yet and be queued behind a mount that has not happened.
  flushSync(() => {
    root?.render(element)
  })
}

/** Apply-and-close, or just close. The legacy signature: `exitEditor(apply)`. */
export function exitEditor(apply = false): void {
  const live = getHandle()
  if (live) live.exit(!!apply)
  else clearPending()
  unmountStandalone()
}

/**
 * A standalone editor is closing because something else took over.
 *
 * Called when a second editor registers — App's own, opened from the button
 * while an API-mounted one was up. Two editors would fight over one handle.
 */
export function dropStandaloneIfAny(): void {
  unmountStandalone()
}

const api = {
  enterEditor,
  exitEditor,
  isActive,
  zoomToFit: () => withEditor((h) => h.zoomToFit()),
  setShowAxes: (show: boolean) => withEditor((h) => h.setShowAxes(show)),
  setAnnotations: (show: boolean) => withEditor((h) => h.setAnnotations(show)),
  setBgColor: (color: string) => withEditor((h) => h.setBgColor(color)),
  setViewRotation: (deg: number) => withEditor((h) => h.setViewRotation(deg)),
  rotateView90: () => withEditor((h) => h.rotateView90()),
  setTool: (tool: 'draw' | 'select') => withEditor((h) => h.setTool(tool)),
  toggleGridSnap: () => withEditor((h) => h.toggleGridSnap()),
  toggleVertexSnap: () => withEditor((h) => h.toggleVertexSnap()),
  toggleEdgeAlign: () => withEditor((h) => h.toggleEdgeAlign()),
  toggleRotationSnap: () => withEditor((h) => h.toggleRotationSnap()),
  setGridSpacing: (metres: number) => withEditor((h) => h.setGridSpacing(metres)),
  editGridBadge: () => withEditor((h) => h.editGridBadge()),
  setHeight: (metres: number) => withEditor((h) => h.setHeight(metres)),
  setVertexCoord: (index: number, axis: 0 | 1, value: number) =>
    withEditor((h) => h.setVertexCoord(index, axis, value)),
  clearAll: () => withEditor((h) => h.clearAll()),
  exportCanvasPNG: (width?: number, height?: number) =>
    ifEditor((h) => h.exportCanvasPNG(width, height), null),
  getDebugState: () =>
    ifEditor((h) => h.getDebugState(), {
      active: false,
      tool: 'select',
      closed: false,
      vertices: 0,
      view: { offsetX: 0, offsetY: 0, scale: 0 },
      viewRotation: 0,
      canvasWidth: 0,
      canvasHeight: 0,
      aisleLabels: [] as string[],
    }),
  /**
   * The 3D toolbar's half of the 旋转吸附 sync.
   *
   * Same name and same underscore as the legacy's (`room_2d_editor.js:2136`),
   * so a caller written against either app finds it.
   */
  _setRotationSnap: (on: boolean) => {
    setRotationSnap(!!on)
    withEditor((h) => h.setRotationSnap(!!on))
  },
  _isRotationSnapOn: () => isRotationSnapOn(),
}

export type Room2DApi = typeof api

if (typeof window !== 'undefined') {
  ;(window as unknown as { room2d?: Room2DApi }).room2d = api
}

export { api as room2dApi }
export type { Room2DHandle }
