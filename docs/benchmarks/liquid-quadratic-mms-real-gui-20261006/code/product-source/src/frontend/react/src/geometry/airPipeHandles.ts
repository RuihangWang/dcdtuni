/**
 * The air-pipe control points and their gizmo — the port of the liquid pipe's
 * waypoint handling (`js/liquid_mode.js:320-615`) for the air-side `pipe`.
 *
 * Selecting a pipe shows a ball on every waypoint: the two END balls (blue at
 * the start, red at the end) and yellow BODY balls in between. Pressing a
 * ball attaches the shared translate gizmo to it:
 *
 *  - dragging an END ball moves that end. A pull that is mostly sideways to
 *    the pipe's axis drops a 90° corner where the drag began, as the liquid
 *    editor does, so a straight run is bent by grabbing its end;
 *  - dragging a BODY ball moves the WHOLE pipe — the body gizmo is the pipe's
 *    move handle, again as in the liquid editor;
 *  - releasing an end within `PIPE_SNAP_DISTANCE_M` of another pipe's free end
 *    snaps it there and joins the two (a ring previews the target while the
 *    drag is in range). The renderer then draws a reducer where the bores
 *    differ. Pulling a joined end away breaks the join.
 *
 * A drag mutates the live project without undo entries and commits ONE
 * `store.edit` on release, the same contract as the face-drag handles.
 */

import * as THREE from 'three'
import type { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import type { CfdProject, Construction, Vec3 } from '../schema/cfdproj'
import { registerHelper, unregisterHelper } from './helperRegistry'
import type { SceneRegistries } from '../interaction/sceneBuilder'
import { createTranslateGizmo, type TranslateGizmo } from '../interaction/translateGizmo'
import * as store from '../projectStore'
import {
  airPipeGeometry,
  airPipeRadius,
  airPipeWorldPoints,
  endOfIndex,
  findAirPipeJoinTarget,
  isAirPipe,
  joinAirPipes,
  joinAt,
  joinBroken,
  translateAirPipe,
  unjoinAirPipeEnd,
  writeAirPipePointWorld,
  type FreePipeEnd,
  type PipeEnd,
} from './airPipe'

export interface AirPipeHandlesHost {
  scene: THREE.Scene
  camera: THREE.Camera
  domElement: HTMLElement
  orbit: OrbitControls | null
  registries: () => SceneRegistries
  project: () => CfdProject | null
  /** The selected model's uid. */
  selection: () => string | null
  /**
   * How many models are selected.
   *
   * The balls are drawn for ONE selected pipe only — the legacy editor's
   * `onSelectionChanged` rule. With several models selected the group move
   * tool is what moves them, and a ball left over the active pipe's end would
   * sit exactly where the next ctrl/shift-click on that pipe lands.
   */
  selectionCount: () => number
  rebuildSoon: () => void
  rebuildNow: (uids?: string[]) => void
  enabled: () => boolean
}

export interface AirPipeHandles {
  refresh(): void
  /** True when the press landed on a ball or on the gizmo: not a click. */
  begin(event: PointerEvent): boolean
  hover(event: PointerEvent): void
  dragging(): boolean
  /** Attach the gizmo to waypoint `index` of the selected pipe (for scripts). */
  grab(index: number): boolean
  dispose(): void
}

export const START_COLOR = 0x2196f3
export const END_COLOR = 0xf44336
export const BODY_COLOR = 0xffeb3b
const CORNER_MIN_DRAG_M = 0.03
const CORNER_MIN_PERP_M = 0.02

interface BallData {
  isAirPipeHandle: true
  uid: string
  index: number
  end: PipeEnd | null
}

interface DragState {
  uid: string
  /** The ball being dragged; its `index` is updated when a corner is inserted. */
  ball: THREE.Mesh
  startWorld: Vec3
  lastWorld: Vec3
  /** The geometry to put back before the commit (deep copy). */
  before: { location: Vec3; waypoints: Vec3[] }
  cornerInserted: boolean
  target: FreePipeEnd | null
}

export function createAirPipeHandles(host: AirPipeHandlesHost): AirPipeHandles {
  const group = new THREE.Group()
  group.name = 'airPipeHandles'
  host.scene.add(group)
  const balls: THREE.Mesh[] = []
  const raycaster = new THREE.Raycaster()
  const pointer = new THREE.Vector2()
  let gizmo: TranslateGizmo | null = null
  let drag: DragState | null = null
  let hovered: THREE.Mesh | null = null
  let ring: THREE.Mesh | null = null
  let lastDragEnd = 0

  const selectedPipe = (): Construction | null => {
    const uid = host.selection()
    const project = host.project()
    if (!uid || !project || host.selectionCount() > 1) return null
    const model = store.findConstruction(project, uid)?.model ?? null
    return isAirPipe(model) ? model : null
  }

  function setRay(event: { clientX: number; clientY: number }) {
    const rect = host.domElement.getBoundingClientRect()
    pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1
    pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1
    raycaster.setFromCamera(pointer, host.camera)
  }

  function clearBalls() {
    for (const b of balls) {
      unregisterHelper(b)
      group.remove(b)
      b.geometry.dispose()
      ;(b.material as THREE.Material).dispose()
    }
    balls.length = 0
    hovered = null
  }

  function hideRing() {
    if (!ring) return
    unregisterHelper(ring)
    host.scene.remove(ring)
    ring.geometry.dispose()
    ;(ring.material as THREE.Material).dispose()
    ring = null
  }

  function showRing(at: Vec3, radius: number) {
    const s = Math.max(0.05, radius * 1.2)
    if (!ring) {
      ring = new THREE.Mesh(
        new THREE.RingGeometry(1, 1.35, 32),
        new THREE.MeshBasicMaterial({
          color: 0x00e5ff,
          transparent: true,
          opacity: 0.8,
          side: THREE.DoubleSide,
          depthTest: false,
        }),
      )
      // Initial size, so the ring is never a unit-scale flash before the
      // first synced frame (and stays correct while the registry is absent).
      ring.scale.setScalar(s)
      ring.renderOrder = 999
      host.scene.add(ring)
    }
    // WP-S3: the ring geometry is already unit (1 → 1.35), so only the
    // registration is new: the legacy world-space scale becomes the data
    // radius and the shared per-frame sync owns the scale. Writing it here
    // too — showRing runs on every pointermove of a snap drag — would fight
    // that write, so the self-write stays only as the registry-absent
    // fallback. A re-register carries a changed target radius to the sync.
    if (!registerHelper(ring, s)) ring.scale.set(s, s, s)
    ring.position.set(at.x, at.y, at.z)
    ring.lookAt(host.camera.position)
  }

  function ensureGizmo(): TranslateGizmo {
    if (gizmo) return gizmo
    gizmo = createTranslateGizmo(host.scene, host.camera, host.domElement, null, {
      space: 'world',
      size: 0.7,
      orbit: host.orbit,
      onDragStart: (target) => onDragStart(target as THREE.Mesh | null),
      onChange: (target) => onDragMove(target as THREE.Mesh | null),
      onDragEnd: () => onDragEnd(),
    })
    return gizmo
  }

  function attachTo(ball: THREE.Mesh) {
    ball.quaternion.identity()
    ensureGizmo().attach(ball)
  }

  function detach() {
    gizmo?.detach()
  }

  function ballAt(index: number): THREE.Mesh | null {
    return balls.find((b) => (b.userData as BallData).index === index) ?? null
  }

  function build(model: Construction) {
    clearBalls()
    const pts = airPipeWorldPoints(model)
    const r = airPipeRadius(model)
    const n = pts.length
    pts.forEach((p, i) => {
      const end = endOfIndex(model, i)
      const isEnd = end !== null && n > 1
      const radius = isEnd ? Math.max(0.03, r * 1.2) : Math.max(0.02, r * 0.8)
      const color = end === 'start' ? START_COLOR : end === 'end' ? END_COLOR : BODY_COLOR
      // WP-S3: UNIT sphere scaled to the bore-derived radius and registered
      // with the shared per-frame helper scaling (scale write = initial
      // condition / registry-absent fallback). Raycasts read the scale, so
      // the balls stay exactly as grabbable as they look.
      const ball = new THREE.Mesh(
        new THREE.SphereGeometry(1, 16, 12),
        new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.6 }),
      )
      ball.scale.setScalar(radius)
      registerHelper(ball, radius)
      ball.position.set(p.x, p.y, p.z)
      const data: BallData = { isAirPipeHandle: true, uid: model.uid, index: i, end }
      ball.userData = data
      group.add(ball)
      balls.push(ball)
    })
  }

  function refresh() {
    if (drag) return
    const model = selectedPipe()
    const keep =
      gizmo?.controls.object && model
        ? (gizmo.controls.object.userData as BallData).index
        : null
    detach()
    hideRing()
    if (!model || !host.enabled()) {
      clearBalls()
      return
    }
    build(model)
    if (keep !== null) {
      const ball = ballAt(keep)
      if (ball) attachTo(ball)
    }
  }

  // ── the drag ──────────────────────────────────────────────────────────────

  function liveModel(uid: string): Construction | null {
    const project = host.project()
    const model = project ? (store.findConstruction(project, uid)?.model ?? null) : null
    return isAirPipe(model) ? model : null
  }

  function onDragStart(ball: THREE.Mesh | null) {
    if (!ball) return
    const data = ball.userData as BallData
    const model = liveModel(data.uid)
    if (!model) return
    const g = airPipeGeometry(model)
    const p = ball.position
    drag = {
      uid: data.uid,
      ball,
      startWorld: { x: p.x, y: p.y, z: p.z },
      lastWorld: { x: p.x, y: p.y, z: p.z },
      before: {
        location: { ...g.location },
        waypoints: g.waypoints.map((w) => ({ ...w })),
      },
      cornerInserted: false,
      target: null,
    }
  }

  function moveOtherBalls(model: Construction, except: THREE.Mesh) {
    const pts = airPipeWorldPoints(model)
    for (const b of balls) {
      if (b === except) continue
      const p = pts[(b.userData as BallData).index]
      if (p) b.position.set(p.x, p.y, p.z)
    }
  }

  function onDragMove(ball: THREE.Mesh | null) {
    if (!drag || !ball || ball !== drag.ball) return
    const model = liveModel(drag.uid)
    if (!model) return
    const data = ball.userData as BallData
    const now = { x: ball.position.x, y: ball.position.y, z: ball.position.z }

    store.mutateLive(() => {
      if (data.end === null) {
        // Body ball: the whole pipe follows.
        translateAirPipe(model, {
          x: now.x - drag!.lastWorld.x,
          y: now.y - drag!.lastWorld.y,
          z: now.z - drag!.lastWorld.z,
        })
      } else {
        // End ball: bend the run at the drag's origin when the pull is mostly
        // sideways — the liquid editor's corner rule (js/liquid_mode.js:419).
        const g = airPipeGeometry(model)
        if (!drag!.cornerInserted && g.waypoints.length >= 2) {
          const disp = {
            x: now.x - drag!.startWorld.x,
            y: now.y - drag!.startWorld.y,
            z: now.z - drag!.startWorld.z,
          }
          const total = Math.hypot(disp.x, disp.y, disp.z)
          if (total > CORNER_MIN_DRAG_M) {
            const pts = airPipeWorldPoints(model)
            const isStart = data.end === 'start'
            const neighbour = isStart ? pts[1]! : pts[pts.length - 2]!
            const axial = {
              x: drag!.startWorld.x - neighbour.x,
              y: drag!.startWorld.y - neighbour.y,
              z: drag!.startWorld.z - neighbour.z,
            }
            const al = Math.hypot(axial.x, axial.y, axial.z) || 1
            const along = Math.abs((disp.x * axial.x + disp.y * axial.y + disp.z * axial.z) / al)
            const perp = Math.sqrt(Math.max(0, total * total - along * along))
            if (perp > along && perp > CORNER_MIN_PERP_M) {
              const wps = (model.geometry as { waypoints: Vec3[] }).waypoints
              const cornerLocal = wps[isStart ? 0 : wps.length - 1]!
              if (isStart) wps.splice(1, 0, { ...cornerLocal })
              else {
                wps.splice(wps.length - 1, 0, { ...cornerLocal })
                data.index = wps.length - 1
              }
              drag!.cornerInserted = true
            }
          }
        }
        writeAirPipePointWorld(model, data.index, now)
      }
    })
    drag.lastWorld = now
    moveOtherBalls(model, ball)

    // Snap preview, ends only.
    if (data.end !== null) {
      const project = host.project()
      drag.target = project ? findAirPipeJoinTarget(project, drag.uid, now) : null
      if (drag.target) showRing(drag.target.world, drag.target.radius)
      else hideRing()
    }
    host.rebuildSoon()
  }

  function onDragEnd() {
    lastDragEnd = Date.now()
    const state = drag
    drag = null
    hideRing()
    if (!state) return
    const model = liveModel(state.uid)
    if (!model) return
    const data = state.ball.userData as BallData
    const g = airPipeGeometry(model)
    const after = {
      location: { ...g.location },
      waypoints: g.waypoints.map((w) => ({ ...w })),
    }
    const target = data.end !== null ? state.target : null
    const end = data.end
    const keepIndex = data.index

    // Put the start state back on the live object so the commit below is the
    // one undoable step from "before the drag" to "after it".
    store.mutateLive(() => {
      const geometry = model.geometry as Record<string, unknown>
      geometry.location = state.before.location
      geometry.waypoints = state.before.waypoints
    })
    store.edit('dragAirPipePoint', `${state.uid} #${keepIndex}`, (draft) => {
      const m = store.findConstruction(draft, state.uid)?.model
      if (!m) return
      const geometry = (m.geometry ?? (m.geometry = {})) as Record<string, unknown>
      geometry.location = after.location
      geometry.waypoints = after.waypoints
      if (end) {
        if (target) joinAirPipes(draft, state.uid, end, target.uid, target.end)
        else if (joinAt(m, end) && joinBroken(draft, m, end)) unjoinAirPipeEnd(draft, state.uid, end)
      } else {
        // The whole pipe moved: any join that no longer meets is broken.
        for (const e of ['start', 'end'] as const) {
          if (joinAt(m, e) && joinBroken(draft, m, e)) unjoinAirPipeEnd(draft, state.uid, e)
        }
      }
    })
    host.rebuildNow()
    const fresh = selectedPipe()
    detach()
    if (fresh) {
      build(fresh)
      const ball = ballAt(keepIndex)
      if (ball) attachTo(ball)
    } else clearBalls()
  }

  // ── pointer ───────────────────────────────────────────────────────────────

  function begin(event: PointerEvent): boolean {
    if (!host.enabled() || event.button !== 0) return false
    // The gizmo's own press: TransformControls has already picked an axis on
    // hover, and the drag it starts must not also be a selection click.
    if (gizmo?.controls.object && gizmo.controls.axis) return true
    // A modifier click is a SELECTION gesture — add this pipe, or take it out —
    // never a grab. The end balls are wider than the tube and cover the pipe's
    // ends, so without this a ctrl- or shift-click on a pipe end attached the
    // gizmo and the selection never changed (measured on DAML's Duct_risers).
    // Before the post-drag guard, so a modifier click straight after a drag
    // still reaches the selection.
    if (event.shiftKey || event.ctrlKey || event.metaKey) return false
    if (Date.now() - lastDragEnd < 200) return true
    if (!balls.length) return false
    setRay(event)
    const hits = raycaster.intersectObjects(balls, false)
    if (!hits.length) return false
    // An end ball inside a body ball: the end wins, as in the liquid editor.
    let chosen = hits[0]!.object as THREE.Mesh
    for (let i = 1; i < hits.length; i++) {
      const cand = hits[i]!.object as THREE.Mesh
      if ((cand.userData as BallData).end && !(chosen.userData as BallData).end && hits[i]!.distance - hits[0]!.distance < 0.3) {
        chosen = cand
        break
      }
    }
    attachTo(chosen)
    return true
  }

  function hover(event: PointerEvent) {
    if (drag || !balls.length) return
    setRay(event)
    const hit = raycaster.intersectObjects(balls, false)[0]?.object as THREE.Mesh | undefined
    if (hit === hovered) return
    if (hovered) (hovered.material as THREE.MeshBasicMaterial).opacity = 0.6
    hovered = hit ?? null
    if (hovered) (hovered.material as THREE.MeshBasicMaterial).opacity = 1
    host.domElement.style.cursor = hovered ? 'grab' : ''
  }

  function grab(index: number): boolean {
    const ball = ballAt(index)
    if (!ball) return false
    attachTo(ball)
    return true
  }

  return {
    refresh,
    begin,
    hover,
    dragging: () => drag !== null,
    grab,
    dispose() {
      hideRing()
      clearBalls()
      gizmo?.dispose()
      gizmo = null
      host.scene.remove(group)
    },
  }
}
