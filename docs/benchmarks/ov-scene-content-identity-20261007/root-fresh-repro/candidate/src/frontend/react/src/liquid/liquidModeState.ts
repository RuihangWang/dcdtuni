/**
 * One flag: is the editor showing liquid cooling or air?
 *
 * It lives on its own rather than inside `legacy/liquidScene.ts` because of who
 * has to read it. The scene shim needs it (the legacy modules ask
 * `isLiquidMode()` before every right-click), the post-processing right column
 * needs it to choose which panel it is, and the chart host needs it to decide
 * whether a tree click means anything — and the host is imported BY the scene
 * shim. Keeping the flag in the shim made that pair a cycle; a leaf module with
 * no imports of its own cannot be in one.
 *
 * `App` still sets it through `legacy/liquidScene`, which re-exports these three
 * names.
 */

let liquidActive = false
const listeners = new Set<() => void>()

/** Told by the app when the cooling-mode buttons switch. */
export function setLiquidModeActive(on: boolean) {
  if (liquidActive === on) return
  liquidActive = on
  listeners.forEach((fn) => fn())
}

export function isLiquidModeActive(): boolean {
  return liquidActive
}

export function subscribeLiquidMode(fn: () => void): () => void {
  listeners.add(fn)
  return () => {
    listeners.delete(fn)
  }
}
