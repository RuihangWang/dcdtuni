/**
 * The shared translate gizmo.
 *
 * A port of `createTranslateGizmo` (STL_generate.html:16045), which the legacy
 * editor uses for the model move tool, the pipe-junction waypoint ball and the
 * streamline seed sphere alike. One implementation for all three is what keeps
 * them behaving identically — the hand-rolled arrows it replaced tracked the
 * cursor only approximately, and the grabbed handle drifted away from the
 * pointer as the camera angle changed.
 *
 * Two things are easy to get wrong and are the reason this is a module:
 *
 * - **Add the HELPER, not the controls.** Since three r169 TransformControls is
 *   no longer an Object3D; adding it to the scene throws, and the fix that
 *   looks obvious — skipping the add — leaves an invisible gizmo that still
 *   swallows clicks.
 * - **Freeze the orbit while dragging.** Without the `dragging-changed`
 *   handler, dragging a handle also orbits the camera, so the object appears
 *   to fly away from the pointer.
 */

import * as THREE from 'three'
import { TransformControls } from 'three/examples/jsm/controls/TransformControls.js'
import type { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'

export interface TranslateGizmoOptions {
  space?: 'world' | 'local'
  size?: number
  axes?: { x?: boolean; y?: boolean; z?: boolean }
  /** Disabled while a handle is held, so the drag does not orbit the camera. */
  orbit?: OrbitControls | null
  /**
   * The three callbacks are handed the object the gizmo is CURRENTLY on.
   *
   * A caller that re-`attach`es — the streamline seed sphere is rebuilt on
   * every post-processing apply — otherwise has to close over the target it saw
   * when the gizmo was created, and that object is disposed and out of the
   * scene by the second drag. The symptom is silent: the handles still move a
   * sphere, the callback still fires, and it reports the dead object's
   * unchanged position, so the drag reads as "the sphere snaps back".
   */
  onDragStart?: (target: THREE.Object3D | null) => void
  onChange?: (target: THREE.Object3D | null) => void
  onDragEnd?: (target: THREE.Object3D | null) => void
}

export interface TranslateGizmo {
  controls: TransformControls
  helper: THREE.Object3D
  attach(target: THREE.Object3D): void
  detach(): void
  setAxes(axes: { x?: boolean; y?: boolean; z?: boolean }): void
  setSize(size: number): void
  setVisible(visible: boolean): void
  dispose(): void
}

export function createTranslateGizmo(
  scene: THREE.Scene,
  camera: THREE.Camera,
  domElement: HTMLElement,
  target: THREE.Object3D | null,
  opts: TranslateGizmoOptions = {},
): TranslateGizmo {
  const controls = new TransformControls(camera, domElement)
  controls.setMode('translate')
  controls.setSpace(opts.space ?? 'world')
  if (opts.size != null) controls.setSize(opts.size)
  const helper = controls.getHelper()
  scene.add(helper)
  if (target) controls.attach(target)

  const setAxes = (axes?: { x?: boolean; y?: boolean; z?: boolean }) => {
    if (!axes) return
    controls.showX = axes.x !== false
    controls.showY = axes.y !== false
    controls.showZ = axes.z !== false
  }
  setAxes(opts.axes)

  const onDragging = (e: { value: boolean }) => {
    if (opts.orbit) opts.orbit.enabled = !e.value
    // `controls.object` is what TransformControls actually moved, which is the
    // last thing `attach` was called with — not whatever the caller captured.
    if (e.value) opts.onDragStart?.(controls.object ?? null)
    else opts.onDragEnd?.(controls.object ?? null)
  }
  const onObjectChange = () => opts.onChange?.(controls.object ?? null)
  controls.addEventListener('dragging-changed', onDragging as never)
  controls.addEventListener('objectChange', onObjectChange as never)

  return {
    controls,
    helper,
    attach(t) {
      controls.attach(t)
    },
    detach() {
      controls.detach()
    },
    setAxes,
    setSize(size) {
      controls.setSize(size)
    },
    setVisible(visible) {
      helper.visible = visible
      controls.enabled = visible
    },
    dispose() {
      controls.removeEventListener('dragging-changed', onDragging as never)
      controls.removeEventListener('objectChange', onObjectChange as never)
      try {
        controls.detach()
      } catch {
        // Detaching an already-detached control throws in some versions; the
        // gizmo is being torn down either way.
      }
      helper.parent?.remove(helper)
      controls.dispose()
      // Re-enable orbiting: disposing mid-drag would otherwise leave the camera
      // frozen with no gizmo left to release it.
      if (opts.orbit) opts.orbit.enabled = true
    },
  }
}


/**
 * Publish the gizmo the way the legacy page publishes it.
 *
 * `js/liquid_mode.js` builds its pipe-waypoint gizmo through
 * `window.createTranslateGizmo(target, opts)` and checks for
 * `THREE.TransformControls` first — neither of which exists in a React app,
 * where TransformControls is an ES import and the gizmo is a module function.
 *
 * Without this the module's `_ensurePipeWaypointTC` returns null and
 * `_attachPipeWaypointGizmo` becomes a silent no-op: the click is CONSUMED
 * (handleControlPointClick returns true) and no handles appear, which reads as
 * "the control points are not clickable" rather than as a missing global.
 *
 * The returned shape is the legacy one — `{ tc, helper, attach, … }` — because
 * that module stores `.tc` and `.helper` directly.
 */
export function installLegacyGizmoBridge(
  scene: THREE.Scene,
  camera: THREE.Camera,
  domElement: HTMLElement,
  orbit: OrbitControls | null,
) {
  const w = window as unknown as Record<string, unknown>
  const three = w.THREE as { TransformControls?: unknown } | undefined
  if (three && !three.TransformControls) three.TransformControls = TransformControls
  // Re-installed on every call, deliberately. React StrictMode runs the
  // viewport's setup effect TWICE in development: the first scene is created,
  // torn down and replaced. An install-once guard here would leave the closure
  // holding that discarded scene, so the gizmo would be built and added to a
  // scene nothing renders — the handles simply never appear, and the click that
  // asked for them is still consumed.
  w.createTranslateGizmo = (
    target: THREE.Object3D | null,
    opts: TranslateGizmoOptions = {},
  ) => {
    const gizmo = createTranslateGizmo(scene, camera, domElement, target, { ...opts, orbit })
    return {
      tc: gizmo.controls,
      helper: gizmo.helper,
      attach: (t: THREE.Object3D) => gizmo.attach(t),
      detach: () => gizmo.detach(),
      setAxes: gizmo.setAxes,
      setSize: gizmo.setSize,
      setVisible: gizmo.setVisible,
      dispose: () => gizmo.dispose(),
    }
  }
}
