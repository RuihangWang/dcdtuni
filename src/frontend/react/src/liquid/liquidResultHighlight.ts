/**
 * Shows the selected object in the 3D view while liquid results are open.
 *
 * The geometry tab's selection highlight covers models with a render root;
 * liquid pipes, valves and manifold stubs are drawn by the shared liquid
 * renderer and get their highlight from the editing code (with its move
 * buttons), which has no place on a results tab. So the results panel draws
 * its own: translucent copies of the selected object's meshes, sharing their
 * geometry, drawn over everything, and a ring of constant screen size on the
 * object so that a part a few pixels wide can still be found.
 *
 * Materials are never touched — the result colouring owns them — and the
 * copies are tagged `isHelper`, so the Omniverse scene snapshot leaves them
 * out. The overlay follows scene rebuilds and is removed with the panel.
 */

import * as THREE from 'three'
import { subscribeScene } from '../viewportBus'
import type { NetworkSelection } from './liquidNetwork'

const GROUP_NAME = 'liquidPostprocessHighlight'
const MAX_MESHES = 4000

type UserData = Record<string, unknown>

/** Whether a mesh, by its own and its parents' userData (nearest first), belongs to `selection`. */
export function meshBelongsTo(chain: readonly UserData[], selection: NetworkSelection): boolean {
  const node = selection.node
  const has = (pred: (d: UserData) => boolean) => chain.some(pred)
  if (node?.type === 'fitting') return has((d) => d.valveId === node.fittingId && d.pipeId === selection.uid)
  if (node?.type === 'equipment') {
    return has((d) => d.isRackEquipment === true && d.equipmentId === node.equipmentId) &&
      has((d) => d.modelId === selection.uid)
  }
  // A whole pipe includes its valves and T fittings and its blind-end caps.
  return has((d) => d.pipeModelId === selection.uid || d.junctionId === selection.uid ||
    ((d.isValve === true || d.isBlindEndCap === true) && d.pipeId === selection.uid) || d.modelId === selection.uid)
}

const HIGHLIGHT_COLOR = 0xffd400
const material = new THREE.MeshBasicMaterial({
  color: HIGHLIGHT_COLOR, transparent: true, opacity: 0.55, depthTest: false, depthWrite: false,
})

/**
 * A ring of constant on-screen size, set on the selected object.
 *
 * The translucent copies alone do not locate a small part: a 25 mm valve is a
 * few pixels wide when the whole room is in view, so selecting it changed
 * nothing visible (seen in the browser check). A sprite with
 * `sizeAttenuation: false` keeps the same size on screen at any zoom; its
 * texture is computed, not drawn on a canvas, so it needs no 2D context.
 */
const MARKER_SCALE = 0.045 // ≈ 5 % of the viewport height at the editor's 45° field of view
let markerMaterial: THREE.SpriteMaterial | null = null

function ringMaterial(): THREE.SpriteMaterial {
  if (markerMaterial) return markerMaterial
  const size = 64
  const data = new Uint8Array(size * size * 4)
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const r = Math.hypot(x + 0.5 - size / 2, y + 0.5 - size / 2) / (size / 2)
      // A solid band from r = 0.68 to 0.92 with one-texel soft edges.
      const alpha = Math.max(0, Math.min(1, (r - 0.68) * 16, (0.92 - r) * 16))
      const i = (y * size + x) * 4
      data[i] = data[i + 1] = data[i + 2] = 255
      data[i + 3] = Math.round(alpha * 255)
    }
  }
  const texture = new THREE.DataTexture(data, size, size, THREE.RGBAFormat)
  texture.needsUpdate = true
  markerMaterial = new THREE.SpriteMaterial({
    map: texture, color: HIGHLIGHT_COLOR, sizeAttenuation: false,
    transparent: true, depthTest: false, depthWrite: false,
  })
  return markerMaterial
}

/** The world centre of the lit mesh nearest the selection's middle, so the ring sits on the object. */
function markerCentre(meshes: readonly THREE.Mesh[]): THREE.Vector3 | null {
  const centres: THREE.Vector3[] = []
  const union = new THREE.Box3()
  for (const mesh of meshes) {
    const geometry = mesh.geometry
    if (!geometry.boundingBox) geometry.computeBoundingBox()
    const box = geometry.boundingBox!.clone().applyMatrix4(mesh.matrixWorld)
    if (box.isEmpty()) continue
    union.union(box)
    centres.push(box.getCenter(new THREE.Vector3()))
  }
  if (!centres.length) return null
  const middle = union.getCenter(new THREE.Vector3())
  return centres.reduce((best, c) => (c.distanceToSquared(middle) < best.distanceToSquared(middle) ? c : best))
}

function root(): THREE.Object3D | null {
  return ((window as unknown as { globalGroup?: THREE.Object3D }).globalGroup) ?? null
}

function render() {
  ;(window as unknown as { __viewportRender?: () => unknown }).__viewportRender?.()
}

function shown(o: THREE.Object3D): boolean {
  for (let p: THREE.Object3D | null = o; p; p = p.parent) if (!p.visible) return false
  return true
}

function clear(scene: THREE.Object3D | null) {
  const old = scene?.getObjectByName(GROUP_NAME)
  if (old) old.removeFromParent()
}

function paint(selection: NetworkSelection | null): number {
  const scene = root()
  clear(scene)
  if (!scene || !selection) { render(); return 0 }
  scene.updateWorldMatrix(true, true)
  const inverse = scene.matrixWorld.clone().invert()
  const group = new THREE.Group()
  group.name = GROUP_NAME
  group.userData = { isHelper: true, isLiquidPostprocessHighlight: true }
  const lit: THREE.Mesh[] = []
  scene.traverse((o) => {
    const mesh = o as THREE.Mesh
    if (!mesh.isMesh || lit.length >= MAX_MESHES || !shown(mesh)) return
    const chain: UserData[] = []
    for (let p: THREE.Object3D | null = mesh; p && p !== scene; p = p.parent) chain.push(p.userData as UserData)
    if (!meshBelongsTo(chain, selection)) return
    lit.push(mesh)
  })
  for (const mesh of lit) {
    const copy = new THREE.Mesh(mesh.geometry, material)
    copy.matrixAutoUpdate = false
    copy.matrix.multiplyMatrices(inverse, mesh.matrixWorld)
    copy.renderOrder = 999
    copy.raycast = () => {}
    copy.userData = { isHelper: true, isLiquidPostprocessHighlight: true }
    group.add(copy)
  }
  const centre = markerCentre(lit)
  if (centre) {
    const marker = new THREE.Sprite(ringMaterial())
    marker.position.copy(centre.applyMatrix4(inverse))
    marker.scale.setScalar(MARKER_SCALE)
    marker.renderOrder = 1000
    marker.raycast = () => {}
    marker.userData = { isHelper: true, isLiquidPostprocessHighlight: true, isLiquidPostprocessMarker: true }
    group.add(marker)
  }
  if (lit.length) scene.add(group)
  render()
  return lit.length
}

let current: NetworkSelection | null = null
let unsubscribe: (() => void) | null = null

/** Show `selection` (null clears). Returns how many meshes are lit. */
export function setLiquidResultHighlight(selection: NetworkSelection | null): number {
  current = selection
  if (selection && !unsubscribe) unsubscribe = subscribeScene(() => { paint(current) })
  if (!selection && unsubscribe) { unsubscribe(); unsubscribe = null }
  return paint(selection)
}
