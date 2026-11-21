/**
 * The handle `window.room2d` drives, and the queue that makes it safe to call
 * before the editor exists.
 *
 * The 3-agent SOP fires the whole sequence in ONE page eval:
 *
 *     room2d.enterEditor(); room2d.setAnnotations(true); room2d.zoomToFit()
 *
 * so every call after the first lands in the same tick as the mount. Anything
 * that arrives before the editor has registered is queued and replayed the
 * moment it does, which is what lets `enterEditor` mount a React tree without
 * the two calls behind it silently doing nothing.
 *
 * Split out from `room2dApi` so it imports NOTHING: the API module pulls in
 * react-dom, and a registry that did the same could not be imported by the
 * component that has to register with it.
 */

export interface Room2DHandle {
  zoomToFit(): void
  setShowAxes(show: boolean): void
  setAnnotations(show: boolean): void
  setBgColor(color: string): void
  setViewRotation(deg: number): void
  rotateView90(): void
  setTool(tool: 'draw' | 'select'): void
  toggleGridSnap(): void
  toggleVertexSnap(): void
  toggleEdgeAlign(): void
  toggleRotationSnap(): void
  setRotationSnap(on: boolean): void
  setGridSpacing(metres: number): void
  editGridBadge(): void
  setHeight(metres: number): void
  setVertexCoord(index: number, axis: 0 | 1, value: number): void
  clearAll(): void
  exportCanvasPNG(width?: number, height?: number): string | null
  getDebugState(): Record<string, unknown>
  exit(apply: boolean): void
}

type Deferred = (handle: Room2DHandle) => void

let handle: Room2DHandle | null = null
let pending: Deferred[] = []

export function getHandle(): Room2DHandle | null {
  return handle
}

export function isActive(): boolean {
  return handle !== null
}

/**
 * Publish the live editor. Returns the un-register, for an effect cleanup.
 *
 * The queue is drained here rather than in `enterEditor`, so it does not matter
 * whether the editor was opened by the API or by the user clicking the button —
 * a queued call runs either way.
 */
export function registerEditor(next: Room2DHandle): () => void {
  handle = next
  const queued = pending
  pending = []
  for (const fn of queued) fn(next)
  return () => {
    if (handle !== next) return
    handle = null
  }
}

/** Run `fn` now if the editor is up, otherwise as soon as it is. */
export function withEditor(fn: Deferred): void {
  if (handle) fn(handle)
  else pending.push(fn)
}

/**
 * Run `fn` only if the editor is already up.
 *
 * For the queries — `exportCanvasPNG`, `getDebugState` — which have to return a
 * value now and cannot be deferred.
 */
export function ifEditor<T>(fn: (h: Room2DHandle) => T, fallback: T): T {
  return handle ? fn(handle) : fallback
}

/** Drop anything queued for an editor that never appeared. */
export function clearPending(): void {
  pending = []
}
