/**
 * The model move gizmo: drag arrows that move a model in the scene.
 *
 * A port of the legacy `createMoveGizmo` / `_onMoveGizmoDrag*`
 * (STL_generate.html:16103). The parts that matter and are easy to lose:
 *
 * - **The pivot is a proxy.** TransformControls moves the object it is attached
 *   to; attaching it to the model's render root would move the RENDERED group
 *   while `geometry.location` stayed put, and the next rebuild would snap the
 *   model back. An empty pivot at the model's OBB centre is dragged instead,
 *   and its delta is applied to the anchor.
 *
 * - **The pivot lives under globalGroup**, which has an identity transform, so
 *   the pivot's local position is in the SAME space as `geometry.location` and
 *   the delta can be added directly.
 *
 * - **A model is more than its anchor.** What moves is decided by
 *   `groupTranslate` — a pipe segment's waypoints and valves included — because
 *   the typed minimum-point box and this drag are the same gesture and must not
 *   disagree about it.
 *
 * - **One undo entry per drag.** The drag mutates the project live — sixty
 *   `edit()` calls a second would each clone the project and push an undo entry
 *   — then restores the starting placement and commits ONCE on release.
 *
 * - **The commit re-finds every model by uid.** `edit()` clones the project, so
 *   the model objects the drag captured belong to the version that was just
 *   replaced. Writing the group's final positions through those captured
 *   references landed them in a discarded object and every co-moved model
 *   snapped back to where the drag started, leaving only the anchor moved.
 *
 * - **Invalid positions are reverted per frame, not on release.** A drag that
 *   ends inside another rack has to be impossible, not just reported.
 */

import * as THREE from 'three'
import type { Construction } from '../schema/cfdproj'
import { getModelOBB, type TransformToolsApi } from '../legacy/transformTools'
import * as store from '../projectStore'
import { createTranslateGizmo, type TranslateGizmo } from './translateGizmo'
import {
  applyPlacement,
  capturePlacement,
  hasPipeGeometry,
  type Vec3,
} from './groupTranslate'
import { redrawPipes } from './pipeRedraw'
import type { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'

/**
 * Re-exported so the rigid-move rule keeps ONE name across the app: it lives
 * with the rest of the translation logic now, and this gizmo is where callers
 * looked for it.
 */
export { followDelta } from './groupTranslate'

export interface MoveGizmoHost {
  scene: THREE.Scene
  camera: THREE.Camera
  domElement: HTMLElement
  /** Identity-transform parent, so pivot coordinates ARE model coordinates. */
  globalGroup: THREE.Group
  orbit: OrbitControls | null
  tools: TransformToolsApi
  /** The model being moved, re-read on demand so it is never a stale copy. */
  model(): Construction | null
  /**
   * The other selected models, moved by the SAME delta.
   *
   * Multi-move is one gizmo on the active model, not one per model: eight
   * arrows in the same place cannot be aimed at, and the legacy's group move
   * translates the selection rigidly (`setMultiMoveMinPoint`,
   * STL_generate.html:10931).
   */
  others?(): Construction[]
  /** Move the rendered group to match the model's current anchor. */
  syncRenderTransform(uid: string): void
  /** Snap settings from the toolbar. */
  snap(): { face: boolean; edge: boolean; grid: boolean }
}

export interface MoveGizmo {
  /** Re-centre on the model when it changed outside a drag. */
  refresh(): void
  dragging(): boolean
  /**
   * How many models this gizmo moves — 1 plus the co-selected ones.
   *
   * Exposed so a browser check can confirm the multi-move WIRING without
   * having to synthesise a TransformControls drag, whose axis picking depends
   * on exact raycast hits.
   */
  groupSize(): number
  dispose(): void
}

/** One model in the moving group, with the placement it had at drag start. */
interface Member {
  model: Construction
  uid: string
  start: Vec3[]
}

interface DragState {
  pivotStart: THREE.Vector3
  /** The anchor's own start, which the frame arithmetic is measured against. */
  start: Vec3
  axes: { x: boolean; y: boolean; z: boolean }
  /** The anchor first, then every co-moved model. */
  members: Member[]
  /** Whether the group holds a pipe, so the tubes are only redrawn when it does. */
  pipes: boolean
}

export function createMoveGizmo(host: MoveGizmoHost): MoveGizmo | null {
  const model = host.model()
  if (!model) return null
  const axes = (model as unknown as { moveTool?: { axes?: { x?: boolean; y?: boolean; z?: boolean } } })
    .moveTool?.axes ?? { x: true, y: true, z: true }

  const pivot = new THREE.Object3D()
  pivot.position.copy(getModelOBB(model).center)
  host.globalGroup.add(pivot)
  pivot.updateMatrixWorld(true)

  let drag: DragState | null = null

  /**
   * Put the whole group at `start + delta` and tell the scene about it.
   *
   * Live mutation, no undo entry: a drag emits a position every frame.
   */
  function place(d: DragState, delta: Vec3) {
    store.mutateLive(() => {
      for (const member of d.members) applyPlacement(member.model, member.start, delta)
    })
    for (const member of d.members) {
      // The bounds cache is keyed by model id and feeds the snap and the
      // collision test; leaving it stale would compare the new position
      // against the old box.
      host.tools.invalidateBounds(member.uid)
      host.syncRenderTransform(member.uid)
    }
  }

  /** The delta the group currently stands at, measured from the anchor. */
  function currentDelta(d: DragState): Vec3 {
    const loc = d.members[0]!.model.geometry?.location as Vec3
    return { x: loc.x - d.start.x, y: loc.y - d.start.y, z: loc.z - d.start.z }
  }

  function applyFrame(d: DragState) {
    const offset = pivot.position.clone().sub(d.pivotStart)
    const previous = currentDelta(d)
    place(d, {
      x: d.axes.x ? offset.x : 0,
      y: d.axes.y ? offset.y : 0,
      z: d.axes.z ? offset.z : 0,
    })

    const group = d.members.map((member) => member.model)
    const ids = d.members.map((member) => member.uid)
    if (host.tools.analyticalCollisionCheck(group, ids) || !host.tools.areModelsWithinProjectBounds(ids)) {
      // Reverted to the last position that WAS valid, not to the drag's
      // start: the user should be able to slide along a rack rather than
      // have the model jump home the moment it touches one.
      place(d, previous)
      return
    }

    const s = host.snap()
    if (!s.face && !s.edge && !s.grid) return
    const snap = s.grid
      ? host.tools.computeGridSnap(ids)
      : host.tools.computeProximitySnap(ids, s.face, s.edge)
    const hasSnap =
      (d.axes.x && Math.abs(snap.x) > 1e-8) ||
      (d.axes.y && Math.abs(snap.y) > 1e-8) ||
      (d.axes.z && Math.abs(snap.z) > 1e-8)
    if (!hasSnap) return

    const preSnap = currentDelta(d)
    place(d, {
      x: preSnap.x + (d.axes.x ? snap.x : 0),
      y: preSnap.y + (d.axes.y ? snap.y : 0),
      z: preSnap.z + (d.axes.z ? snap.z : 0),
    })
    if (host.tools.analyticalCollisionCheck(group, ids) || !host.tools.areModelsWithinProjectBounds(ids)) {
      place(d, preSnap)
      return
    }
    // Rounded to the micrometre: a snap should produce 3.6, not
    // 3.5999999999999996, or the number in the inspector reads as noise and the
    // .cfdproj carries float dust for ever. The DELTA is what is rounded, so
    // every model in the group lands on the same clean offset from a start that
    // the previous edit already rounded.
    const loc = d.members[0]!.model.geometry?.location as Vec3
    place(d, {
      x: Math.round(loc.x * 1e6) / 1e6 - d.start.x,
      y: Math.round(loc.y * 1e6) / 1e6 - d.start.y,
      z: Math.round(loc.z * 1e6) / 1e6 - d.start.z,
    })
  }

  const gizmo: TranslateGizmo = createTranslateGizmo(
    host.scene,
    host.camera,
    host.domElement,
    pivot,
    {
      space: 'world',
      axes,
      orbit: host.orbit,
      onDragStart() {
        const m = host.model()
        if (!m) return
        host.tools.initSnapCache()
        // Captured ONCE, at the start: reading each model's current position
        // per frame would compound the delta and the group would fly apart.
        const members: Member[] = [{ model: m, uid: m.uid, start: capturePlacement(m) }]
        for (const other of host.others?.() ?? []) {
          if (other.uid === m.uid) continue
          members.push({ model: other, uid: other.uid, start: capturePlacement(other) })
        }
        drag = {
          pivotStart: pivot.position.clone(),
          start: { ...(m.geometry?.location as Vec3) },
          axes: { x: axes.x !== false, y: axes.y !== false, z: axes.z !== false },
          members,
          pipes: hasPipeGeometry(members.map((member) => member.model)),
        }
      },
      onChange() {
        if (!drag || !host.model()) return
        applyFrame(drag)
        // The tubes are swept from the waypoints this frame just moved, so
        // without this the pipe stays drawn where the drag started.
        if (drag.pipes) redrawPipes()
      },
      onDragEnd() {
        const d = drag
        drag = null
        host.tools.clearSnapCache()
        if (!d || !host.model()) return
        const delta = currentDelta(d)
        // Put every point back before committing, so the snapshot `edit` takes
        // is the placement the drag STARTED from. Without this the live
        // mutations have already moved it and undo restores nothing.
        place(d, { x: 0, y: 0, z: 0 })

        const moved =
          Math.abs(delta.x) > 1e-9 || Math.abs(delta.y) > 1e-9 || Math.abs(delta.z) > 1e-9
        if (moved) {
          // ONE edit for the whole group: one undo entry for one drag, and —
          // because `edit` hands the mutation a CLONE — every model re-found by
          // uid inside it rather than written through the captured reference,
          // which now belongs to the replaced project.
          const detail =
            d.members.length > 1
              ? `${d.members.length} models`
              : `${d.members[0]!.uid} → ${(d.start.x + delta.x).toFixed(4)}, ${(d.start.y + delta.y).toFixed(4)}, ${(d.start.z + delta.z).toFixed(4)}`
          store.edit(d.members.length > 1 ? 'moveModels' : 'moveModel', detail, (draft) => {
            for (const member of d.members) {
              const found = store.findConstruction(draft, member.uid)
              if (found) applyPlacement(found.model, member.start, delta)
            }
          })
        }
        for (const member of d.members) {
          host.syncRenderTransform(member.uid)
          host.tools.invalidateBounds(member.uid)
        }
        if (d.pipes) redrawPipes()
      },
    },
  )

  return {
    groupSize: () => 1 + (host.others?.().filter((o) => o.uid !== model.uid).length ?? 0),
    refresh() {
      if (drag) return // never move the pivot mid-drag; TransformControls owns it
      const m = host.model()
      if (!m) return
      pivot.position.copy(getModelOBB(m).center)
      pivot.updateMatrixWorld(true)
    },
    dragging() {
      return !!drag
    },
    dispose() {
      gizmo.dispose()
      pivot.parent?.remove(pivot)
    },
  }
}
