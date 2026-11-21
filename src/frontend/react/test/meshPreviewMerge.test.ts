import * as THREE from 'three'
import { describe, expect, it } from 'vitest'
import {
  buildMeshPreview,
  disposeMeshPreview,
  MERGE_THRESHOLD,
  mergedPatchAtTriangle,
  setMeshOpacity,
  setPatchesVisible,
  setPatchVisible,
} from '../src/run/meshPreview'
import type { MeshMeta, PatchMeta } from '../src/postprocess'

/**
 * A big mesh draws as TWO objects, not two per patch.
 *
 * A data centre meshes into well over a thousand patches — 200 racks at seven
 * each — and the preview built one surface plus one wireframe for every one of
 * them, so the cases that most need looking at were the ones it could not draw.
 * The legacy collapses above 200 into a single merged surface and a single
 * merged edge set (`_MP_MERGE_THRESHOLD`, STL_generate.html:24438) and rebuilds
 * that pair whenever a checkbox moves.
 */

/** One triangle and one edge per patch, at a distinct height so they differ. */
function fixture(count: number, visible = true): { meta: MeshMeta; buffer: ArrayBuffer } {
  const patches: PatchMeta[] = Array.from({ length: count }, (_, i) => ({
    name: `p${i}`,
    nPoints: 3,
    nTriangles: 1,
    nEdges: 1,
    defaultVisible: visible,
  }))
  const positions = new Float32Array(count * 3 * 3)
  const indices = new Uint32Array(count * 3)
  const edges = new Uint32Array(count * 2)
  for (let i = 0; i < count; i++) {
    const base = i * 3
    positions.set([0, i, 0, 1, i, 0, 0, i, 1], base * 3)
    indices.set([base, base + 1, base + 2], i * 3)
    edges.set([base, base + 1], i * 2)
  }
  // The preview binary has NO header: it is the three arrays back to back,
  // sized by the patch table. `decodeMesh` checks the total length against that
  // table and throws if they disagree, which is what caught the first version
  // of this fixture writing a JSON header the format does not have.
  const buffer = new ArrayBuffer(
    positions.byteLength + indices.byteLength + edges.byteLength,
  )
  let off = 0
  new Float32Array(buffer, off, positions.length).set(positions)
  off += positions.byteLength
  new Uint32Array(buffer, off, indices.length).set(indices)
  off += indices.byteLength
  new Uint32Array(buffer, off, edges.length).set(edges)
  return { meta: { patches } as MeshMeta, buffer }
}

function build(count: number, visible = true) {
  const { meta, buffer } = fixture(count, visible)
  return buildMeshPreview(meta, buffer)
}

/** What the renderer would actually walk. */
function drawn(group: THREE.Group): number {
  let n = 0
  group.children.forEach((o) => {
    if ((o as THREE.Mesh).isMesh || (o as THREE.LineSegments).isLineSegments) n++
  })
  return n
}

function mergedTriangles(built: ReturnType<typeof build>): number {
  const index = built.mergedPair?.surface.geometry.getIndex()
  return index ? index.count / 3 : 0
}

describe('below the threshold', () => {
  it('draws one surface and one wireframe per patch', () => {
    const built = build(5)
    expect(built.merged).toBe(false)
    expect(drawn(built.group)).toBe(10)
    expect(built.byPatch.size).toBe(5)
    disposeMeshPreview(built)
  })

  it('toggles a patch by its own flag, with no rebuild', () => {
    const built = build(5)
    setPatchVisible(built, 'p2', false)
    expect(built.byPatch.get('p2')!.surface.visible).toBe(false)
    expect(built.mergedPair).toBeNull()
    disposeMeshPreview(built)
  })
})

describe('above the threshold', () => {
  const COUNT = MERGE_THRESHOLD + 1

  it('draws exactly two objects however many patches there are', () => {
    const built = build(COUNT)
    expect(built.merged).toBe(true)
    // Two — not 2 × 201.
    expect(drawn(built.group)).toBe(2)
    disposeMeshPreview(built)
  })

  it('still knows every patch by name, for highlighting and picking', () => {
    const built = build(COUNT)
    expect(built.byPatch.size).toBe(COUNT)
    // Built but deliberately off-scene, as the legacy leaves them.
    expect(built.group.children).not.toContain(built.byPatch.get('p0')!.surface)
    disposeMeshPreview(built)
  })

  it('merges only the patches that are visible', () => {
    const built = build(COUNT)
    expect(mergedTriangles(built)).toBe(COUNT)
    setPatchVisible(built, 'p0', false)
    setPatchVisible(built, 'p1', false)
    expect(mergedTriangles(built)).toBe(COUNT - 2)
    disposeMeshPreview(built)
  })

  it('rebuilds when a patch comes back', () => {
    const built = build(COUNT)
    setPatchVisible(built, 'p0', false)
    const without = mergedTriangles(built)
    setPatchVisible(built, 'p0', true)
    expect(mergedTriangles(built)).toBe(without + 1)
    disposeMeshPreview(built)
  })

  it('starts from defaultVisible, not from everything', () => {
    const built = build(COUNT, false)
    // Nothing visible means nothing to draw — and an empty geometry would
    // still cost a draw call and a normals pass.
    expect(drawn(built.group)).toBe(0)
    expect(built.mergedPair).toBeNull()
    disposeMeshPreview(built)
  })

  it('applies opacity to the merged surface, which is the one on screen', () => {
    const built = build(COUNT)
    setMeshOpacity(built, 0.4)
    const mat = built.mergedPair!.surface.material as THREE.MeshPhongMaterial
    expect(mat.opacity).toBeCloseTo(0.4, 6)
    // Depth-correct at every opacity: depthWrite off is what let a closed patch
    // blend its own far hull in creation order — the "shadow" mud.
    expect(mat.depthWrite).toBe(true)
    disposeMeshPreview(built)
  })

  it('keeps that opacity across a re-merge', () => {
    const built = build(COUNT)
    setMeshOpacity(built, 0.4)
    setPatchVisible(built, 'p3', false)
    const mat = built.mergedPair!.surface.material as THREE.MeshPhongMaterial
    // The re-merge builds a NEW material; reading the slider's value off the
    // source is what stops it reverting to the default.
    expect(mat.opacity).toBeCloseTo(0.4, 6)
    disposeMeshPreview(built)
  })

  it('disposes the off-scene per-patch objects too', () => {
    const built = build(COUNT)
    let disposed = 0
    built.byPatch.forEach(({ surface }) => {
      surface.geometry.addEventListener('dispose', () => {
        disposed++
      })
    })
    disposeMeshPreview(built)
    // A traversal of the group would have missed every one of them.
    expect(disposed).toBe(COUNT)
  })

  it('re-merges ONCE for a batch, not once per patch', () => {
    const built = build(COUNT)
    // Counted by patching `dispose` on the prototype, because every re-merge
    // throws the previous merged geometry away. The first version of this test
    // re-armed a listener from INSIDE the dispose handler — at which point the
    // replacement geometry does not exist yet, so the re-arm silently failed
    // and it counted 1 whichever implementation ran. A test that cannot fail is
    // worse than no test.
    const real = THREE.BufferGeometry.prototype.dispose
    let disposals = 0
    THREE.BufferGeometry.prototype.dispose = function (this: THREE.BufferGeometry) {
      disposals++
      return real.call(this)
    }
    try {
      setPatchesVisible(built, [...built.byPatch.keys()], false)
    } finally {
      THREE.BufferGeometry.prototype.dispose = real
    }

    // One rebuild disposes the surface and its edge set: two geometries. A
    // per-patch loop would be in the hundreds.
    expect(disposals).toBeLessThanOrEqual(2)
    expect(built.mergedPair).toBeNull()

    setPatchesVisible(built, [...built.byPatch.keys()], true)
    expect(mergedTriangles(built)).toBe(COUNT)
    disposeMeshPreview(built)
  })

  it('still lets a single patch be toggled on its own', () => {
    const built = build(COUNT)
    setPatchVisible(built, 'p7', false)
    expect(mergedTriangles(built)).toBe(COUNT - 1)
    disposeMeshPreview(built)
  })
})

describe('picking on the merged surface', () => {
  /** Like `fixture`, but with the given names, so slave pairs can be planted. */
  function named(names: string[], visible = true) {
    const { meta, buffer } = fixture(names.length, visible)
    meta.patches!.forEach((p, i) => {
      p.name = names[i]!
    })
    return buildMeshPreview(meta, buffer)
  }
  const names = Array.from({ length: MERGE_THRESHOLD + 1 }, (_, i) => `p${i}`)

  it('resolves a triangle to the patch that actually put it in the buffer', () => {
    const built = named(names)
    expect(mergedPatchAtTriangle(built, 0)).toBe('p0')
    expect(mergedPatchAtTriangle(built, MERGE_THRESHOLD)).toBe(`p${MERGE_THRESHOLD}`)
    expect(mergedPatchAtTriangle(built, MERGE_THRESHOLD + 1)).toBeNull()
    expect(mergedPatchAtTriangle(built, -1)).toBeNull()
    disposeMeshPreview(built)
  })

  it('skips the _slave duplicates the merge leaves out, so later patches do not shift', () => {
    // p1 is drawn as its master only; a count over the patch table minus the
    // hidden set would name p1_slave for triangle 1 and be one off for every
    // patch after it.
    const withPair = names.slice()
    withPair[1] = 'sheet_master'
    withPair[2] = 'sheet_slave'
    const built = named(withPair)
    expect(built.mergedPair?.surface.geometry.index?.count).toBe(MERGE_THRESHOLD * 3)
    expect(mergedPatchAtTriangle(built, 1)).toBe('sheet_master')
    expect(mergedPatchAtTriangle(built, 2)).toBe('p3')
    expect(mergedPatchAtTriangle(built, MERGE_THRESHOLD - 1)).toBe(`p${MERGE_THRESHOLD}`)
    disposeMeshPreview(built)
  })

  it('follows a visibility change: hidden patches leave the buffer and the map', () => {
    const built = named(names)
    setPatchesVisible(built, ['p0', 'p1'], false)
    expect(mergedPatchAtTriangle(built, 0)).toBe('p2')
    setPatchVisible(built, 'p0', true)
    expect(mergedPatchAtTriangle(built, 0)).toBe('p0')
    expect(mergedPatchAtTriangle(built, 1)).toBe('p2')
    disposeMeshPreview(built)
  })
})
