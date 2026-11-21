/**
 * First-person mode, from the legacy editor's own module.
 *
 * `js/first_person_mode.js` is 623 lines of creative-mode navigation: capsule
 * collision against a BVH built from the whole scene, walk and fly modes,
 * gravity and jump, pointer lock with a cursor-free toggle, a pause menu, and a
 * crosshair click that hands off to the editor's granular selection resolver.
 *
 * It is imported, not rewritten. It reads its Three.js handles from
 * `window.__three` and touches nothing else of the page, which is exactly the
 * seam a second app needs — and a reimplementation would be a second answer to
 * "can I walk through this rack".
 *
 * Two things it needs that a bare React viewport does not have:
 *
 *   `three-mesh-bvh` patched onto BufferGeometry — `_buildCollider` calls
 *     `geometry.computeBoundsTree()`, which is supplied by the package sharing
 *     the viewport's Three.js instance. Without it entering the mode throws and the camera
 *     is left with orbit disabled.
 *   `window.requestRender` — the module calls it after every frame it
 *     integrates. React renders continuously, so this can be a no-op, but its
 *     ABSENCE is silent and would only show as a stale first frame.
 */

import * as THREE from 'three'
import { t, type TranslationKey } from '../i18n'

import { computeBoundsTree, disposeBoundsTree, acceleratedRaycast } from 'three-mesh-bvh'

/** Use the viewport's Three.js for collision and ordinary editor picking. */
export function installMeshBvh() {
  const proto = THREE.BufferGeometry.prototype
  if (Object.prototype.hasOwnProperty.call(proto, 'computeBoundsTree')) return
  proto.computeBoundsTree = computeBoundsTree
  proto.disposeBoundsTree = disposeBoundsTree
  const original = THREE.Mesh.prototype.raycast
  THREE.Mesh.prototype.raycast = function (raycaster, intersects) {
    // Only the scene collider has a BVH. Other editor meshes keep their
    // original picking method, including custom raycast behavior.
    if (this.geometry?.boundsTree)
      return acceleratedRaycast.call(this, raycaster, intersects)
    return original.call(this, raycaster, intersects)
  }
}

export interface FpsThreeHandles {
  camera: THREE.PerspectiveCamera
  controls: { enabled: boolean; target: THREE.Vector3; update(): void }
  renderer: THREE.WebGLRenderer
  scene: THREE.Scene
  globalGroup: THREE.Group
  /** Optional click-to-select hand-off; first-person is inspect-only. */
  edit?: {
    handleViewportSelection(event: PointerEvent): void
    canSelectGeometry(): boolean
  }
}

export interface FpsModeApi {
  version: string
  enter(): void
  exit(): void
  toggle(): void
  isActive(): boolean
  _state: { active: boolean; mode: string; pos: THREE.Vector3; yaw: number; pitch: number }
  _injectInput(key: string, down: boolean): void
  _setLook(yaw: number, pitch: number): void
  _tick(dt: number): void
}

let loaded = false

/**
 * Publish the handles the module reads and load it.
 *
 * Called from the viewport once its scene exists. Loading it earlier would
 * leave `window.__three` pointing at a renderer that is about to be replaced.
 */
export async function installFirstPerson(handles: FpsThreeHandles): Promise<FpsModeApi> {
  const w = window as unknown as {
    __three?: FpsThreeHandles
    requestRender?: () => void
    fpsMode?: FpsModeApi
    _fpsTranslate?: (key: string) => string
  }
  w.__three = handles
  w._fpsTranslate = (key) => t(key as TranslationKey)
  // React drives a continuous animation loop, so a redraw request is already
  // satisfied by the next frame. Defined anyway: the module calls it six times
  // and an undefined call would be a silent no-op only by luck of the guard.
  if (!w.requestRender) w.requestRender = () => {}
  if (!loaded) {
    installMeshBvh()
    await import('../../../shared/js/first_person_mode.js')
    loaded = true
  }
  if (!w.fpsMode) throw new Error('first_person_mode.js did not install window.fpsMode')
  return w.fpsMode
}

export function firstPerson(): FpsModeApi | null {
  return (window as unknown as { fpsMode?: FpsModeApi }).fpsMode ?? null
}
