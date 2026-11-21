import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import {
  decodeField,
  decodeMesh,
  decodeSlice,
  decodeStreamlines,
  pointRange,
  triangleRange,
  type MeshMeta,
} from '../src/postprocess'

/**
 * The binary decoders, checked against the server's real output.
 *
 * These buffers carry no self-describing length except the slice header, so a
 * decoder that is off by one section reads garbage rather than failing — a
 * mesh whose triangles index into the wrong points still renders, as noise.
 * The mesh metadata here is a VERBATIM capture from
 * /api/pp/mesh on the 100-step Base_leakage run: 53 patches, 164568 points,
 * 316788 triangles, and the buffer the server sent for it was 5776272 bytes.
 */

const FIX = join(__dirname, '__fixtures__')
const meshMeta = JSON.parse(readFileSync(join(FIX, 'pp_mesh_meta.json'), 'utf8')) as MeshMeta

/** Build a slice payload in the server's exact format. */
function packSlice(header: Record<string, unknown>, points: number[], tris: number[], scalars: number[]) {
  const json = new TextEncoder().encode(JSON.stringify(header))
  const pad = (4 - (json.length % 4)) % 4
  const padded = new Uint8Array(json.length + pad)
  padded.set(json)
  const total = 4 + padded.length + points.length * 4 + tris.length * 4 + scalars.length * 4
  const buf = new ArrayBuffer(total)
  const view = new DataView(buf)
  view.setUint32(0, padded.length, true)
  new Uint8Array(buf, 4, padded.length).set(padded)
  let off = 4 + padded.length
  new Float32Array(buf, off, points.length).set(points)
  off += points.length * 4
  new Uint32Array(buf, off, tris.length).set(tris)
  off += tris.length * 4
  new Float32Array(buf, off, scalars.length).set(scalars)
  return { buf, pad }
}

describe('mesh buffer', () => {
  it('agrees with the real capture on total size', () => {
    // The number the server actually sent for this metadata.
    let points = 0
    let tris = 0
    let edges = 0
    for (const p of meshMeta.patches) {
      points += p.nPoints
      tris += p.nTriangles
      edges += p.nEdges
    }
    expect(meshMeta.patches.length).toBe(53)
    expect(points).toBe(164568)
    expect(tris).toBe(316788)
    expect(points * 3 * 4 + tris * 3 * 4 + edges * 2 * 4).toBe(5776272)
  })

  it('splits a buffer into the three sections', () => {
    const meta: MeshMeta = {
      patches: [
        { name: 'a', nPoints: 3, nTriangles: 1, nEdges: 2, defaultVisible: true },
        { name: 'b', nPoints: 2, nTriangles: 0, nEdges: 1, defaultVisible: false },
      ],
    }
    const buf = new ArrayBuffer(5 * 3 * 4 + 1 * 3 * 4 + 3 * 2 * 4)
    new Float32Array(buf, 0, 15).set([0, 0, 0, 1, 0, 0, 0, 1, 0, 5, 5, 5, 6, 6, 6])
    new Uint32Array(buf, 60, 3).set([0, 1, 2])
    new Uint32Array(buf, 72, 6).set([0, 1, 1, 2, 3, 4])
    const m = decodeMesh(buf, meta)
    expect(m.totals).toEqual({ points: 5, triangles: 1, edges: 3 })
    expect([...m.indices]).toEqual([0, 1, 2])
    expect([...m.edges]).toEqual([0, 1, 1, 2, 3, 4])
    expect(m.positions[12]).toBe(6)
  })

  it('refuses a buffer whose size disagrees with the patch table', () => {
    // Silent corruption otherwise: the sections would land at wrong offsets and
    // the geometry would render as noise rather than fail.
    const meta: MeshMeta = {
      patches: [{ name: 'a', nPoints: 3, nTriangles: 1, nEdges: 0, defaultVisible: true }],
    }
    expect(() => decodeMesh(new ArrayBuffer(8), meta)).toThrow(/patch table says/)
  })

  it('locates each patch contiguously', () => {
    const patches = meshMeta.patches
    const [t0s, t0e] = triangleRange(patches, 0)
    const [t1s] = triangleRange(patches, 1)
    expect(t0s).toBe(0)
    expect(t0e).toBe((patches[0] as { nTriangles: number }).nTriangles * 3)
    expect(t1s).toBe(t0e)

    const [p0s, p0e] = pointRange(patches, 0)
    const [p1s] = pointRange(patches, 1)
    expect(p0s).toBe(0)
    expect(p0e).toBe((patches[0] as { nPoints: number }).nPoints)
    expect(p1s).toBe(p0e)
  })

  it('covers every triangle exactly once across all patches', () => {
    const patches = meshMeta.patches
    let cursor = 0
    for (let i = 0; i < patches.length; i++) {
      const [s, e] = triangleRange(patches, i)
      expect(s).toBe(cursor)
      cursor = e
    }
    expect(cursor).toBe(316788 * 3)
  })
})

describe('slice payload', () => {
  it('decodes the real fixture', () => {
    const buf = readFileSync(join(FIX, 'pp_slice_small.bin'))
    const ab = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength)
    const s = decodeSlice(ab)
    expect(s.field).toBe('T')
    expect(s.nPoints).toBe(3)
    expect(s.nTriangles).toBe(1)
    expect(s.scalarRange).toEqual([288.5, 294])
    expect([...s.indices]).toEqual([0, 1, 2])
    expect(s.values && [...s.values]).toEqual([288.5, 291, 294])
  })

  it('handles a header that NEEDS padding', () => {
    // headerLen is the PADDED length, and the padding exists so the Float32Array
    // after it starts on a 4-byte boundary. Reading the unpadded length puts it
    // on an odd offset and the constructor throws — so a fixture whose header
    // happens to be a multiple of four never exercises this.
    const header = { nPoints: 1, nTriangles: 0, scalarRange: [1, 2], field: 'Ux' }
    const { buf, pad } = packSlice(header, [7, 8, 9], [], [42])
    expect(pad, 'this header must need padding for the test to mean anything').toBeGreaterThan(0)
    const s = decodeSlice(buf)
    expect(s.field).toBe('Ux')
    expect([...s.positions]).toEqual([7, 8, 9])
    expect(s.values && [...s.values]).toEqual([42])
  })

  it('returns an empty slice rather than throwing when the plane misses', () => {
    const { buf } = packSlice({ nPoints: 0, nTriangles: 0, scalarRange: [0, 0], field: 'T' }, [], [], [])
    const s = decodeSlice(buf)
    expect(s.nPoints).toBe(0)
    expect(s.values).toBeNull()
    expect(s.positions.length).toBe(0)
  })
})


describe('streamline payload', () => {
  const stats = JSON.parse(
    readFileSync(join(FIX, 'pp_streamlines_full_stats.json'), 'utf8'),
  ) as { lines: number; points: number; bytes: number; scalarRange: [number, number] }

  it('agrees with the real capture on size', () => {
    // The server sent this for 40 seeds at (2.2, 1.5, 3.0) r=1.0 on the
    // 100-step Base_leakage run. Every point is a QUAD, not three arrays.
    expect(stats.lines).toBe(78)
    expect(stats.points).toBe(17180)
    // header (108 + pad) + 17180 quads: the quad layout is the whole point.
    expect(stats.bytes - stats.points * 16).toBeGreaterThan(0)
  })

  it('deinterleaves the xyzs quads', () => {
    const buf = readFileSync(join(FIX, 'pp_streamlines_small.bin'))
    const ab = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength)
    const s = decodeStreamlines(ab)
    expect(s.field).toBe('speed')
    expect(s.lines.length).toBe(3)
    const total = s.lines.reduce((n, l) => n + l.count, 0)
    expect(s.points.length).toBe(total * 3)
    expect(s.scalars.length).toBe(total)
    // Every scalar must be inside the declared range. Reading the data as
    // separate position and scalar blocks instead of quads puts coordinates
    // into the scalar array, which blows past the range immediately.
    for (let i = 0; i < s.scalars.length; i++) {
      expect(s.scalars[i]).toBeGreaterThanOrEqual(s.scalarRange[0] - 1e-4)
      expect(s.scalars[i]).toBeLessThanOrEqual(s.scalarRange[1] + 1e-4)
    }
  })

  it('gives each polyline a contiguous, non-overlapping slice', () => {
    const buf = readFileSync(join(FIX, 'pp_streamlines_small.bin'))
    const ab = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength)
    const s = decodeStreamlines(ab)
    let cursor = 0
    for (const l of s.lines) {
      expect(l.start).toBe(cursor)
      expect(l.count).toBeGreaterThan(0)
      cursor += l.count
    }
    expect(cursor * 3).toBe(s.points.length)
  })

  it('handles the empty result the server sends when nothing seeds', () => {
    const json = new TextEncoder().encode(
      JSON.stringify({ streamlines: [], scalarRange: [0, 0], field: 'speed' }),
    )
    const pad = (4 - (json.length % 4)) % 4
    const buf = new ArrayBuffer(4 + json.length + pad)
    new DataView(buf).setUint32(0, json.length + pad, true)
    new Uint8Array(buf, 4, json.length).set(json)
    const s = decodeStreamlines(buf)
    expect(s.lines).toEqual([])
    expect(s.points.length).toBe(0)
  })
})

describe('field payload', () => {
  /** min, max, nComponents, nPoints, then the values — the server's layout. */
  function fieldBuffer(min: number, max: number, values: number[]): ArrayBuffer {
    const buf = new ArrayBuffer(16 + values.length * 4)
    const view = new DataView(buf)
    view.setFloat32(0, min, true)
    view.setFloat32(4, max, true)
    view.setUint32(8, 1, true)
    view.setUint32(12, values.length, true)
    values.forEach((v, i) => view.setFloat32(16 + i * 4, v, true))
    return buf
  }

  it('SKIPS the 16-byte binary header instead of reading it as data', () => {
    // Reading the body flat returns four extra values and shifts every point's
    // value by four positions. Both the picture and the colour scale come out
    // wrong while still looking like a plausible result — the failure this
    // whole function exists to prevent.
    const f = decodeField(fieldBuffer(290, 300, [290, 295, 300]))
    expect(Array.from(f.values)).toEqual([290, 295, 300])
    expect(f.values.length).toBe(3)
  })

  it('takes the range from the header rather than scanning', () => {
    // The server computed it over the whole field; a client scan of a subset
    // would put the colorbar on a different scale from the solver's own report.
    const f = decodeField(fieldBuffer(288.15, 310.88, [290, 295, 300]))
    expect(f.info.min).toBeCloseTo(288.15, 2)
    expect(f.info.max).toBeCloseTo(310.88, 2)
    expect(f.info.points).toBe(3)
    expect(f.info.components).toBe(1)
  })

  it('reads a vector field as components x points', () => {
    const buf = new ArrayBuffer(16 + 6 * 4)
    const view = new DataView(buf)
    view.setFloat32(0, 0, true)
    view.setFloat32(4, 1, true)
    view.setUint32(8, 3, true)
    view.setUint32(12, 2, true)
    for (let i = 0; i < 6; i++) view.setFloat32(16 + i * 4, i, true)
    expect(decodeField(buf).values.length).toBe(6)
  })

  it('keeps the HTTP header info when the body carries its own', () => {
    const f = decodeField(fieldBuffer(1, 2, [1.5]), { name: 'T' })
    expect(f.info.name).toBe('T')
    expect(f.info.min).toBe(1)
  })

  it('does not mis-slice a body too short to hold a header', () => {
    // An empty or truncated body must come back empty, not throw a RangeError
    // from a negative length.
    expect(decodeField(new ArrayBuffer(0)).values.length).toBe(0)
    expect(decodeField(new ArrayBuffer(8)).values.length).toBe(2)
  })
})
