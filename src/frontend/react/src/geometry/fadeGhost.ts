/**
 * The faded geometry as ONE mesh and ONE line set.
 *
 * Behind a result (post-processing's 3D模型透明度) or a mesh preview the
 * geometry is only context, drawn see-through. Drawn as itself it is every
 * model's meshes and edge lines, each transparent: GTC's 3,378 meshes became
 * 7,720 transparent draws, and the frame took 333 ms in draw-call overhead
 * (79 % of it in WebGLRenderer.render, measured with the Chrome profiler) --
 * 3 FPS whatever the volume or the surface did.
 *
 * While a fade is up the viewport hides the model roots and draws this ghost
 * instead: the same triangles and edges in the same places, each vertex in its
 * material's colour, at the fade's opacity. Textures are not carried -- at a
 * fade they read as their mean tint anyway. Nothing of the project changes;
 * when the fade goes the roots come back exactly as they were.
 */

import * as THREE from 'three'

const HELPER_FLAGS = [
  'isHelper', 'isAnchorMarker', 'isResizeHandle', 'isMoveGizmo', 'rotateGizmoAxis',
  'isSeedHandle', 'isPatchHandle', 'isWaypoint', 'isPipeWaypoint', 'isSlotBoundaryGuide',
]

export interface FadeGhost {
  readonly group: THREE.Group
  /** The roots it hid; they are shown again by `dispose`. */
  readonly hidden: readonly THREE.Object3D[]
  readonly triangles: number
  readonly segments: number
  setOpacity(value: number): void
  dispose(): void
}

function isHelper(object: THREE.Object3D): boolean {
  const data = object.userData as Record<string, unknown>
  return HELPER_FLAGS.some((flag) => data[flag])
}

/** Visible all the way up to (not including) `stop`. */
function shown(object: THREE.Object3D, stop: THREE.Object3D): boolean {
  for (let o: THREE.Object3D | null = object; o && o !== stop; o = o.parent) {
    if (!o.visible) return false
  }
  return true
}

function firstMaterial(object: THREE.Mesh | THREE.LineSegments): THREE.Material | null {
  const m = object.material
  return (Array.isArray(m) ? m[0] : m) ?? null
}

function colourOf(material: THREE.Material | null): THREE.Color {
  const c = (material as unknown as { color?: THREE.Color } | null)?.color
  return c ? c : new THREE.Color(0.6, 0.6, 0.6)
}

/**
 * Merge every shown mesh and edge set under `roots` into a ghost added to
 * `parent`, and hide those roots. Returns null when there is nothing to merge.
 */
export function buildFadeGhost(
  roots: Iterable<THREE.Object3D | null | undefined>,
  parent: THREE.Object3D,
  opacity: number,
): FadeGhost | null {
  parent.updateMatrixWorld(true)
  const toParent = parent.matrixWorld.clone().invert()
  const meshes: THREE.Mesh[] = []
  const lines: THREE.LineSegments[] = []
  const hidden: THREE.Object3D[] = []
  for (const root of roots) {
    if (!root || root.name === 'meshPreview' || !root.visible) continue
    hidden.push(root)
    root.traverse((o) => {
      if (isHelper(o) || !shown(o, root.parent ?? root)) return
      if ((o as THREE.Mesh).isMesh && !(o as THREE.InstancedMesh).isInstancedMesh) {
        const material = firstMaterial(o as THREE.Mesh)
        if (!material || (material as THREE.ShaderMaterial).isShaderMaterial) return
        if ((o as THREE.Mesh).geometry?.getAttribute('position')) meshes.push(o as THREE.Mesh)
      } else if ((o as THREE.LineSegments).isLineSegments) {
        if ((o as THREE.LineSegments).geometry?.getAttribute('position')) lines.push(o as THREE.LineSegments)
      }
    })
  }
  if (!meshes.length && !lines.length) return null

  // Triangles: count, then fill one indexed geometry.
  let vertexCount = 0
  let indexCount = 0
  for (const mesh of meshes) {
    const g = mesh.geometry
    vertexCount += g.getAttribute('position').count
    indexCount += g.index ? g.index.count : g.getAttribute('position').count
  }
  const positions = new Float32Array(vertexCount * 3)
  const normals = new Float32Array(vertexCount * 3)
  const colours = new Float32Array(vertexCount * 3)
  const indices = vertexCount > 65535 ? new Uint32Array(indexCount) : new Uint16Array(indexCount)
  const v = new THREE.Vector3()
  const n = new THREE.Vector3()
  const matrix = new THREE.Matrix4()
  const normalMatrix = new THREE.Matrix3()
  let vBase = 0
  let iBase = 0
  for (const mesh of meshes) {
    const g = mesh.geometry
    const pos = g.getAttribute('position')
    const nor = g.getAttribute('normal')
    matrix.multiplyMatrices(toParent, mesh.matrixWorld)
    normalMatrix.getNormalMatrix(matrix)
    const c = colourOf(firstMaterial(mesh))
    for (let k = 0; k < pos.count; k++) {
      const o = (vBase + k) * 3
      v.fromBufferAttribute(pos, k).applyMatrix4(matrix)
      positions[o] = v.x; positions[o + 1] = v.y; positions[o + 2] = v.z
      if (nor) {
        n.fromBufferAttribute(nor, k).applyMatrix3(normalMatrix).normalize()
        normals[o] = n.x; normals[o + 1] = n.y; normals[o + 2] = n.z
      }
      colours[o] = c.r; colours[o + 1] = c.g; colours[o + 2] = c.b
    }
    if (g.index) {
      const src = g.index
      for (let k = 0; k < src.count; k++) indices[iBase + k] = vBase + src.getX(k)
      iBase += src.count
    } else {
      for (let k = 0; k < pos.count; k++) indices[iBase + k] = vBase + k
      iBase += pos.count
    }
    vBase += pos.count
  }
  const group = new THREE.Group()
  group.name = 'fadeGhost'
  group.userData.isFadeGhost = true
  let surfaceMaterial: THREE.MeshStandardMaterial | null = null
  if (meshes.length) {
    const geometry = new THREE.BufferGeometry()
    geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3))
    geometry.setAttribute('normal', new THREE.BufferAttribute(normals, 3))
    geometry.setAttribute('color', new THREE.BufferAttribute(colours, 3))
    geometry.setIndex(new THREE.BufferAttribute(indices, 1))
    surfaceMaterial = new THREE.MeshStandardMaterial({
      vertexColors: true, transparent: true, opacity, depthWrite: false,
      roughness: 0.75, metalness: 0.05, side: THREE.DoubleSide,
    })
    const surface = new THREE.Mesh(geometry, surfaceMaterial)
    surface.name = 'fadeGhostSurface'
    surface.raycast = () => {}
    group.add(surface)
  }

  // Edges: one non-indexed segment list.
  let segmentVertices = 0
  for (const line of lines) {
    const g = line.geometry
    segmentVertices += g.index ? g.index.count : g.getAttribute('position').count
  }
  let lineMaterial: THREE.LineBasicMaterial | null = null
  if (segmentVertices) {
    const lp = new Float32Array(segmentVertices * 3)
    const lc = new Float32Array(segmentVertices * 3)
    let base = 0
    for (const line of lines) {
      const g = line.geometry
      const pos = g.getAttribute('position')
      matrix.multiplyMatrices(toParent, line.matrixWorld)
      const c = colourOf(firstMaterial(line))
      const count = g.index ? g.index.count : pos.count
      for (let k = 0; k < count; k++) {
        const o = (base + k) * 3
        v.fromBufferAttribute(pos, g.index ? g.index.getX(k) : k).applyMatrix4(matrix)
        lp[o] = v.x; lp[o + 1] = v.y; lp[o + 2] = v.z
        lc[o] = c.r; lc[o + 1] = c.g; lc[o + 2] = c.b
      }
      base += count
    }
    const geometry = new THREE.BufferGeometry()
    geometry.setAttribute('position', new THREE.BufferAttribute(lp, 3))
    geometry.setAttribute('color', new THREE.BufferAttribute(lc, 3))
    lineMaterial = new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, opacity, depthWrite: false })
    const edges = new THREE.LineSegments(geometry, lineMaterial)
    edges.name = 'fadeGhostEdges'
    edges.raycast = () => {}
    group.add(edges)
  }

  group.visible = opacity > 0.001
  parent.add(group)
  for (const root of hidden) {
    root.visible = false
    root.userData.hiddenByFadeGhost = true
  }
  let disposed = false
  return {
    group,
    hidden,
    triangles: indexCount / 3,
    segments: segmentVertices / 2,
    setOpacity(value: number) {
      if (surfaceMaterial) surfaceMaterial.opacity = value
      if (lineMaterial) lineMaterial.opacity = value
      group.visible = value > 0.001
    },
    dispose() {
      if (disposed) return
      disposed = true
      group.parent?.remove(group)
      group.traverse((o) => {
        const mesh = o as THREE.Mesh
        mesh.geometry?.dispose()
        const m = mesh.material
        if (m) (Array.isArray(m) ? m : [m]).forEach((x) => x.dispose())
      })
      for (const root of hidden) {
        if (!root.userData.hiddenByFadeGhost) continue
        delete root.userData.hiddenByFadeGhost
        root.visible = true
      }
    },
  }
}
