/** Derived mesh-preview display data. It never becomes a .cfdproj model or solver input. */
import * as THREE from 'three'
import type { MeshPreviewSession } from '../run/meshPreviewSession'

type Vec3 = [number, number, number]
export interface RenderOverlayPrimitive {
  id: string
  patchName: string
  kind: 'triangles' | 'lines'
  positions: number[]
  indices: number[]
  matrix: number[]
  visible: boolean
  material: { color: Vec3; opacity: number; doubleSided: boolean; unlit: boolean }
  width?: number
}
export interface RenderOverlayPacket {
  schema: 'dcdtuni.render-overlay/v1'
  projectUid: string
  source: { kind: 'meshPreview'; meshSnapshotKey: string | null }
  primitives: RenderOverlayPrimitive[]
}

const MAX_SCALARS = 2_000_000

/** Ignore progress logs/folds; only drawn mesh changes should trigger OV transfer. */
export function meshOverlayRevision(session: MeshPreviewSession): string {
  return JSON.stringify([
    session.meshSnapshotKey, !!session.built, session.built?.merged ?? false,
    session.visible, session.meshOpacity, [...session.hidden].sort(),
  ])
}

function geometryArray(attribute: THREE.BufferAttribute | THREE.InterleavedBufferAttribute, components: 1 | 3): number[] {
  const values: number[] = []
  for (let i = 0; i < attribute.count; i++) {
    values.push(attribute.getX(i))
    if (components === 3) values.push(attribute.getY(i), attribute.getZ(i))
  }
  if (!values.every(Number.isFinite)) throw new Error('Mesh preview has non-finite geometry')
  return values
}

function primitive(
  object: THREE.Mesh | THREE.LineSegments, group: THREE.Group,
  key: string, patchName: string, kind: 'triangles' | 'lines', visible: boolean,
): RenderOverlayPrimitive {
  const geometry = object.geometry
  const positions = geometry.getAttribute('position')
  if (!positions || positions.itemSize !== 3) throw new Error(`Mesh patch ${patchName} has no vec3 positions`)
  const indices = geometry.index ? geometryArray(geometry.index, 1)
    : Array.from({ length: positions.count }, (_, index) => index)
  const colorMaterial = object.material as THREE.Material & { color?: THREE.Color }
  if (!colorMaterial.color || !Number.isFinite(colorMaterial.opacity) ||
      colorMaterial.opacity < 0 || colorMaterial.opacity > 1) {
    throw new Error(`Mesh patch ${patchName} has an unsupported material`)
  }
  object.updateMatrix()
  const matrix = group.matrixWorld.clone().multiply(object.matrix).toArray()
  return {
    id: `${key}/${encodeURIComponent(patchName)}/${kind}`,
    patchName, kind,
    positions: geometryArray(positions as THREE.BufferAttribute | THREE.InterleavedBufferAttribute, 3),
    indices, matrix, visible,
    material: {
      color: colorMaterial.color.toArray() as Vec3,
      opacity: colorMaterial.opacity,
      doubleSided: colorMaterial.side === THREE.DoubleSide,
      unlit: kind === 'lines',
    },
    ...(kind === 'lines' ? { width: .001 } : {}),
  }
}

/** Preserve each authored patch and its polygon edges; never infer a model UID from its name. */
export function buildMeshPreviewOverlay(projectUid: string, session: MeshPreviewSession): RenderOverlayPacket {
  if (!projectUid) throw new Error('Mesh overlay requires a saved project UID')
  const key = session.meshSnapshotKey
  const packet: RenderOverlayPacket = {
    schema: 'dcdtuni.render-overlay/v1', projectUid,
    source: { kind: 'meshPreview', meshSnapshotKey: key }, primitives: [],
  }
  const built = session.built
  if (!built || !session.visible) return packet
  if (!key) throw new Error('Mesh preview has no server case/mesh identity')
  const scalars = session.patches.reduce((sum, patch) =>
    sum + patch.nPoints * 6 + patch.nTriangles * 3 + patch.nEdges * 2, 0)
  if (scalars > MAX_SCALARS) throw new Error('Mesh preview needs binary OV transfer; JSON overlay limit exceeded')
  built.group.updateWorldMatrix(true, true)
  for (const patch of session.patches) {
    const pair = built.byPatch.get(patch.name)
    if (!pair) throw new Error(`Missing preview geometry for patch ${patch.name}`)
    if (!patch.nEdges) throw new Error(`Patch ${patch.name} lacks original polygon edges`)
    const visible = !session.hidden.has(patch.name) && !(built.merged && patch.name.endsWith('_slave'))
    packet.primitives.push(primitive(pair.surface, built.group, key, patch.name, 'triangles', visible))
    packet.primitives.push(primitive(pair.edges, built.group, key, patch.name, 'lines', visible))
  }
  return packet
}
