import * as THREE from 'three'

export const FOCUS_SELECTION_EVENT = 'dcdtuni-focus-selection'
const HELPER_TAGS = [
  'isHelper', 'isAnchorMarker', 'isSlotBoundaryGuide', 'isWaypoint',
  'isPipeWaypoint', 'isPipeControlPoint', 'isPipeSegmentWaypoint',
  'isJunctionPort', 'isJunctionPortSphere', 'isSeedHandle', 'isPatchHandle',
  'rotateGizmoAxis', 'rotateGizmoModelId', 'isMoveGizmo', 'isResizeHandle',
  'isFaceHandle', 'isSnapIndicator', 'isPreviewNormalHelper',
  'isLocInMesh', 'isExtraLocInMesh', 'isRZone', 'isRZPlane', 'isRZCenter',
  'isBMGrid', 'isSensorSphere', 'isSupplyFlow',
]
const finiteVector = (v: THREE.Vector3) => [v.x, v.y, v.z].every(Number.isFinite)
const finiteBox = (b: THREE.Box3) => !b.isEmpty() && finiteVector(b.min) && finiteVector(b.max)
function excluded(o: THREE.Object3D, helpers: ReadonlySet<THREE.Object3D>): boolean {
  return !o.visible || helpers.has(o) || /Helper$/.test(o.type) ||
    HELPER_TAGS.some(key => !!o.userData[key])
}

/** Bounds of actual visible surfaces, never room-wide helpers or authored inputs. */
export function selectedVisibleBounds(
  roots: ReadonlyMap<string, THREE.Object3D>,
  uids: readonly string[],
  helpers: ReadonlySet<THREE.Object3D> = new Set(),
  displayRoot?: THREE.Object3D | null,
): THREE.Box3 | null {
  const result = new THREE.Box3()
  const selected = new Set(uids)
  const candidates = new Set<THREE.Object3D>()
  selected.forEach(uid => { const root = roots.get(uid); if (root) candidates.add(root) })
  // Liquid pipes and blind caps live outside the structural root registry.
  // Read their existing identity-bound objects without reparenting the scene
  // or registering display buffers for CFD/STL export.
  displayRoot?.traverse(o => {
    const uid = o.userData.pipeModelId ?? o.userData.pipeId ?? o.userData.junctionId
    if (typeof uid === 'string' && selected.has(uid)) candidates.add(o)
  })
  for (const root of candidates) {
    let hidden = false
    for (let o: THREE.Object3D | null = root; o; o = o.parent) {
      if (excluded(o, helpers)) { hidden = true; break }
    }
    if (hidden) continue
    root.updateWorldMatrix(true, true)
    const visit = (o: THREE.Object3D) => {
      if (excluded(o, helpers)) return
      const mesh = o as THREE.Mesh
      if (mesh.isMesh) {
        const materials = Array.isArray(mesh.material) ? mesh.material : [mesh.material]
        const pos = mesh.geometry.getAttribute('position')
        if (materials.some(m => m.visible && m.opacity > 0) && pos?.itemSize === 3 && pos.count) {
          const local = new THREE.Box3()
          const point = new THREE.Vector3()
          let valid = o.matrixWorld.elements.every(Number.isFinite)
          for (let i = 0; valid && i < pos.count; i++) {
            point.fromBufferAttribute(pos, i)
            if (!finiteVector(point)) { valid = false; break }
            local.expandByPoint(point)
          }
          if (valid && finiteBox(local)) {
            const instance = mesh as THREE.InstancedMesh
            if (instance.isInstancedMesh) {
              const im = new THREE.Matrix4()
              for (let i = 0; i < instance.count; i++) {
                instance.getMatrixAt(i, im)
                const world = im.premultiply(o.matrixWorld)
                if (!world.elements.every(Number.isFinite)) continue
                const b = local.clone().applyMatrix4(world)
                if (finiteBox(b)) result.union(b)
              }
            } else {
              const b = local.applyMatrix4(o.matrixWorld)
              if (finiteBox(b)) result.union(b)
            }
          }
        }
      }
      o.children.forEach(visit)
    }
    visit(root)
  }
  return finiteBox(result) ? result : null
}

export interface ScreenRect { left: number; right: number; top: number; bottom: number }
export interface SafeFrameArea { minX: number; maxX: number; minY: number; maxY: number }
const FULL_AREA: SafeFrameArea = { minX: -1, maxX: 1, minY: -1, maxY: 1 }
const validRect = (r: ScreenRect) => Object.values(r).every(Number.isFinite) &&
  r.right > r.left && r.bottom > r.top

/** The canvas spans behind the panels. Fit the unobscured gap, in NDC. */
export function unobscuredFrameArea(
  canvas: ScreenRect,
  overlays: { left?: ScreenRect; right?: ScreenRect; toolbar?: ScreenRect; log?: ScreenRect },
  inset = 12,
): SafeFrameArea | null {
  if (!validRect(canvas) || !Number.isFinite(inset) || inset < 0) return null
  let { left, right, top, bottom } = canvas
  const intersects = (r: ScreenRect) => validRect(r) && r.left < right && r.right > left &&
    r.top < bottom && r.bottom > top
  if (overlays.left && intersects(overlays.left)) left = Math.max(left, overlays.left.right)
  if (overlays.right && intersects(overlays.right)) right = Math.min(right, overlays.right.left)
  if (overlays.toolbar && intersects(overlays.toolbar)) top = Math.max(top, overlays.toolbar.bottom)
  if (overlays.log && intersects(overlays.log)) bottom = Math.min(bottom, overlays.log.top)
  left += inset; right -= inset; top += inset; bottom -= inset
  if (right <= left || bottom <= top) return null
  const width = canvas.right - canvas.left, height = canvas.bottom - canvas.top
  return {
    minX: 2 * (left - canvas.left) / width - 1,
    maxX: 2 * (right - canvas.left) / width - 1,
    minY: 1 - 2 * (bottom - canvas.top) / height,
    maxY: 1 - 2 * (top - canvas.top) / height,
  }
}

/** Read layout only when the user asks; no observer, hidden control or CSS write. */
export function readUnobscuredFrameArea(canvas: HTMLElement): SafeFrameArea | null {
  const rect = (el: HTMLElement | null): ScreenRect | undefined => {
    if (!el || el.hidden) return undefined
    const style = getComputedStyle(el)
    if (style.display === 'none' || style.visibility === 'hidden') return undefined
    const r = el.getBoundingClientRect()
    return { left: r.left, right: r.right, top: r.top, bottom: r.bottom }
  }
  const canvasRect = rect(canvas)
  return canvasRect ? unobscuredFrameArea(canvasRect, {
    left: rect(document.getElementById('left-panel')),
    right: rect(document.getElementById('right-panel')),
    toolbar: rect(document.getElementById('toolbar')),
    log: rect(document.getElementById('log-panel')),
  }) : null
}

export interface SelectionFrame {
  position: THREE.Vector3
  target: THREE.Vector3
  near: number
  far: number
}
/** Exact eight-corner perspective fit, retaining the current viewing direction/up. */
export function selectionFrame(
  box: THREE.Box3,
  camera: THREE.PerspectiveCamera,
  oldTarget: THREE.Vector3,
  padding = 1.2,
  area: SafeFrameArea = FULL_AREA,
): SelectionFrame | null {
  if (!finiteBox(box) || !finiteVector(camera.position) || !finiteVector(oldTarget) ||
    !finiteVector(camera.up) || !Number.isFinite(camera.aspect) || camera.aspect <= 0 ||
    !Number.isFinite(camera.fov) || camera.fov <= 0 || camera.fov >= 179 ||
    !Number.isFinite(camera.zoom) || camera.zoom <= 0 ||
    !Number.isFinite(padding) || padding < 1 ||
    !Object.values(area).every(Number.isFinite) || area.minX < -1 || area.maxX > 1 ||
    area.minY < -1 || area.maxY > 1 || area.minX >= area.maxX || area.minY >= area.maxY ||
    camera.view?.enabled || camera.filmOffset !== 0) return null
  const direction = camera.position.clone().sub(oldTarget)
  if (direction.lengthSq() < 1e-20) {
    camera.getWorldDirection(direction).negate()
  }
  if (!finiteVector(direction) || direction.lengthSq() < 1e-20) return null
  direction.normalize()
  const right = new THREE.Vector3().crossVectors(camera.up, direction)
  if (!finiteVector(right) || right.lengthSq() < 1e-20) return null
  right.normalize()
  const up = new THREE.Vector3().crossVectors(direction, right).normalize()
  const centre = box.getCenter(new THREE.Vector3())
  const size = box.getSize(new THREE.Vector3())
  const span = size.length()
  const tanY = Math.tan(THREE.MathUtils.degToRad(camera.fov / 2)) / camera.zoom
  const tanX = tanY * camera.aspect
  if (!finiteVector(centre) || !Number.isFinite(span) || !Number.isFinite(tanX) ||
    !Number.isFinite(tanY) || tanX <= 0 || tanY <= 0) return null
  const cx = (area.minX + area.maxX) / 2, cy = (area.minY + area.maxY) / 2
  const minX = cx + (area.minX - cx) / padding, maxX = cx + (area.maxX - cx) / padding
  const minY = cy + (area.minY - cy) / padding, maxY = cy + (area.maxY - cy) / padding
  const depthFloor = Math.max(span * .01, 1e-6)
  let distance = depthFloor
  let minZ = Infinity, maxZ = -Infinity
  for (const x of [box.min.x, box.max.x]) for (const y of [box.min.y, box.max.y]) {
    for (const z of [box.min.z, box.max.z]) {
      const delta = new THREE.Vector3(x, y, z).sub(centre)
      const dx = delta.dot(right), dy = delta.dot(up), dz = delta.dot(direction)
      minZ = Math.min(minZ, dz); maxZ = Math.max(maxZ, dz)
      distance = Math.max(distance,
        (dx + maxX * dz * tanX) / ((maxX - cx) * tanX),
        (-dx - minX * dz * tanX) / ((cx - minX) * tanX),
        (dy + maxY * dz * tanY) / ((maxY - cy) * tanY),
        (-dy - minY * dz * tanY) / ((cy - minY) * tanY),
        dz + depthFloor)
    }
  }
  const near = Math.max(1e-9, (distance - maxZ) * .25)
  const far = distance - minZ + Math.max(span * .5, depthFloor)
  // A lateral camera+target shift puts the model at the usable gap's centre
  // without changing orientation or an asymmetric projection matrix. The same
  // ordinary position/target pair is already carried by the OV camera bridge.
  const target = centre.clone().addScaledVector(right, -cx * distance * tanX)
    .addScaledVector(up, -cy * distance * tanY)
  const position = target.clone().addScaledVector(direction, distance)
  if (!finiteVector(position) || ![near, far].every(Number.isFinite) ||
    near <= 0 || near >= distance - maxZ || far <= near) return null
  return { position, target, near, far }
}

export interface FocusContext {
  camera: THREE.PerspectiveCamera
  controls: { target: THREE.Vector3; update(): unknown }
  roots: ReadonlyMap<string, THREE.Object3D>
  uids: readonly string[]
  helpers?: ReadonlySet<THREE.Object3D>
  displayRoot?: THREE.Object3D | null
  frozen: boolean
  area?: SafeFrameArea | null
  beforeFocus?: () => void
  onFocused?: () => void
}

export function focusSelectedRenderRoots(ctx: FocusContext): boolean {
  if (ctx.frozen || !ctx.uids.length || ctx.area === null) return false
  const bounds = selectedVisibleBounds(ctx.roots, ctx.uids, ctx.helpers, ctx.displayRoot)
  if (!bounds) return false
  const frame = selectionFrame(bounds, ctx.camera, ctx.controls.target, 1.2, ctx.area)
  if (!frame) return false
  ctx.beforeFocus?.()
  ctx.camera.position.copy(frame.position)
  ctx.controls.target.copy(frame.target)
  // Keep the room's existing far range so ordinary zooming back out remains
  // useful. Only widen valid clip coverage; a tiny selection lowers near.
  ctx.camera.near = Number.isFinite(ctx.camera.near) && ctx.camera.near > 0
    ? Math.min(ctx.camera.near, frame.near) : frame.near
  ctx.camera.far = Number.isFinite(ctx.camera.far) && ctx.camera.far > ctx.camera.near
    ? Math.max(ctx.camera.far, frame.far) : frame.far
  ctx.camera.updateProjectionMatrix()
  ctx.camera.lookAt(frame.target)
  ctx.controls.update()
  ctx.camera.updateMatrixWorld()
  ctx.onFocused?.()
  return true
}

/** A button requests the viewport's CURRENT selection; no project or history write. */
export function requestSelectionFocus(): void {
  window.dispatchEvent(new Event(FOCUS_SELECTION_EVENT))
}
export function installSelectionFocus(getContext: () => FocusContext | null): () => void {
  const focus = () => { const ctx = getContext(); if (ctx) focusSelectedRenderRoots(ctx) }
  window.addEventListener(FOCUS_SELECTION_EVENT, focus)
  return () => window.removeEventListener(FOCUS_SELECTION_EVENT, focus)
}
