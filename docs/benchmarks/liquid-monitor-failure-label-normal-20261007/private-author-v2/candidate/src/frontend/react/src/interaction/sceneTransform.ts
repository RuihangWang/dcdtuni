/**
 * The toolbar's transform buttons with NOTHING selected: turn the whole scene.
 *
 * `globalTransform` (STL_generate.html:16219) tries the selection first and,
 * when there is none, rotates / mirrors / resets `wrapperGroup` — the group the
 * whole model tree hangs under. It is how a set of imported models that came in
 * lying on their side gets stood up without touching a single model's data.
 *
 * This is a VIEW transform: it changes no `.cfdproj` field, and there is no
 * undo entry because there is nothing in the project to undo. It does reach the
 * STL export, which reads the built scene and compensates for a mirrored
 * wrapper by flipping the winding (the legacy checks
 * `wrapperGroup.matrixWorld.determinant() < 0` in half a dozen places).
 *
 * The three.js calls are the legacy's, not equivalents: `rotateX` turns about
 * the object's OWN axis (a post-multiply), so a second rotation about a
 * different axis lands somewhere a world-axis rotation would not.
 */

import type * as THREE from 'three'
import type { TransformAction } from './transformActions'

/** The subset of `THREE.Object3D` a scene transform touches. */
export interface SceneWrapper {
  rotateX(angle: number): unknown
  rotateY(angle: number): unknown
  rotateZ(angle: number): unknown
  rotation: { set(x: number, y: number, z: number): unknown }
  scale: { x: number; y: number; z: number; set(x: number, y: number, z: number): unknown }
  updateMatrixWorld(force?: boolean): unknown
}

export function applySceneTransformTo(wrapper: SceneWrapper, action: TransformAction) {
  if (action.kind === 'rotate') {
    const rad = (action.degrees * Math.PI) / 180
    if (action.axis === 'x') wrapper.rotateX(rad)
    else if (action.axis === 'y') wrapper.rotateY(rad)
    else wrapper.rotateZ(rad)
  } else if (action.kind === 'mirror') {
    wrapper.scale[action.axis] *= -1
  } else {
    wrapper.rotation.set(0, 0, 0)
    wrapper.scale.set(1, 1, 1)
  }
  wrapper.updateMatrixWorld(true)
}

/**
 * The live wrapper group, or null before the viewport has mounted.
 *
 * Reached through `window.__three.globalGroup` — the handle the viewport
 * already publishes for first-person mode — because the wrapper is its PARENT:
 * the builders require `globalGroup` to sit at identity, so the room-centring
 * offset and any scene transform live one level up, on the group the viewport
 * calls `content` and passes to the scene builder as `wrapperGroup`.
 */
export function sceneWrapperGroup(): SceneWrapper | null {
  const three = (window as unknown as { __three?: { globalGroup?: THREE.Object3D } }).__three
  const parent = three?.globalGroup?.parent
  return (parent as unknown as SceneWrapper) ?? null
}

/** True when a wrapper was there to transform. */
export function applySceneTransform(action: TransformAction): boolean {
  const wrapper = sceneWrapperGroup()
  if (!wrapper) return false
  applySceneTransformTo(wrapper, action)
  ;(window as unknown as { __viewportRender?: () => unknown }).__viewportRender?.()
  return true
}
