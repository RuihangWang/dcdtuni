/**
 * Who gets a viewport press first.
 *
 * Every step here draws with `depthTest` off, ON TOP of the thing it belongs
 * to, so the order decides which of two overlapping helpers a press means. The
 * legacy fixes it in one dispatcher (STL_generate.html:18204-18211): the valve
 * drag, then the seed hub, then the rotate rings, then the resize handles, and
 * only if none of them wanted it does the press become a selection click.
 *
 * A thunk per step, not a boolean: two of them raycast the scene, and asking
 * all four to choose one would pay for four.
 */

export interface PointerDownChain {
  /**
   * Liquid mode's valve → rack-manifold drag. Absent outside liquid mode,
   * which is most of the time.
   */
  valveDrag?: () => boolean
  seedDrag: () => boolean
  rotateGizmo: () => boolean
  /** The air-pipe waypoint balls and their gizmo. */
  pipeHandles?: () => boolean
  faceDrag: () => boolean
}

export type PointerGrab = 'valve' | 'seed' | 'rotate' | 'pipe' | 'face' | null

export function dispatchPointerDown(chain: PointerDownChain): PointerGrab {
  if (chain.valveDrag?.()) return 'valve'
  if (chain.seedDrag()) return 'seed'
  if (chain.rotateGizmo()) return 'rotate'
  if (chain.pipeHandles?.()) return 'pipe'
  if (chain.faceDrag()) return 'face'
  return null
}
