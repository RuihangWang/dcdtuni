/**
 * Sensor probes in the 3D viewport: a coloured ball per probe, draggable.
 *
 * Ported from `_updateSensorSpheres` (STL_generate.html:23432), its solver-tab
 * gate (:19478) and the drag handlers `beginSensorDrag` / `updateSensorDrag` /
 * `endSensorDrag` (:23479-23575).
 *
 * Until this existed the sensor panel told the user to "drag the coloured
 * spheres in the 3D view" — `sensor.drag_hint`, which the panel renders — while
 * nothing drew any, and a probe could only be placed by typing three numbers.
 *
 * The parts that carry the behaviour:
 *
 * - **Depth testing is OFF.** A probe usually sits inside the room, and a ball
 *   hidden behind a wall cannot be clicked, let alone dragged.
 * - **The drag plane faces the camera and is anchored at the ball's centre**,
 *   and the ball snaps to the ray-plane hit with no grab offset. That is the
 *   locationInMesh feel, deliberately: the two handles look the same, so they
 *   must move the same way.
 * - **One undo entry per drag.** The first movement takes the checkpoint; the
 *   rest mutate in place, and the release logs once. Sixty `edit()` calls a
 *   second would each clone the project.
 * - **The coordinate readout is written straight to the DOM during the drag.**
 *   Re-rendering React per frame would hand the viewport a new project object
 *   and rebuild the whole scene mid-drag. The legacy writes the same node, by
 *   the same `data-sensor-coord-id` attribute.
 * - **The size is camera-adaptive through the shared helper registry (WP-W4).**
 *   A ball is a UNIT sphere registered with its scene-scale data radius; the
 *   Viewport render loop's `syncHelperScales` draws it at
 *   `helperDisplayRadius(dataRadius, dist)` — the data radius at normal and far
 *   distances, shrinking linearly as the camera closes in.
 *
 * The shared `translateGizmo` is attached to whichever ball was last grabbed,
 * so a probe can also be nudged along one axis after it has been thrown roughly
 * into place — the ball drag is fast and imprecise, the arrows are the opposite.
 */

import * as THREE from 'three'
import type { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import type { CfdProject } from '../schema/cfdproj'
import * as store from '../projectStore'
import { registerHelper, unregisterHelper } from '../geometry/helperRegistry'
import { createTranslateGizmo, type TranslateGizmo } from '../interaction/translateGizmo'
import { unitOf, type LengthUnit } from '../lengthUnits'
import { formatSensorLocation } from './sensorEdit'
import { SENSOR_PALETTE, paletteColor } from './sensorPalette'

/**
 * `window._smTempColors` — the same palette the monitor charts and the sensor
 * rows use. One definition, in `sensorPalette.ts`: three consumers indexing two
 * copies of a list is how the ball and the curve stop being the same colour
 * with nothing failing.
 */
export const SENSOR_COLORS = SENSOR_PALETTE
export const sensorColor = paletteColor

export interface SensorBBox {
  xMin: number
  xMax: number
  yMin: number
  yMax: number
  zMin: number
  zMax: number
}

/**
 * The room's box, from the footprint polygon and the enabled bound overrides.
 *
 * Unset axes stay infinite, exactly as `_sensorGetRoomBBox` leaves them
 * (STL_generate.html:23162): `geometry.bounds` entries only count when their
 * own `enabled` flag is on, so a disabled bound must not silently clamp a probe.
 */
export function sensorRoomBBox(project: CfdProject | null): SensorBBox {
  const b: SensorBBox = {
    xMin: -Infinity, xMax: Infinity,
    yMin: -Infinity, yMax: Infinity,
    zMin: -Infinity, zMax: Infinity,
  }
  const geometry = project?.geometry as
    | {
        plane?: [number, number][]
        planeHeight?: number
        height?: number
        bounds?: Record<string, { enabled?: boolean; value?: number }>
      }
    | undefined
  const vertices = geometry?.plane
  if (Array.isArray(vertices) && vertices.length) {
    // Y-up: the footprint is [x, z] in plan coordinates and the height is Y.
    const xs = vertices.map((p) => p[0])
    const zs = vertices.map((p) => p[1])
    b.xMin = Math.min(...xs)
    b.xMax = Math.max(...xs)
    b.zMin = Math.min(...zs)
    b.zMax = Math.max(...zs)
    b.yMin = 0
    const height = geometry?.planeHeight != null ? geometry.planeHeight : geometry?.height
    if (typeof height === 'number' && height > 0) b.yMax = height
  }
  const bounds = geometry?.bounds ?? {}
  for (const key of ['xMin', 'xMax', 'yMin', 'yMax', 'zMin', 'zMax'] as const) {
    const entry = bounds[key]
    if (entry && entry.enabled && typeof entry.value === 'number') b[key] = entry.value
  }
  return b
}

/**
 * Ball DATA radius: 1.2% of the room diagonal, never under 4 cm.
 *
 * A fixed radius is either invisible in a 40 m hall or a beach ball in a
 * testbed. The 4/4/3 fallback covers a project with no footprint yet, where the
 * extents are infinite and the diagonal would be NaN.
 *
 * This is the radius the ball shows at in normal and far views; up close the
 * shared registry shrinks it from here (`helperDisplayRadius`), and a room
 * resize re-registers the same mesh with a new value instead of rebuilding it.
 */
export function sensorSphereRadius(b: SensorBBox): number {
  const span = (min: number, max: number, fallback: number) => {
    const d = max - min
    return Number.isFinite(d) ? d : fallback
  }
  const dx = span(b.xMin, b.xMax, 4)
  const dy = span(b.yMin, b.yMax, 4)
  const dz = span(b.zMin, b.zMax, 3)
  return Math.max(0.04, 0.012 * Math.sqrt(dx * dx + dy * dy + dz * dz))
}

/**
 * Where a dragged probe lands: the raw hit, rounded to a centimetre.
 *
 * No room clamp, on purpose — a diagnostic probe just outside an opening is a
 * legitimate placement, and the legacy dropped its clamp for exactly that.
 */
export function roundProbePoint(p: { x: number; y: number; z: number }): {
  x: number
  y: number
  z: number
} {
  const r = (v: number) => Math.round(v * 100) / 100
  return { x: r(p.x), y: r(p.y), z: r(p.z) }
}

/**
 * The coordinate text the panel shows, so a drag can keep it in step.
 *
 * `formatSensorLocation` is the single source — the same helper the row itself
 * renders through, and the legacy's `_formatSensorCoordText`
 * (STL_generate.html:23206), which exists there for this same reason. It was a
 * unit-free `toFixed(2)` on metres here, so on a millimetre project a drag
 * rewrote the row into a different unit from the one it had been showing.
 */
export function formatProbeCoord(
  p: { x: number; y: number; z: number },
  unit: LengthUnit = 'm',
): string {
  return formatSensorLocation(p, unit)
}

export interface SensorSphereHost {
  camera: THREE.Camera
  scene: THREE.Scene
  domElement: HTMLElement
  /** Identity-transform parent, so a probe's location IS the ball's position. */
  globalGroup: THREE.Group
  orbit: OrbitControls | null
  project(): CfdProject | null
  /** The open left tab — the balls exist only on `solver`. */
  activeTab(): string
  requestRender(): void
}

export interface SensorSpheres {
  refresh(): void
  dragging(): boolean
  /** The probe the arrows are on, for browser verification. */
  activeProbe(): string | null
  /** id -> world position of the ball, for browser verification. */
  positions(): Record<string, [number, number, number]>
  dispose(): void
}

interface Ball {
  id: string
  mesh: THREE.Mesh<THREE.SphereGeometry, THREE.MeshBasicMaterial>
  /** The DATA radius last registered — a room resize re-registers, no rebuild. */
  radius: number
}

const SOLVER_TAB = 'solver'

export function createSensorSpheres(host: SensorSphereHost): SensorSpheres {
  const group = new THREE.Group()
  group.name = 'sensorSpheres'
  host.globalGroup.add(group)

  let balls: Ball[] = []
  let gizmo: TranslateGizmo | null = null
  let active: string | null = null
  let drag: {
    pointerId: number
    id: string
    plane: THREE.Plane
    pushed: boolean
  } | null = null
  /** True while the arrows are being dragged, so the two paths never overlap. */
  let gizmoDrag: { id: string; pushed: boolean } | null = null
  /**
   * Whether the click that follows this pointer sequence belongs to a ball.
   *
   * The viewport's own picking listens on the SAME canvas element and was
   * registered first, so `stopPropagation` from here cannot reach it — a press
   * on a probe would grab the ball AND select whatever model is behind it. A
   * capture-phase listener on `document` runs before any listener on the
   * canvas, which is where that click can still be swallowed.
   */
  let swallowClick = false

  const onSolverTab = () => host.activeTab() === SOLVER_TAB

  const probes = () => store.sensorProbes(host.project())

  function dropBall(ball: Ball) {
    // This is the dispose path for a registered helper, so the registry entry
    // goes with the mesh — a stale entry would be re-scaled forever.
    unregisterHelper(ball.mesh)
    group.remove(ball.mesh)
    ball.mesh.geometry.dispose()
    ball.mesh.material.dispose()
  }

  /**
   * Keep the panel's coordinate span in step without a React render.
   *
   * A re-render per frame would replace the project object the viewport keys
   * its rebuild effect on, so the whole scene would be rebuilt — and re-framed
   * — under a drag in progress.
   */
  function writeCoordText(id: string, p: { x: number; y: number; z: number }) {
    if (typeof document === 'undefined') return
    const escaped = typeof CSS !== 'undefined' && CSS.escape ? CSS.escape(id) : id
    const el = document.querySelector(`[data-sensor-coord-id="${escaped}"]`)
    if (el) el.textContent = formatProbeCoord(p, unitOf(host.project()))
  }

  function place(id: string, point: { x: number; y: number; z: number }) {
    store.mutateLive((project) => {
      const probe = (
        project as unknown as {
          instrumentation?: { probes?: Record<string, { geometry?: { location?: { x: number; y: number; z: number } } }> }
        }
      ).instrumentation?.probes?.[id]
      const loc = probe?.geometry?.location
      if (!loc) return
      loc.x = point.x
      loc.y = point.y
      loc.z = point.z
    }, { scene: false })
    const ball = balls.find((b) => b.id === id)
    ball?.mesh.position.set(point.x, point.y, point.z)
    writeCoordText(id, point)
    host.requestRender()
  }

  function attachGizmo(id: string | null) {
    active = id
    const ball = id ? balls.find((b) => b.id === id) : null
    if (!ball) {
      gizmo?.dispose()
      gizmo = null
      return
    }
    if (!gizmo) {
      gizmo = createTranslateGizmo(host.scene, host.camera, host.domElement, ball.mesh, {
        size: 0.7,
        orbit: host.orbit,
        onDragStart() {
          if (!active) return
          gizmoDrag = { id: active, pushed: false }
        },
        onChange() {
          const state = gizmoDrag
          if (!state) return
          const target = balls.find((b) => b.id === state.id)
          if (!target) return
          if (!state.pushed) {
            store.pushUndoCheckpoint('dragSensorPoint')
            state.pushed = true
          }
          place(state.id, roundProbePoint(target.mesh.position))
        },
        onDragEnd() {
          const state = gizmoDrag
          gizmoDrag = null
          if (!state?.pushed) return
          store.notifyLiveEdit('dragSensorPoint', state.id)
        },
      })
    } else {
      gizmo.attach(ball.mesh)
    }
    gizmo.setVisible(onSolverTab())
  }

  function refresh() {
    const visible = onSolverTab()
    group.visible = visible
    const radius = sensorSphereRadius(sensorRoomBBox(host.project()))
    const list = probes()
    const keep = new Set<string>()

    list.forEach((probe, index) => {
      // Disabled probes are skipped but still consume their colour, so
      // unticking one does not recolour every probe after it.
      const colour = sensorColor(index)
      if (probe.meta?.enabled === false) return
      keep.add(probe.uid)
      let ball = balls.find((b) => b.id === probe.uid)
      if (!ball) {
        // UNIT sphere + shared registry (WP-W4), not a radius baked into the
        // geometry: the render loop draws it at helperDisplayRadius(radius,
        // dist) — the scene-scale radius at normal and far distances, shrinking
        // linearly as the camera closes in, like every other world-fixed
        // helper. The raycaster reads the same world matrix, so the picking
        // shape always matches what is drawn. The baseline scale keeps the data
        // size before the first sync frame — and permanently when the registry
        // is absent (unit tests), where registerHelper is a no-op.
        const mesh = new THREE.Mesh(
          new THREE.SphereGeometry(1, 20, 14),
          new THREE.MeshBasicMaterial({
            color: new THREE.Color(colour),
            transparent: true,
            opacity: 0.85,
            depthTest: false,
            depthWrite: false,
          }),
        )
        mesh.scale.setScalar(radius)
        mesh.renderOrder = 9999
        mesh.userData = { isSensorSphere: true, sensorId: probe.uid }
        registerHelper(mesh, radius)
        group.add(mesh)
        ball = { id: probe.uid, mesh, radius }
        balls.push(ball)
      } else if (Math.abs(ball.radius - radius) > 1e-9) {
        // The room was resized, so the DATA radius changed. Re-registering
        // updates the existing entry — no rebuild, no new geometry, and the
        // ball keeps its position, colour and gizmo attachment.
        ball.radius = radius
        ball.mesh.scale.setScalar(radius)
        registerHelper(ball.mesh, radius)
      }
      ball.mesh.material.color.set(colour)
      const loc = probe.geometry?.location
      ball.mesh.position.set(Number(loc?.x) || 0, Number(loc?.y) || 0, Number(loc?.z) || 0)
    })

    for (const ball of balls.filter((b) => !keep.has(b.id))) dropBall(ball)
    balls = balls.filter((b) => keep.has(b.id))

    if (active && !keep.has(active)) attachGizmo(null)
    else if (active) attachGizmo(active)
    host.requestRender()
  }

  // --- direct ball drag ----------------------------------------------------
  const ndc = new THREE.Vector2()
  const ray = new THREE.Raycaster()

  function castFrom(e: PointerEvent) {
    const rect = host.domElement.getBoundingClientRect()
    ndc.set(
      ((e.clientX - rect.left) / rect.width) * 2 - 1,
      -((e.clientY - rect.top) / rect.height) * 2 + 1,
    )
    ray.setFromCamera(ndc, host.camera)
    return ray
  }

  function onClickCapture(e: MouseEvent) {
    if (!swallowClick) return
    swallowClick = false
    e.stopPropagation()
  }

  function onPointerDown(e: PointerEvent) {
    // Cleared on EVERY press, so a sequence that never produced a click cannot
    // leave the flag armed and swallow an unrelated one later.
    swallowClick = false
    if (e.button !== 0 || !onSolverTab() || !group.visible || !balls.length) return
    // The arrows get first refusal: they are drawn over the ball they belong
    // to, so a press with an axis under the cursor means the axis, and taking
    // the ball drag as well would move the probe twice per frame.
    if (gizmo && (gizmo.controls.dragging || gizmo.controls.axis)) return
    const rc = castFrom(e)
    const hit = rc.intersectObjects(
      balls.map((b) => b.mesh),
      false,
    )[0]
    if (!hit) return
    const id = (hit.object.userData as { sensorId?: string }).sensorId
    if (!id) return

    const camDir = new THREE.Vector3()
    host.camera.getWorldDirection(camDir)
    drag = {
      pointerId: e.pointerId,
      id,
      plane: new THREE.Plane().setFromNormalAndCoplanarPoint(
        camDir.clone().negate(),
        hit.object.position.clone(),
      ),
      pushed: false,
    }
    attachGizmo(id)
    swallowClick = true
    if (host.orbit) host.orbit.enabled = false
    try {
      ;(host.domElement as HTMLElement & { setPointerCapture(id: number): void }).setPointerCapture(
        e.pointerId,
      )
    } catch {
      /* jsdom and some browsers refuse capture on a non-focused element */
    }
    e.stopPropagation()
  }

  function onPointerMove(e: PointerEvent) {
    if (!drag || e.pointerId !== drag.pointerId) return
    const rc = castFrom(e)
    const target = new THREE.Vector3()
    if (!rc.ray.intersectPlane(drag.plane, target)) return
    if (!drag.pushed) {
      store.pushUndoCheckpoint('dragSensorPoint')
      drag.pushed = true
    }
    place(drag.id, roundProbePoint(target))
  }

  function onPointerUp(e: PointerEvent) {
    if (!drag || (e.pointerId != null && e.pointerId !== drag.pointerId)) return
    try {
      ;(host.domElement as HTMLElement & { releasePointerCapture(id: number): void }).releasePointerCapture(
        drag.pointerId,
      )
    } catch {
      /* capture may never have been granted */
    }
    if (host.orbit) host.orbit.enabled = true
    const finished = drag
    drag = null
    if (finished.pushed) store.notifyLiveEdit('dragSensorPoint', finished.id)
  }

  document.addEventListener('click', onClickCapture, true)
  host.domElement.addEventListener('pointerdown', onPointerDown)
  host.domElement.addEventListener('pointermove', onPointerMove)
  host.domElement.addEventListener('pointerup', onPointerUp)
  host.domElement.addEventListener('pointercancel', onPointerUp)

  return {
    refresh,
    dragging: () => !!drag || !!gizmoDrag,
    activeProbe: () => active,
    positions() {
      const out: Record<string, [number, number, number]> = {}
      for (const b of balls) out[b.id] = [b.mesh.position.x, b.mesh.position.y, b.mesh.position.z]
      return out
    },
    dispose() {
      document.removeEventListener('click', onClickCapture, true)
      host.domElement.removeEventListener('pointerdown', onPointerDown)
      host.domElement.removeEventListener('pointermove', onPointerMove)
      host.domElement.removeEventListener('pointerup', onPointerUp)
      host.domElement.removeEventListener('pointercancel', onPointerUp)
      gizmo?.dispose()
      gizmo = null
      for (const ball of balls) dropBall(ball)
      balls = []
      host.globalGroup.remove(group)
    },
  }
}
