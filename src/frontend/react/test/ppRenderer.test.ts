import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import * as THREE from 'three'
import { describe, expect, it } from 'vitest'
import { decodeStreamlines } from '../src/postprocess'
import { buildSeedSphere, buildSlice, buildSliceRing, buildStreamlines, buildSurface } from '../src/postprocess/ppRenderer'
import type { DecodedMesh, SlicePayload } from '../src/postprocess'

const FIX = join(__dirname, '__fixtures__')

/** Two triangles sharing an edge, split across two named patches. */
function tinyMesh(): DecodedMesh {
  return {
    positions: new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0, 1, 1, 0]),
    indices: new Uint32Array([0, 1, 2, 1, 3, 2]),
    edges: new Uint32Array([]),
    patches: [
      { name: 'wall_a', nPoints: 3, nTriangles: 1, nEdges: 0, defaultVisible: true },
      { name: 'wall_b', nPoints: 1, nTriangles: 1, nEdges: 0, defaultVisible: true },
    ],
    totals: { points: 4, triangles: 2, edges: 0 },
  }
}

describe('surface', () => {
  it('builds one geometry with the whole index buffer', () => {
    const s = buildSurface(tinyMesh(), { colorMap: 'coolwarm' })
    expect(s.object.geometry.getIndex()?.count).toBe(6)
    expect(s.object.geometry.getAttribute('position').count).toBe(4)
    s.dispose()
  })

  it('carries the RAW scalar, not a baked colour', () => {
    // The LUT is sampled per fragment in the shader. Baking RGB here and
    // letting the rasteriser interpolate it blends through hues that are in
    // neither end colour — a coolwarm triangle spanning blue to red would pass
    // through grey-purple, which reads as banding the data does not have.
    const s = buildSurface(tinyMesh(), {
      values: new Float32Array([0, 1, 2, 3]),
      colorMap: 'coolwarm',
      autoScale: true,
    })
    expect(s.range).toEqual([0, 3])
    const attr = s.object.geometry.getAttribute('fieldValue')
    expect(attr.count).toBe(4)
    expect(attr.getX(2)).toBe(2)
    expect(s.object.geometry.getAttribute('color')).toBeUndefined()
    s.dispose()
  })

  it('hands the colour limits to the shader, not to a colour table', () => {
    const s = buildSurface(tinyMesh(), {
      values: new Float32Array([0, 1, 2, 3]),
      climMin: 288,
      climMax: 306,
      colorMap: 'coolwarm',
    })
    const m = s.object.material as THREE.ShaderMaterial
    expect(m.uniforms.uMinValue?.value).toBe(288)
    expect(m.uniforms.uMaxValue?.value).toBe(306)
    s.dispose()
  })

  it('draws above the model geometry', () => {
    // renderOrder 100, the legacy layering. At 0 it sorts against the racks by
    // depth alone and the result flickers in and out of them.
    const s = buildSurface(tinyMesh(), { colorMap: 'coolwarm' })
    expect(s.object.renderOrder).toBe(100)
    s.dispose()
  })

  it('REFUSES a field that is not exactly as long as the mesh', () => {
    // Both directions matter. Short means values from another run. LONG means
    // the field body's 16-byte binary header was read as four data values,
    // which shifts every point's value by four positions and still renders a
    // plausible, structured, entirely wrong picture. This check is what caught
    // exactly that on the 100-step Base_leakage run.
    expect(() =>
      buildSurface(tinyMesh(), { values: new Float32Array([1, 2]), colorMap: 'coolwarm' }),
    ).toThrow(/same geometry/)
    expect(() =>
      buildSurface(tinyMesh(), {
        values: new Float32Array([0, 1, 2, 3, 99, 99]),
        colorMap: 'coolwarm',
      }),
    ).toThrow(/same geometry/)
  })

  it('drops the triangles of a hidden patch, keeping the points', () => {
    const s = buildSurface(tinyMesh(), { colorMap: 'coolwarm', hidden: new Set(['wall_b']) })
    expect(s.object.geometry.getIndex()?.count).toBe(3)
    // The point array is shared, so hiding a patch must not renumber anything.
    expect(s.object.geometry.getAttribute('position').count).toBe(4)
    s.dispose()
  })

  it('honours explicit colour limits over the data range', () => {
    const s = buildSurface(tinyMesh(), {
      values: new Float32Array([0, 1, 2, 3]),
      climMin: 10,
      climMax: 20,
      colorMap: 'coolwarm',
    })
    expect(s.range).toEqual([10, 20])
    s.dispose()
  })
})

describe('slice', () => {
  const slice: SlicePayload = {
    positions: new Float32Array([0, 0, 0, 1, 0, 0, 0, 0, 1]),
    indices: new Uint32Array([0, 1, 2]),
    values: new Float32Array([290, 292, 294]),
    scalarRange: [290, 294],
    field: 'T',
    nPoints: 3,
    nTriangles: 1,
  }

  it('does not write depth', () => {
    // A slice sits inside the room; writing depth makes it fight the boundary
    // surface it cuts through and the plane flickers as the camera moves.
    const s = buildSlice(slice, 'coolwarm')
    const m = s.object.material as THREE.ShaderMaterial
    expect(m.depthWrite).toBe(false)
    expect(m.side).toBe(THREE.DoubleSide)
    s.dispose()
  })

  it('is UNLIT, so one temperature is one colour across the cut', () => {
    // A lit plane shades unevenly, and the same value then reads as two
    // different colours on the same slice — the legend stops meaning anything.
    const s = buildSlice(slice, 'coolwarm')
    const m = s.object.material as THREE.ShaderMaterial
    expect(m.fragmentShader).not.toContain('vNormal')
    expect(s.object.geometry.getAttribute('fieldValue').count).toBe(3)
    s.dispose()
  })

  it('uses the payload range when no limits are given', () => {
    const s = buildSlice(slice, 'coolwarm')
    expect(s.range).toEqual([290, 294])
    s.dispose()
  })
})

describe('streamlines', () => {
  const set = decodeStreamlines(
    (() => {
      const b = readFileSync(join(FIX, 'pp_streamlines_small.bin'))
      return b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength)
    })(),
  )

  it('lofts ONE tube per polyline, never bridging between them', () => {
    // A bridge joins the end of one streamline to the start of the next and
    // draws a tube straight across the room that no particle travelled.
    const s = buildStreamlines(set, 'coolwarm')
    const drawable = set.lines.filter((l) => l.count >= 2).length
    expect(s.object.children).toHaveLength(drawable)
    s.dispose()
  })

  it('gives every tube vertex a scalar within the polyline it came from', () => {
    const s = buildStreamlines(set, 'coolwarm')
    const tube = s.object.children[0] as THREE.Mesh
    const values = tube.geometry.getAttribute('fieldValue')
    const positions = tube.geometry.getAttribute('position')
    expect(values.count).toBe(positions.count)
    const first = set.lines[0]!
    const own = Array.from(set.scalars.subarray(first.start, first.start + first.count))
    for (let i = 0; i < values.count; i++) expect(own).toContain(values.getX(i))
    s.dispose()
  })

  it('honours the tube radius', () => {
    const thin = buildStreamlines(set, 'coolwarm', undefined, 0.005)
    const fat = buildStreamlines(set, 'coolwarm', undefined, 0.05)
    const extent = (b: { object: THREE.Group }) => {
      const g = (b.object.children[0] as THREE.Mesh).geometry
      g.computeBoundingBox()
      return g.boundingBox!.getSize(new THREE.Vector3()).length()
    }
    expect(extent(fat)).toBeGreaterThan(extent(thin))
    thin.dispose()
    fat.dispose()
  })

  it('skips a one-point polyline instead of producing NaN vertices', () => {
    // TubeGeometry on a curve with no direction emits NaN, which poisons the
    // whole group's bounding sphere and makes everything vanish from culling.
    const s = buildStreamlines(
      {
        lines: [{ start: 0, count: 1 }],
        points: new Float32Array([1, 2, 3]),
        scalars: new Float32Array([7]),
        scalarRange: [7, 7],
        field: 'speed',
      },
      'coolwarm',
    )
    expect(s.object.children).toHaveLength(0)
    s.dispose()
  })

  it('produces nothing for an empty set rather than throwing', () => {
    const s = buildStreamlines(
      { lines: [], points: new Float32Array(), scalars: new Float32Array(), scalarRange: [0, 0], field: 'speed' },
      'coolwarm',
    )
    expect(s.object.children).toHaveLength(0)
    s.dispose()
  })
})

describe('handles', () => {
  it('puts the seed sphere at its centre, SCALED to its radius', () => {
    // Unit geometry plus scale, not a re-built sphere: the translate gizmo
    // attaches to this object, and rebuilding the geometry on every radius
    // change would drop the grab mid-drag.
    const s = buildSeedSphere([1, 2, 3], 0.75)
    expect(s.object.position.toArray()).toEqual([1, 2, 3])
    expect(s.object.scale.toArray()).toEqual([0.75, 0.75, 0.75])
    s.dispose()
  })

  it('draws both preview handles THROUGH the geometry', () => {
    // The seed ball sits inside the racks and the ring cuts through them. With
    // depth testing on, the control you are trying to position is hidden by the
    // thing you are positioning it against.
    const seed = buildSeedSphere([0, 0, 0], 1)
    const ring = buildSliceRing([0, 0, 0], [0, 1, 0], 3)
    for (const o of [seed, ring]) {
      const m = o.object.material as THREE.MeshBasicMaterial
      expect(m.depthTest).toBe(false)
      expect(m.depthWrite).toBe(false)
    }
    // …and above every result layer: surface 100, tubes 110, slice 120.
    expect(ring.object.renderOrder).toBe(998)
    expect(seed.object.renderOrder).toBe(999)
    seed.dispose()
    ring.dispose()
  })

  it('gives the ring a tube, so it survives being seen edge-on', () => {
    // A flat annulus vanishes edge-on, which is exactly the view a user takes
    // to check a cut plane's height.
    const r = buildSliceRing([0, 0, 0], [0, 1, 0], 4)
    const box = r.object.geometry.boundingBox ?? (r.object.geometry.computeBoundingBox(), r.object.geometry.boundingBox!)
    // 2% of 4 m, doubled for both sides of the tube.
    expect(box.max.z - box.min.z).toBeCloseTo(0.16, 3)
    r.dispose()
  })

  it('aims the slice ring along the plane normal', () => {
    const r = buildSliceRing([0, 1, 0], [0, 1, 0], 2)
    // RingGeometry lies in XY, so its +Z must end up pointing along the normal.
    const z = new THREE.Vector3(0, 0, 1).applyQuaternion(r.object.quaternion)
    expect(z.x).toBeCloseTo(0, 6)
    expect(z.y).toBeCloseTo(1, 6)
    expect(z.z).toBeCloseTo(0, 6)
    r.dispose()
  })

  it('survives a zero normal instead of producing NaN', () => {
    const r = buildSliceRing([0, 0, 0], [0, 0, 0], 1)
    expect([...r.object.quaternion.toArray()].every(Number.isFinite)).toBe(true)
    r.dispose()
  })
})
