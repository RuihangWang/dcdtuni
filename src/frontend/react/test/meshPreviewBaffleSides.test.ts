import * as THREE from 'three'
import { describe, expect, it } from 'vitest'
import {
  buildMeshPreview,
  disposeMeshPreview,
  MERGE_THRESHOLD,
  setPatchesVisible,
  setPatchVisible,
} from '../src/run/meshPreview'
import type { MeshMeta, PatchMeta } from '../src/postprocess'

/**
 * A baffle drawn ALONE is visible from both sides.
 *
 * A zero-thickness baffle arrives as a master/slave pair of coincident sheets
 * with opposite normals, and the server hides every `_slave` by default
 * (postprocess.py `defaultVisible`). The per-patch preview drew both sides
 * FrontSide — right when both are shown (each seen from its own side, no
 * z-fight) and wrong in the default view, where the master alone is drawn and
 * is invisible from the side its normal points away from.
 *
 * Measured on Tri_angle (dcCart, 38 patches, per-patch mode): the master of
 * `Default_Rack_1_u17_partition` — the bottom of the U18..U23 box — faces +y,
 * into the box, so seen from below the partition drew nothing and read as a
 * missing sheet. The mesh itself was complete (area = STL, one component, free
 * edge = perimeter). The legacy draws every patch DoubleSide, and the merged
 * path here keeps the master and draws it DoubleSide.
 */

/** Patches of one triangle each; `names` in order. */
function fixture(names: readonly { name: string; visible?: boolean }[]): { meta: MeshMeta; buffer: ArrayBuffer } {
  const patches: PatchMeta[] = names.map((n) => ({
    name: n.name,
    nPoints: 3,
    nTriangles: 1,
    nEdges: 1,
    defaultVisible: n.visible !== false,
  }))
  const count = patches.length
  const positions = new Float32Array(count * 9)
  const indices = new Uint32Array(count * 3)
  const edges = new Uint32Array(count * 2)
  for (let i = 0; i < count; i++) {
    const base = i * 3
    positions.set([0, 0, 0, 1, 0, 0, 0, 0, 1], base * 3)
    indices.set([base, base + 1, base + 2], i * 3)
    edges.set([base, base + 1], i * 2)
  }
  const buffer = new ArrayBuffer(positions.byteLength + indices.byteLength + edges.byteLength)
  let off = 0
  new Float32Array(buffer, off, positions.length).set(positions)
  off += positions.byteLength
  new Uint32Array(buffer, off, indices.length).set(indices)
  off += indices.byteLength
  new Uint32Array(buffer, off, edges.length).set(edges)
  return { meta: { patches } as MeshMeta, buffer }
}

const side = (built: ReturnType<typeof buildMeshPreview>, name: string) =>
  (built.byPatch.get(name)!.surface.material as THREE.MeshPhongMaterial).side

describe('per-patch preview — which side a baffle sheet is drawn from', () => {
  const PAIR = [
    { name: 'Default_Rack_1_u17_partition_master' },
    // The server's default: slaves hidden.
    { name: 'Default_Rack_1_u17_partition_slave', visible: false },
    { name: 'Default_Rack_1_frame' },
  ]

  it('draws the master from both sides while its slave is hidden (the default view)', () => {
    const { meta, buffer } = fixture(PAIR)
    const built = buildMeshPreview(meta, buffer)
    expect(built.merged).toBe(false)
    expect(side(built, 'Default_Rack_1_u17_partition_master')).toBe(THREE.DoubleSide)
    disposeMeshPreview(built)
  })

  it('goes back to one side each once the slave is shown, so the two do not z-fight', () => {
    const { meta, buffer } = fixture(PAIR)
    const built = buildMeshPreview(meta, buffer)
    setPatchVisible(built, 'Default_Rack_1_u17_partition_slave', true)
    expect(side(built, 'Default_Rack_1_u17_partition_master')).toBe(THREE.FrontSide)
    expect(side(built, 'Default_Rack_1_u17_partition_slave')).toBe(THREE.FrontSide)
    setPatchesVisible(built, ['Default_Rack_1_u17_partition_slave'], false)
    expect(side(built, 'Default_Rack_1_u17_partition_master')).toBe(THREE.DoubleSide)
    disposeMeshPreview(built)
  })

  it('draws the slave from both sides when it is the one left on its own', () => {
    const { meta, buffer } = fixture(PAIR)
    const built = buildMeshPreview(meta, buffer)
    setPatchesVisible(built, ['Default_Rack_1_u17_partition_slave'], true)
    setPatchesVisible(built, ['Default_Rack_1_u17_partition_master'], false)
    expect(side(built, 'Default_Rack_1_u17_partition_slave')).toBe(THREE.DoubleSide)
    disposeMeshPreview(built)
  })

  it('leaves a master with no slave at all visible from both sides', () => {
    const { meta, buffer } = fixture([{ name: 'lonely_master' }])
    const built = buildMeshPreview(meta, buffer)
    expect(side(built, 'lonely_master')).toBe(THREE.DoubleSide)
    disposeMeshPreview(built)
  })

  it('does not touch ordinary patches', () => {
    const { meta, buffer } = fixture(PAIR)
    const built = buildMeshPreview(meta, buffer)
    expect(side(built, 'Default_Rack_1_frame')).toBe(THREE.DoubleSide)
    disposeMeshPreview(built)
  })

  it('is picked from below too: the raycaster honours the side', () => {
    // The sheet lies in y = 0 with its normal along -y... or +y; whichever it
    // is, a ray from the OTHER side must hit it while the slave is hidden.
    const { meta, buffer } = fixture(PAIR)
    const built = buildMeshPreview(meta, buffer)
    const surface = built.byPatch.get('Default_Rack_1_u17_partition_master')!.surface
    surface.updateMatrixWorld(true)
    const hitsFrom = (y: number) =>
      new THREE.Raycaster(new THREE.Vector3(0.2, y, 0.2), new THREE.Vector3(0, -Math.sign(y), 0)).intersectObject(
        surface,
      ).length
    expect(hitsFrom(1)).toBe(1)
    expect(hitsFrom(-1)).toBe(1)
    disposeMeshPreview(built)
  })
})

describe('merged preview — the highlight overlay', () => {
  it('draws a highlighted master overlay from both sides while its slave is hidden', () => {
    const names = [
      { name: 'sheet_master' },
      { name: 'sheet_slave', visible: false },
      ...Array.from({ length: MERGE_THRESHOLD }, (_, i) => ({ name: `p${i}` })),
    ]
    const { meta, buffer } = fixture(names)
    const built = buildMeshPreview(meta, buffer)
    expect(built.merged).toBe(true)
    // The overlay surfaces are off-scene and invisible in merged mode; the
    // list's visibility lives on `source.visible`, which is what decides.
    expect(side(built, 'sheet_master')).toBe(THREE.DoubleSide)
    setPatchesVisible(built, ['sheet_slave'], true)
    expect(side(built, 'sheet_master')).toBe(THREE.FrontSide)
    disposeMeshPreview(built)
  })
})
