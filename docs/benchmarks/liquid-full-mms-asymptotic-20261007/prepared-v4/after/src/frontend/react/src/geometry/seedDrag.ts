/**
 * Dragging the seed helper in the 3D viewport.
 *
 * The helper has three grabbable parts. The HUB slides the anchor across the
 * surface; either TIP pushes the pair further apart or pulls it in. Until this
 * existed the helper was decoration: React drew it, nothing raycast it, and the
 * anchor could not be changed by any control in the app — the panel prints it
 * as a read-only badge, and the only writable fields were direction and offset.
 *
 * ## Why the anchor matters
 *
 * snappyHexMesh keeps the side of a surface its seed point lands in. The
 * automatic anchor is the surface CENTRE, and on a perforated tile under a rack
 * or a containment panel that meets a wall, the centre is inside solid
 * geometry — so the seed lands in a volume that is not the room, and the mesh
 * comes out missing half the domain with nothing in the solver log to say why.
 * Sliding the anchor to open air is the fix, and dragging it is how the legacy
 * offers that.
 *
 * ## The rigid rod
 *
 * The two tips are not independent. They are `anchor ± direction × offset`, so
 * a tip drag can only change `offset` — the pointer is projected onto the rod's
 * axis and the signed distance becomes the new offset, clamped to `offsetMin`
 * so the pair can never collapse onto the surface. `beginSeedSurfaceDrag`
 * (STL_generate.html:18033) and `updateSeedSurfaceDrag` (:18084) call this the
 * rigid rod model and do the same arithmetic.
 *
 * One deliberate divergence: the legacy pins the rod to the SURFACE NORMAL and
 * writes that normal back as the direction on every drag, which silently throws
 * away a direction the user typed into the panel. This uses the STORED
 * direction instead. It is the axis the arrows are actually drawn along, so a
 * tip follows the pointer instead of jumping, and the two agree whenever the
 * direction is the normal — which it is unless somebody changed it.
 */

import * as THREE from 'three'
import {
  normalizeSeedConfig,
  projectOntoSurfacePlane,
  seedEndpoints,
  type SeedConfig,
  type SeedSurface,
} from './seedConfig'
import { beginSeedEdit, commitSeedEdit, liveSeedConfig, type SeedRestore } from './seedActions'

export type SeedSide = 'anchor' | 'positive' | 'negative'

export interface SeedDragHost {
  camera: THREE.Camera
  domElement: HTMLElement
  orbit?: { enabled: boolean } | null
  /** The surface the inspector is showing — the only one with a helper drawn. */
  surface: () => SeedSurface | null
  /** The hub and the two tips, or an empty list when no helper is up. */
  handles: () => THREE.Object3D[]
  /** Move the drawn helper to this config, without rebuilding it. */
  reposition: (cfg: SeedConfig) => void
  /** Draw a frame — the drag mutates in place, so React is not re-rendering. */
  render: () => void
  /** False while the UI is frozen, the helper is hidden, or another tab is up. */
  enabled: () => boolean
}

export interface SeedDragTools {
  /** Try to take the pointer. True means the drag is ours and the click is not. */
  begin(event: PointerEvent): boolean
  /** Show a grab cursor over a handle. */
  hover(event: PointerEvent): void
  dragging(): boolean
  /**
   * Where a handle sits on screen, or null when no helper is drawn.
   *
   * For verification. The handles are three spheres in a Three.js group with no
   * DOM of their own, so a scripted drag has no way to aim at one — the same
   * reason `__faceDrag` is published. It projects through the drag's OWN
   * camera, so a test that clicks here clicks what a user would.
   */
  screenPositionOf(side: SeedSide): { clientX: number; clientY: number } | null
  dispose(): void
}

interface DragState {
  pointerId: number
  side: SeedSide
  /** Which surface this drag belongs to — abandoned if the selection changes. */
  surfaceKey: string
  restore: SeedRestore
  anchor: THREE.Vector3
  direction: THREE.Vector3
  offset: number
  plane: THREE.Plane
  latest: SeedConfig
  moved: boolean
}

export function seedSurfaceKey(surface: SeedSurface): string {
  return `${surface.owner}:${surface.modelUid}:${surface.solidName}`
}

/**
 * The plane a drag follows.
 *
 * The hub slides ON the surface, so its plane is the surface's. A tip moves
 * along a line, and a line cannot be raycast — so it gets a BILLBOARD plane
 * facing the camera through the tip, and the hit is projected onto the rod
 * afterwards. Without the billboard the pointer would fall off the plane
 * whenever the camera looked along the surface.
 */
export function seedDragPlane(
  surface: SeedSurface,
  cfg: SeedConfig,
  side: SeedSide,
  cameraDirection: THREE.Vector3,
): THREE.Plane {
  const { anchor, positive, negative } = seedEndpoints(cfg)
  if (side === 'anchor') {
    return new THREE.Plane().setFromNormalAndCoplanarPoint(
      new THREE.Vector3(surface.normal.x, surface.normal.y, surface.normal.z).normalize(),
      new THREE.Vector3(anchor.x, anchor.y, anchor.z),
    )
  }
  const tip = side === 'negative' ? negative : positive
  return new THREE.Plane().setFromNormalAndCoplanarPoint(
    cameraDirection.clone().negate().normalize(),
    new THREE.Vector3(tip.x, tip.y, tip.z),
  )
}

/**
 * The config a pointer hit produces — pure, so it can be tested without a scene.
 *
 * `offsetMin` is a floor, not a suggestion: a zero offset puts both seed points
 * ON the surface, and the mesher then has no point in either half.
 */
export function seedConfigForDrag(
  surface: SeedSurface,
  state: { side: SeedSide; anchor: THREE.Vector3; direction: THREE.Vector3; offset: number },
  hit: THREE.Vector3,
): SeedConfig {
  const direction = {
    x: state.direction.x,
    y: state.direction.y,
    z: state.direction.z,
  }
  if (state.side === 'anchor') {
    return normalizeSeedConfig(surface, {
      enabled: true,
      anchor: projectOntoSurfacePlane(surface, { x: hit.x, y: hit.y, z: hit.z }),
      direction,
      offset: state.offset,
    })
  }
  let projected = hit.clone().sub(state.anchor).dot(state.direction)
  // The negative tip sits at `anchor - direction × offset`, so its distance is
  // measured the other way round; without the flip, dragging it outward would
  // read as a negative offset and clamp to the minimum every frame.
  if (state.side === 'negative') projected = -projected
  return normalizeSeedConfig(surface, {
    enabled: true,
    anchor: { x: state.anchor.x, y: state.anchor.y, z: state.anchor.z },
    direction,
    offset: Math.max(Math.abs(projected), surface.offsetMin),
  })
}

export function createSeedDragTools(host: SeedDragHost): SeedDragTools {
  const raycaster = new THREE.Raycaster()
  const pointer = new THREE.Vector2()
  let drag: DragState | null = null
  let hovering = false

  function setRay(event: { clientX: number; clientY: number }) {
    const rect = host.domElement.getBoundingClientRect()
    pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1
    pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1
    raycaster.setFromCamera(pointer, host.camera)
  }

  function handleUnder(event: { clientX: number; clientY: number }): SeedSide | null {
    const handles = host.handles()
    if (!handles.length) return null
    setRay(event)
    const hits = raycaster.intersectObjects(handles, false)
    const side = hits[0]?.object.userData?.seedSide
    return side === 'anchor' || side === 'positive' || side === 'negative' ? side : null
  }

  function onMove(event: PointerEvent) {
    if (!drag || event.pointerId !== drag.pointerId) return
    const surface = host.surface()
    // The selection changed under the drag — a tree click, an undo. Committing
    // onto whatever is selected now would move the wrong surface's seed.
    if (!surface || seedSurfaceKey(surface) !== drag.surfaceKey) return
    setRay(event)
    const hit = new THREE.Vector3()
    if (!raycaster.ray.intersectPlane(drag.plane, hit)) return
    drag.latest = seedConfigForDrag(surface, drag, hit)
    drag.moved = true
    liveSeedConfig(surface, drag.latest)
    host.reposition(drag.latest)
    host.render()
  }

  function finish() {
    const state = drag
    drag = null
    if (!state) return
    window.removeEventListener('pointermove', onMove)
    window.removeEventListener('pointerup', onUp)
    try {
      host.domElement.releasePointerCapture?.(state.pointerId)
    } catch {
      // Best effort — the window listeners above carried the drag regardless.
    }
    if (host.orbit) host.orbit.enabled = true
    host.domElement.style.cursor = ''
    const surface = host.surface()
    if (!surface || seedSurfaceKey(surface) !== state.surfaceKey) return
    // A click that never moved is not an edit. Committing it would push an undo
    // entry, mark the project dirty and turn the surface custom, all for a
    // pointer that went down and up on the hub.
    if (!state.moved) {
      liveSeedConfig(surface, surface.seedConfig)
      return
    }
    commitSeedEdit(surface, state.restore, state.latest)
  }

  function onUp(event: PointerEvent) {
    if (!drag || event.pointerId !== drag.pointerId) return
    finish()
  }

  return {
    begin(event: PointerEvent): boolean {
      if (drag || event.button !== 0 || !host.enabled()) return false
      const surface = host.surface()
      if (!surface) return false
      const side = handleUnder(event)
      if (!side) return false

      const cfg = normalizeSeedConfig(surface, surface.seedConfig)
      const { anchor } = seedEndpoints(cfg)
      const cameraDirection = new THREE.Vector3()
      host.camera.getWorldDirection(cameraDirection)
      drag = {
        pointerId: event.pointerId,
        side,
        surfaceKey: seedSurfaceKey(surface),
        restore: beginSeedEdit(surface),
        anchor: new THREE.Vector3(anchor.x, anchor.y, anchor.z),
        direction: new THREE.Vector3(cfg.direction.x, cfg.direction.y, cfg.direction.z).normalize(),
        offset: cfg.offset,
        plane: seedDragPlane(surface, cfg, side, cameraDirection),
        latest: cfg,
        moved: false,
      }
      if (host.orbit) host.orbit.enabled = false
      try {
        host.domElement.setPointerCapture?.(event.pointerId)
      } catch {
        // Best effort, as above.
      }
      host.domElement.style.cursor = 'grabbing'
      window.addEventListener('pointermove', onMove)
      window.addEventListener('pointerup', onUp)
      return true
    },

    hover(event: PointerEvent) {
      if (drag || !host.enabled()) return
      const over = handleUnder(event) !== null
      if (over === hovering) return
      hovering = over
      // Only ever RELEASES a cursor it set: the face-drag tools own the same
      // property, and clearing theirs on every mouse move would kill their
      // resize cursors.
      if (over) host.domElement.style.cursor = 'grab'
      else if (host.domElement.style.cursor === 'grab') host.domElement.style.cursor = ''
    },

    dragging: () => drag !== null,

    screenPositionOf(side: SeedSide) {
      const handle = host.handles().find((h) => h.userData?.seedSide === side)
      if (!handle) return null
      const rect = host.domElement.getBoundingClientRect()
      const ndc = handle.getWorldPosition(new THREE.Vector3()).project(host.camera)
      return {
        clientX: rect.left + ((ndc.x + 1) / 2) * rect.width,
        clientY: rect.top + ((1 - ndc.y) / 2) * rect.height,
      }
    },

    dispose() {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      drag = null
    },
  }
}
