import * as THREE from 'three'
import { beforeAll, describe, expect, it } from 'vitest'

/**
 * The facet writer's output contract, tested against the shared module both
 * editors use — not against a copy of it.
 *
 * These assertions look pedantic (six decimals, this exact spacing) and that is
 * the point: `stl_oracle.py` compares exports BYTE FOR BYTE, and watertightness
 * depends on two adjacent solids rounding a shared vertex to the same string.
 */

let writer: {
  triangleHasCoincidentVertices(a: THREE.Vector3, b: THREE.Vector3, c: THREE.Vector3): boolean
  getTriangleFacetData(
    a: THREE.Vector3,
    b: THREE.Vector3,
    c: THREE.Vector3
  ): { normal: THREE.Vector3 } | null
  appendMeshFacets(parts: string[], mesh: THREE.Mesh, targetParent?: THREE.Group): void
  exportToStlString(
    name: string,
    mesh: THREE.Mesh,
    parts: string[],
    isMirrored: boolean,
    targetParent?: THREE.Group
  ): void
}

beforeAll(async () => {
  // THREE must be global before the module body runs — it reads it at call time
  // but the guard is clearer if it is there from the start.
  ;(globalThis as unknown as { THREE: unknown }).THREE = THREE
  await import('../../../shared/js/stl_writer.js')
  writer = (globalThis as unknown as { stlWriter: typeof writer }).stlWriter
})

const v = (x: number, y: number, z: number) => new THREE.Vector3(x, y, z)

/** One triangle in the XY plane, normal +Z, as a mesh at the origin. */
function triangleMesh(): THREE.Mesh {
  const geom = new THREE.BufferGeometry()
  geom.setAttribute(
    'position',
    new THREE.Float32BufferAttribute([0, 0, 0, 1, 0, 0, 0, 1, 0], 3)
  )
  return new THREE.Mesh(geom)
}

describe('degenerate triangles', () => {
  it('detects coincident vertices', () => {
    expect(writer.triangleHasCoincidentVertices(v(0, 0, 0), v(0, 0, 0), v(1, 0, 0))).toBe(true)
    expect(writer.triangleHasCoincidentVertices(v(0, 0, 0), v(1, 0, 0), v(0, 1, 0))).toBe(false)
  })

  it('drops a zero-area triangle rather than emitting a zero normal', () => {
    // Collinear: a facet here would carry a zero-length normal, which makes
    // surfaceFeatureExtract and snappyHexMesh disagree about the surface.
    expect(writer.getTriangleFacetData(v(0, 0, 0), v(1, 0, 0), v(2, 0, 0))).toBeNull()
    expect(writer.getTriangleFacetData(v(0, 0, 0), v(0, 0, 0), v(1, 0, 0))).toBeNull()
  })

  it('returns a UNIT normal for a real triangle', () => {
    const tri = writer.getTriangleFacetData(v(0, 0, 0), v(2, 0, 0), v(0, 2, 0))
    expect(tri).not.toBeNull()
    expect(tri!.normal.length()).toBeCloseTo(1, 12)
    expect(tri!.normal.z).toBeCloseTo(1, 12)
  })
})

describe('facet text', () => {
  it('writes six decimals in the expected layout', () => {
    const parts: string[] = []
    writer.appendMeshFacets(parts, triangleMesh(), new THREE.Group())
    expect(parts.join('')).toBe(
      '  facet normal 0.000000 0.000000 1.000000\n' +
        '    outer loop\n' +
        '      vertex 0.000000 0.000000 0.000000\n' +
        '      vertex 1.000000 0.000000 0.000000\n' +
        '      vertex 0.000000 1.000000 0.000000\n' +
        '    endloop\n' +
        '  endfacet\n'
    )
  })

  it('wraps facets in a named solid', () => {
    const parts: string[] = []
    writer.exportToStlString('room_wall', triangleMesh(), parts, false, new THREE.Group())
    const text = parts.join('')
    expect(text.startsWith('solid room_wall\n')).toBe(true)
    expect(text.endsWith('endsolid room_wall\n\n')).toBe(true)
  })

  it('emits WORLD coordinates, not local ones', () => {
    const mesh = triangleMesh()
    mesh.position.set(10, 0, 0)
    const parts: string[] = []
    writer.appendMeshFacets(parts, mesh, new THREE.Group())
    expect(parts.join('')).toContain('vertex 10.000000 0.000000 0.000000')
  })

  it('reverses winding under a mirrored transform so the normal still points out', () => {
    const mesh = triangleMesh()
    mesh.scale.set(-1, 1, 1) // determinant < 0
    const plain: string[] = []
    writer.appendMeshFacets(plain, triangleMesh(), new THREE.Group())
    const mirrored: string[] = []
    writer.appendMeshFacets(mirrored, mesh, new THREE.Group())
    // Un-mirrored the normal is +Z; a naive mirror would flip it to -Z.
    expect(plain.join('')).toContain('facet normal 0.000000 0.000000 1.000000')
    expect(mirrored.join('')).toContain('facet normal 0.000000 0.000000 1.000000')
  })

  it('skips a liquid manifold mesh entirely', () => {
    const mesh = triangleMesh()
    mesh.userData.isLiquidManifold = true
    const parts: string[] = []
    writer.appendMeshFacets(parts, mesh, new THREE.Group())
    expect(parts).toEqual([])
  })

  it('refuses an orphan mesh with no target parent', () => {
    // Without a parent the world matrix is the local one, so exporting would
    // silently write local coordinates.
    expect(() => writer.appendMeshFacets([], triangleMesh())).toThrow(/targetParent/)
  })
})
