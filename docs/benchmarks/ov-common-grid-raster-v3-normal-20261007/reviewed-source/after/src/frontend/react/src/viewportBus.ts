import * as THREE from 'three'
import type { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import type {LineRasterObservation} from './renderer/displayRaster'

/**
 * A handle on the live viewport camera, for the widgets that live outside it.
 *
 * The view cube sits in the toolbar and the axis triad sits over the canvas —
 * two separate React subtrees that both need the camera the Viewport owns.
 * Passing a ref down through the whole app would couple every panel in between
 * to a Three.js object; a module-level bus keeps that coupling to the three
 * files that actually need it. Same shape as the Omniverse demo's roiStore.
 *
 * The camera is deliberately NOT React state: it changes every frame, and
 * re-rendering the app at 60fps to track it is exactly the mistake this port is
 * trying not to repeat. Subscribers read it inside their own animation loop.
 */

export interface ViewportHandle {
  camera: THREE.PerspectiveCamera
  controls: OrbitControls
  /** Observation from the retained owned renderer; absent means unobserved. */
  readLineRaster?: () => LineRasterObservation | null
}

let transition: { frame: number; controls: OrbitControls; onManualStart: () => void } | null = null

/** Stop a preset flight as soon as the user starts orbiting or another view wins. */
export function cancelViewTransition(): void {
  if (!transition) return
  cancelAnimationFrame(transition.frame)
  transition.controls.removeEventListener('start', transition.onManualStart)
  transition = null
}

let handle: ViewportHandle | null = null
const listeners = new Set<(h: ViewportHandle | null) => void>()

// ── camera-settled notifications (remote-renderer camera linkage) ──────────
// Discrete camera moves are the only moments worth forwarding to the OV
// re-render: the view cube / toolbar presets, a finished preset flight, and
// the end of any orbit gesture. Continuous orbit stays Three-local (and in
// OV mode the Three viewport is covered, so only these entries can move it).
const cameraSettledListeners = new Set<() => void>()

export function subscribeCameraSettled(fn: () => void): () => void {
  cameraSettledListeners.add(fn)
  return () => {
    cameraSettledListeners.delete(fn)
  }
}

export function notifyCameraSettled(): void {
  cameraSettledListeners.forEach(fn => fn())
}

// Live drag linkage: orbit/cube-drag frames fire controls 'change' at pointer
// rate. Forwarding every frame would flood the WebRTC command channel, so
// emits are throttled to one per THROTTLE_MS with a guaranteed trailing edge,
// and 'end' flushes immediately (a settled view must never be lost).
const CAMERA_MOVE_THROTTLE_MS = 120
let moveTimer: ReturnType<typeof setTimeout> | null = null
let lastMoveEmit = -Infinity

function notifyCameraMoved(): void {
  const now = performance.now()
  const elapsed = now - lastMoveEmit
  if (elapsed >= CAMERA_MOVE_THROTTLE_MS) {
    lastMoveEmit = now
    if (moveTimer) { clearTimeout(moveTimer); moveTimer = null }
    notifyCameraSettled()
    return
  }
  if (moveTimer) return
  moveTimer = setTimeout(() => {
    moveTimer = null
    lastMoveEmit = performance.now()
    notifyCameraSettled()
  }, CAMERA_MOVE_THROTTLE_MS - elapsed)
}

let controlsEndUnsubscribe: (() => void) | null = null
let controlsChangeUnsubscribe: (() => void) | null = null

export function setViewportHandle(next: ViewportHandle | null) {
  if (handle !== next) cancelViewTransition()
  controlsEndUnsubscribe?.()
  controlsEndUnsubscribe = null
  controlsChangeUnsubscribe?.()
  controlsChangeUnsubscribe = null
  if (moveTimer) { clearTimeout(moveTimer); moveTimer = null }
  if (next && typeof next.controls.addEventListener === 'function') {
    next.controls.addEventListener('end', notifyCameraSettled)
    next.controls.addEventListener('change', notifyCameraMoved)
    controlsEndUnsubscribe = () => next.controls.removeEventListener('end', notifyCameraSettled)
    controlsChangeUnsubscribe = () => next.controls.removeEventListener('change', notifyCameraMoved)
  }
  handle = next
  // Also published on `window`, mirroring the legacy editor's global `camera`
  // and `controls`. Browser verification has to reach the live camera to work
  // out where a gizmo handle is on screen, and a module-scoped binding is
  // unreachable from a page-level eval.
  ;(window as unknown as { __viewportHandle?: ViewportHandle | null }).__viewportHandle = next
  listeners.forEach((fn) => fn(next))
}

export function getViewportHandle(): ViewportHandle | null {
  return handle
}

export function subscribeViewport(fn: (h: ViewportHandle | null) => void): () => void {
  listeners.add(fn)
  fn(handle)
  return () => {
    listeners.delete(fn)
  }
}

/** Named views, as the legacy view cube and `applyViewPreset` define them. */
export type ViewPreset = 'top' | 'bottom' | 'front' | 'back' | 'left' | 'right' | 'iso'

const PRESET_DIR: Record<ViewPreset, [number, number, number]> = {
  top: [0, 1, 0],
  bottom: [0, -1, 0],
  front: [0, 0, 1],
  back: [0, 0, -1],
  right: [1, 0, 0],
  left: [-1, 0, 0],
  iso: [1, 0.8, 1],
}

/**
 * Point the camera along a named axis, keeping its distance to the target.
 *
 * Distance is preserved rather than reset so a user who has zoomed into a rack
 * stays zoomed in when they switch to the top view — resetting it would throw
 * away the part of the navigation they care about most.
 */
export function applyViewPreset(preset: ViewPreset) {
  cancelViewTransition()
  const h = handle
  if (!h) return
  const { camera, controls } = h
  const target = controls.target
  const distance = camera.position.distanceTo(target)
  const [dx, dy, dz] = PRESET_DIR[preset]
  const len = Math.hypot(dx, dy, dz) || 1
  camera.position.set(
    target.x + (dx / len) * distance,
    target.y + (dy / len) * distance,
    target.z + (dz / len) * distance,
  )
  // A camera looking straight down needs a different up vector, or the view
  // degenerates: the default up is parallel to the view direction there.
  camera.up.set(0, preset === 'top' || preset === 'bottom' ? 0 : 1, preset === 'top' ? -1 : preset === 'bottom' ? 1 : 0)
  camera.lookAt(target)
  controls.update()
  notifyCameraSettled()
}

/** Orbit at a fixed radius from the current view to a clicked cube face. */
export function animateViewPreset(preset: ViewPreset, durationMs = 420): void {
  const h = handle
  if (!h) return
  cancelViewTransition()
  if (durationMs <= 0 || window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) {
    applyViewPreset(preset)
    return
  }
  const { camera, controls } = h
  const target = controls.target.clone()
  const startOffset = camera.position.clone().sub(target)
  const radius = startOffset.length()
  if (radius < 1e-6) {
    applyViewPreset(preset)
    return
  }
  const direction = new THREE.Vector3(...PRESET_DIR[preset]).normalize()
  const from = new THREE.Spherical().setFromVector3(startOffset)
  const to = new THREE.Spherical().setFromVector3(direction)
  const azimuth = Math.atan2(Math.sin(to.theta - from.theta), Math.cos(to.theta - from.theta))
  const startUp = camera.up.clone().normalize()
  const endUp = preset === 'top' ? new THREE.Vector3(0, 0, -1)
    : preset === 'bottom' ? new THREE.Vector3(0, 0, 1) : new THREE.Vector3(0, 1, 0)
  const upRotation = new THREE.Quaternion().setFromUnitVectors(startUp, endUp)
  const upStep = new THREE.Quaternion()
  const offset = new THREE.Vector3()
  const spherical = new THREE.Spherical()
  const started = performance.now()
  const onManualStart = () => cancelViewTransition()
  controls.addEventListener('start', onManualStart)

  const frame = (now: number) => {
    if (handle !== h || controls.target.distanceToSquared(target) > 1e-12) {
      cancelViewTransition()
      return
    }
    const progress = Math.min(1, Math.max(0, (now - started) / durationMs))
    const eased = progress < 0.5 ? 4 * progress ** 3 : 1 - (-2 * progress + 2) ** 3 / 2
    spherical.set(radius, from.phi + (to.phi - from.phi) * eased, from.theta + azimuth * eased)
    offset.setFromSpherical(spherical)
    camera.position.copy(target).add(offset)
    upStep.identity().slerp(upRotation, eased)
    camera.up.copy(startUp).applyQuaternion(upStep)
    camera.lookAt(target)
    controls.update()
    if (progress < 1) {
      if (transition) transition.frame = requestAnimationFrame(frame)
    } else {
      cancelViewTransition()
      notifyCameraSettled()
    }
  }
  transition = { frame: requestAnimationFrame(frame), controls, onManualStart }
}

// ── the built scene, for the parts of the UI that describe it ──────────────
//
// Some tree nodes exist only in the BUILT geometry, not in the project: a wall
// split by patches renders as several base-wall meshes, each carrying its own
// OpenFOAM export name in `userData`. The legacy tree reads them straight off
// `renderMeshes` for exactly this reason — there is nowhere else the names
// exist — so this exposes the same list rather than inventing a second source
// of truth that could disagree with what gets exported.

interface SceneMeshLike {
  userData?: {
    modelId?: string
    faceId?: string
    isBaseWall?: boolean
    isSeedHandle?: boolean
    wallIndex?: number
    exportName?: string
  }
}

let sceneMeshes: readonly SceneMeshLike[] = []
let sceneVersion = 0
const sceneListeners = new Set<(version: number) => void>()

/** Called by the viewport after every rebuild. */
export function setSceneMeshes(meshes: readonly SceneMeshLike[]) {
  sceneMeshes = meshes
  sceneVersion += 1
  sceneListeners.forEach((fn) => fn(sceneVersion))
}

export function subscribeScene(fn: (version: number) => void): () => void {
  sceneListeners.add(fn)
  return () => {
    sceneListeners.delete(fn)
  }
}

/**
 * The export names of one face's wall regions, in wall order.
 *
 * De-duplicated by wall index: a region can be split into several meshes and
 * they all carry the same index and name, which would otherwise list the same
 * region three times.
 *
 * ## Indexed, not scanned
 *
 * The feature tree asks this for EVERY model on EVERY render, and the scan is
 * over the whole built scene. On `case/Alibaba-hongchou` that is 287 models x
 * 15,153 meshes — 4.3 million `userData` reads to answer a question whose answer
 * is empty for all but the 63 walls, baffles and floors. Measured: the tree
 * render spent more time here than in React. One index per scene version, built
 * on the first question after a rebuild, turns each lookup into a `Map.get`.
 */
const NO_REGIONS: readonly string[] = Object.freeze([])

let regionIndex: Map<string, string[]> | null = null
let regionIndexVersion = -1

function regionKey(modelId: string, faceId: string): string {
  return `${modelId}\u0000${faceId}`
}

function regionsByFace(): Map<string, string[]> {
  if (regionIndex && regionIndexVersion === sceneVersion) return regionIndex
  const byIndex = new Map<string, Map<number, string>>()
  for (const mesh of sceneMeshes) {
    const d = mesh.userData
    if (!d || !d.modelId || !d.faceId) continue
    if (!d.isBaseWall || d.isSeedHandle || !Number.isInteger(d.wallIndex)) continue
    const key = regionKey(d.modelId, d.faceId)
    let perFace = byIndex.get(key)
    if (!perFace) {
      perFace = new Map<number, string>()
      byIndex.set(key, perFace)
    }
    const index = d.wallIndex as number
    if (!perFace.has(index)) perFace.set(index, d.exportName ?? `wall_${index + 1}`)
  }
  const out = new Map<string, string[]>()
  for (const [key, perFace] of byIndex) {
    out.set(key, [...perFace.entries()].sort((a, b) => a[0] - b[0]).map(([, name]) => name))
  }
  regionIndex = out
  regionIndexVersion = sceneVersion
  return out
}

export function wallRegionNames(modelId: string, faceId: string): readonly string[] {
  return regionsByFace().get(regionKey(modelId, faceId)) ?? NO_REGIONS
}
