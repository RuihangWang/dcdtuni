/**
 * The rotate gizmo: three rings around a model, dragged to turn it.
 *
 * A port of `createRotationGizmo` / `beginRotationGizmoDrag` /
 * `updateRotationGizmoDrag` / `endRotationGizmoDrag` (STL_generate.html:15662,
 * 16023, 16084, 16155). The math lives in `rotateGizmo.ts` and is tested there;
 * this file is the scene objects and the pointer plumbing.
 *
 * Why rings rather than TransformControls, which the move tool uses: three's
 * rotate gizmo adds an E ring and a free-rotation sphere the legacy does not
 * have, and its snapping is its own. The rings are also the visual the user
 * knows — yaw cyan, pitch red, roll green, one per ENABLED axis.
 *
 * The parts that carry the behaviour:
 *
 * - **The drag reads an angle on a PLANE, not a screen delta.** The ring's axis
 *   is the plane normal through the gizmo centre, so the grabbed point stays
 *   under the pointer however the camera is oriented.
 * - **Every rotate-enabled model in the selection turns about its OWN centre.**
 *   A shared pivot would swing a row of racks around one of them; the legacy
 *   spins each in place (`dragState._rotateTargets`).
 * - **One undo entry per drag.** The drag mutates live, then puts every
 *   orientation back and commits once, so undo spans the whole drag.
 */

import * as THREE from 'three'
import type { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import type { Construction } from '../schema/cfdproj'
import { registerHelper, unregisterHelper } from '../geometry/helperRegistry'
import { getModelOBB } from '../legacy/transformTools'
import * as store from '../projectStore'
import {
  angleOnPlane,
  axisVector,
  handlePosition,
  normaliseDelta,
  ringRotation,
  rotateAnchor,
  rotateQuaternion,
  snapDelta,
  type RotationAxis,
} from './rotateGizmo'

/** Yaw is the vertical axis in this Y-up scene; roll is Z. */
const AXIS_OF: Record<'yaw' | 'pitch' | 'roll', { axis: RotationAxis; color: number }> = {
  yaw: { axis: 'y', color: 0x00d4ff },
  pitch: { axis: 'x', color: 0xff4444 },
  roll: { axis: 'z', color: 0x44ff44 },
}

export interface RotateGizmoHost {
  /** Identity-transform parent, so ring coordinates ARE model coordinates. */
  parent: THREE.Object3D
  camera: THREE.Camera
  domElement: HTMLElement
  /** Frozen while a ring is held, so the drag does not orbit the camera. */
  orbit: OrbitControls | null
  /** The rotate-enabled models in the selection, re-read so none is stale. */
  models(): Construction[]
  /** Move the rendered group to match the model's anchor and orientation. */
  syncRenderTransform(uid: string): void
  /** 旋转吸附 — the toolbar flag that quantises the drag to whole degrees. */
  degreeSnap(): boolean
  requestRender(): void
  invalidateBounds(uid: string): void
}

export interface RotateGizmoHandle {
  /** Re-centre the rings on the models when the project changed outside a drag. */
  refresh(): void
  dragging(): boolean
  /** Offer a pointerdown to the rings; true if one was grabbed. */
  begin(event: PointerEvent): boolean
  /** How many models a drag will turn — for the parity harness. */
  groupSize(): number
  /** How many rings are on screen — for the parity harness. */
  ringCount(): number
  /**
   * Each ring as plain data: which model, which axis, where and how big.
   *
   * Browser verification has to work out where a ring IS on screen before it
   * can synthesise a drag along one, and the meshes live inside this closure.
   */
  rings(): { uid: string; axis: RotationAxis; centre: Vec3; radius: number }[]
  dispose(): void
}

interface Ring {
  axis: RotationAxis
  visual: THREE.Mesh<THREE.TorusGeometry, THREE.MeshBasicMaterial>
  hit: THREE.Mesh<THREE.TorusGeometry, THREE.MeshBasicMaterial>
  handle: THREE.Mesh<THREE.SphereGeometry, THREE.MeshBasicMaterial>
}

interface Gizmo {
  uid: string
  centre: THREE.Vector3
  radius: number
  rings: Ring[]
}

type Vec3 = { x: number; y: number; z: number }
type Quat = { x: number; y: number; z: number; w: number }

/**
 * Where a ring's handle sits for the model's CURRENT orientation.
 *
 * Read off the Euler decomposition, as the legacy does (`_getCurrentAxisAngle`):
 * a quaternion has no per-axis angle of its own, and the handle is only a
 * grab point — its exact placement under a compound rotation is cosmetic.
 */
function currentAxisAngle(q: Quat, axis: RotationAxis): number {
  const euler = store.quaternionToEulerDeg(q)
  const deg = axis === 'y' ? euler.y : axis === 'x' ? euler.x : euler.z
  return (deg * Math.PI) / 180
}

function rotateToolOf(model: Construction) {
  return (
    (model as unknown as {
      rotateTool?: { enabled?: boolean; axes?: { yaw?: boolean; pitch?: boolean; roll?: boolean } }
    }).rotateTool ?? null
  )
}

export function createRotateGizmo(host: RotateGizmoHost): RotateGizmoHandle | null {
  const gizmos: Gizmo[] = []

  const build = () => {
    for (const model of host.models()) {
      const tool = rotateToolOf(model)
      if (!tool?.enabled) continue
      const wanted = (['yaw', 'pitch', 'roll'] as const).filter((k) => tool.axes?.[k])
      if (!wanted.length) continue

      const obb = getModelOBB(model)
      const diag = Math.hypot(obb.half[0], obb.half[1], obb.half[2]) * 2
      // A floor-thin baffle would otherwise get a ring too small to aim at.
      const radius = Math.max(diag * 0.6, 0.3)
      const q = (model.geometry?.quaternion as Quat) ?? { x: 0, y: 0, z: 0, w: 1 }

      const rings = wanted.map((key) => {
        const { axis, color } = AXIS_OF[key]
        // depthTest off + a high renderOrder: a ring inside a rack must still
        // be grabbable, or the tool is unusable on anything but an empty room.
        const visual = new THREE.Mesh(
          new THREE.TorusGeometry(radius, 0.015, 8, 64),
          new THREE.MeshBasicMaterial({
            color,
            transparent: true,
            opacity: 0.6,
            depthTest: false,
            side: THREE.DoubleSide,
          }),
        )
        visual.position.copy(obb.center)
        visual.rotation.copy(ringRotation(axis))
        visual.renderOrder = 999
        visual.userData = { rotateGizmoAxis: axis, rotateGizmoModelId: model.uid }
        host.parent.add(visual)

        // A 15 mm-thick ring is a 3-pixel target at room scale; the invisible
        // 80 mm torus is what the pointer actually hits.
        const hit = new THREE.Mesh(
          new THREE.TorusGeometry(radius, 0.08, 8, 48),
          new THREE.MeshBasicMaterial({ visible: false, side: THREE.DoubleSide }),
        )
        hit.position.copy(obb.center)
        hit.rotation.copy(ringRotation(axis))
        hit.renderOrder = 998
        hit.userData = { rotateGizmoAxis: axis, rotateGizmoModelId: model.uid }
        host.parent.add(hit)

        const handle = new THREE.Mesh(
          new THREE.SphereGeometry(1, 16, 16),
          new THREE.MeshBasicMaterial({ color, depthTest: false }),
        )
        // WP-S3: a UNIT sphere at the legacy 50 mm handle radius, handed to
        // the shared registry so it shrinks with the camera instead of
        // staying 50 mm across the room. The scale write is the initial
        // condition (and the fallback look while the registry is absent);
        // once registered, the per-frame sync owns it. Raycasts read the
        // scale, so the pick target follows what is drawn.
        handle.scale.setScalar(0.05)
        registerHelper(handle, 0.05)
        handle.renderOrder = 1000
        handle.userData = { rotateGizmoAxis: axis, rotateGizmoModelId: model.uid }
        handle.position.copy(handlePosition(obb.center, radius, currentAxisAngle(q, axis), axis))
        host.parent.add(handle)

        return { axis, visual, hit, handle }
      })

      gizmos.push({ uid: model.uid, centre: obb.center.clone(), radius, rings })
    }
  }
  build()
  if (!gizmos.length) return null

  const raycaster = new THREE.Raycaster()
  const pointer = new THREE.Vector2()

  let drag: {
    pointerId: number
    axis: RotationAxis
    /** The grabbed ring's plane, fixed for the drag. */
    plane: THREE.Plane
    centre: THREE.Vector3
    startAngle: number
    targets: { model: Construction; centre: THREE.Vector3; quaternion: Quat; location: Vec3 }[]
  } | null = null

  const setPointer = (event: PointerEvent) => {
    const rect = host.domElement.getBoundingClientRect()
    pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1
    pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1
    raycaster.setFromCamera(pointer, host.camera as THREE.PerspectiveCamera)
  }

  const label = () => {
    let el = document.getElementById('rotate-angle-label')
    if (!el) {
      el = document.createElement('div')
      el.id = 'rotate-angle-label'
      el.style.cssText =
        'position:fixed;pointer-events:none;font:13px "Share Tech Mono",monospace;color:#ffcc00;' +
        'background:rgba(0,0,0,0.7);padding:2px 8px;border-radius:4px;z-index:9999;'
      document.body.appendChild(el)
    }
    return el
  }
  const dropLabel = () => document.getElementById('rotate-angle-label')?.remove()

  const positionRings = () => {
    // Mid-drag only the grabbed ring's handle follows: the other handles are
    // read off an Euler decomposition that drifts under a compound rotation,
    // and watching them wander while turning one axis reads as a bug.
    const active = drag?.axis ?? null
    for (const gizmo of gizmos) {
      const model = host.models().find((m) => m.uid === gizmo.uid)
      if (!model) continue
      const obb = getModelOBB(model)
      gizmo.centre.copy(obb.center)
      const q = (model.geometry?.quaternion as Quat) ?? { x: 0, y: 0, z: 0, w: 1 }
      for (const ring of gizmo.rings) {
        ring.visual.position.copy(obb.center)
        ring.hit.position.copy(obb.center)
        if (!active || ring.axis === active) {
          ring.handle.position.copy(
            handlePosition(obb.center, gizmo.radius, currentAxisAngle(q, ring.axis), ring.axis),
          )
        }
      }
    }
  }

  const apply = (deltaAngle: number) => {
    if (!drag) return
    store.mutateLive(() => {
      for (const target of drag!.targets) {
        const geometry = target.model.geometry as unknown as {
          quaternion?: Quat
          location?: Vec3
        }
        if (!geometry) continue
        geometry.quaternion = rotateQuaternion(target.quaternion, drag!.axis, deltaAngle)
        if (geometry.location) {
          // The model turns about its OBB CENTRE, so its anchor — a corner on
          // a rack — has to travel with it. Turning about the anchor instead
          // would swing the rack across the room.
          const moved = rotateAnchor(target.location, target.centre, drag!.axis, deltaAngle)
          geometry.location.x = moved.x
          geometry.location.y = moved.y
          geometry.location.z = moved.z
        }
      }
    })
    for (const target of drag.targets) {
      host.invalidateBounds(target.model.uid)
      host.syncRenderTransform(target.model.uid)
    }
    positionRings()
    host.requestRender()
  }

  const onMove = (event: PointerEvent) => {
    if (!drag) return
    setPointer(event)
    const point = new THREE.Vector3()
    if (!raycaster.ray.intersectPlane(drag.plane, point)) return
    const raw = angleOnPlane(point, drag.centre, drag.axis) - drag.startAngle
    const delta = snapDelta(normaliseDelta(raw), event.shiftKey, host.degreeSnap())
    apply(delta)
    const el = label()
    el.textContent = `${((delta * 180) / Math.PI).toFixed(1)}°`
    el.style.left = `${event.clientX + 15}px`
    el.style.top = `${event.clientY - 25}px`
  }

  const onUp = () => {
    if (!drag) return
    const finished = drag
    const finals = finished.targets.map((t) => ({
      uid: t.model.uid,
      model: t.model,
      start: t,
      quaternion: { ...((t.model.geometry?.quaternion as Quat) ?? t.quaternion) },
      location: { ...((t.model.geometry?.location as Vec3) ?? t.location) },
    }))
    drag = null

    if (host.orbit) host.orbit.enabled = true
    try {
      host.domElement.releasePointerCapture(finished.pointerId)
    } catch {
      /* the pointer may already be gone */
    }
    host.domElement.removeEventListener('pointermove', onMove)
    host.domElement.removeEventListener('pointerup', onUp)
    host.domElement.removeEventListener('pointercancel', onUp)
    dropLabel()
    for (const ring of gizmos.flatMap((g) => g.rings)) ring.visual.material.opacity = 0.6

    const turned = finals.some(
      (f) =>
        Math.abs(f.quaternion.x - f.start.quaternion.x) > 1e-9 ||
        Math.abs(f.quaternion.y - f.start.quaternion.y) > 1e-9 ||
        Math.abs(f.quaternion.z - f.start.quaternion.z) > 1e-9 ||
        Math.abs(f.quaternion.w - f.start.quaternion.w) > 1e-9,
    )
    if (!turned) {
      positionRings()
      host.requestRender()
      return
    }
    // Put every orientation back BEFORE the checkpoint, so the snapshot undo
    // restores is the one the drag started from — the live mutations have
    // already turned the models.
    store.mutateLive(() => {
      for (const f of finals) {
        const geometry = f.model.geometry as unknown as { quaternion?: Quat; location?: Vec3 }
        if (!geometry) continue
        geometry.quaternion = { ...f.start.quaternion }
        if (geometry.location) {
          geometry.location.x = f.start.location.x
          geometry.location.y = f.start.location.y
          geometry.location.z = f.start.location.z
        }
      }
    })
    store.pushUndoCheckpoint('modelRotate3D')
    // The checkpoint CLONES the project, so the models captured at drag start
    // now belong to the undo SNAPSHOT. Writing the final orientation through
    // those references landed it in the snapshot and left the live project
    // untouched — the drag looked right until the mouse came up, then the
    // model snapped back. Re-resolve every model by uid on the live project.
    store.mutateLive((project) => {
      for (const f of finals) {
        const model = store.findConstruction(project, f.uid)?.model
        const geometry = model?.geometry as unknown as { quaternion?: Quat; location?: Vec3 }
        if (!geometry) continue
        geometry.quaternion = { ...f.quaternion }
        if (geometry.location) {
          geometry.location.x = f.location.x
          geometry.location.y = f.location.y
          geometry.location.z = f.location.z
        }
      }
    })
    store.notifyLiveEdit('modelRotate3D', `${finals.length} model(s)`)
    for (const f of finals) {
      host.invalidateBounds(f.uid)
      host.syncRenderTransform(f.uid)
    }
    positionRings()
    host.requestRender()
  }

  return {
    groupSize: () => gizmos.length,
    ringCount: () => gizmos.reduce((n, g) => n + g.rings.length, 0),
    rings: () =>
      gizmos.flatMap((g) =>
        g.rings.map((r) => ({
          uid: g.uid,
          axis: r.axis,
          centre: { x: g.centre.x, y: g.centre.y, z: g.centre.z },
          radius: g.radius,
        })),
      ),
    refresh() {
      if (drag) return
      positionRings()
    },
    dragging: () => !!drag,
    begin(event: PointerEvent) {
      if (drag || event.button !== 0) return false
      setPointer(event)
      const targets = gizmos.flatMap((g) => g.rings.flatMap((r) => [r.hit, r.handle]))
      const hits = raycaster.intersectObjects(targets, false)
      const first = hits[0]
      if (!first) return false
      const axis = (first.object.userData as { rotateGizmoAxis?: RotationAxis }).rotateGizmoAxis
      const uid = (first.object.userData as { rotateGizmoModelId?: string }).rotateGizmoModelId
      const gizmo = gizmos.find((g) => g.uid === uid)
      if (!axis || !gizmo) return false

      const plane = new THREE.Plane().setFromNormalAndCoplanarPoint(axisVector(axis), gizmo.centre)
      const point = new THREE.Vector3()
      if (!raycaster.ray.intersectPlane(plane, point)) return false

      // Captured ONCE: every frame reads these starts, so a rotation is
      // always measured from where the drag began and cannot compound.
      const targetsForDrag = host
        .models()
        .filter((m) => rotateToolOf(m)?.enabled)
        .map((model) => {
          const g = gizmos.find((x) => x.uid === model.uid)
          return {
            model,
            centre: g ? g.centre.clone() : getModelOBB(model).center.clone(),
            quaternion: { ...((model.geometry?.quaternion as Quat) ?? { x: 0, y: 0, z: 0, w: 1 }) },
            location: { ...((model.geometry?.location as Vec3) ?? { x: 0, y: 0, z: 0 }) },
          }
        })

      drag = {
        pointerId: event.pointerId,
        axis,
        plane,
        centre: gizmo.centre.clone(),
        startAngle: angleOnPlane(point, gizmo.centre, axis),
        targets: targetsForDrag,
      }
      if (host.orbit) host.orbit.enabled = false
      try {
        host.domElement.setPointerCapture(event.pointerId)
      } catch {
        /* headless pointers have no capture */
      }
      host.domElement.addEventListener('pointermove', onMove)
      host.domElement.addEventListener('pointerup', onUp)
      host.domElement.addEventListener('pointercancel', onUp)
      for (const ring of gizmos.flatMap((g) => g.rings)) {
        ring.visual.material.opacity = ring.axis === axis ? 1 : 0.6
      }
      host.requestRender()
      return true
    },
    dispose() {
      if (drag) onUp()
      host.domElement.removeEventListener('pointermove', onMove)
      host.domElement.removeEventListener('pointerup', onUp)
      host.domElement.removeEventListener('pointercancel', onUp)
      dropLabel()
      for (const gizmo of gizmos) {
        for (const ring of gizmo.rings) {
          unregisterHelper(ring.handle)
          for (const mesh of [ring.visual, ring.hit, ring.handle] as THREE.Mesh[]) {
            mesh.parent?.remove(mesh)
            mesh.geometry.dispose()
            ;(mesh.material as THREE.Material).dispose()
          }
        }
      }
      gizmos.length = 0
    },
  }
}
