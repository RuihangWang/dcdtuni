import { describe, expect, it } from 'vitest'
import { buildVolume, decodeVolume, percentileRange } from '../src/postprocess/ppVolume'

/** Build a /api/pp/volume payload the way the server writes it. */
function volumeBuffer(
  dims: [number, number, number],
  scalars: number[],
  extra: Record<string, unknown> = {},
): ArrayBuffer {
  const header = JSON.stringify({
    dimensions: dims,
    origin: [0, 0, 0],
    spacing: [1, 1, 1],
    scalarRange: [0, 1],
    ...extra,
  })
  const headerBytes = new TextEncoder().encode(header)
  // The server pads the header to a 4-byte boundary; a Float32Array view on an
  // unaligned offset throws.
  const padded = Math.ceil(headerBytes.length / 4) * 4
  const buf = new ArrayBuffer(4 + padded + scalars.length * 4)
  const view = new DataView(buf)
  view.setUint32(0, padded, true)
  new Uint8Array(buf, 4, headerBytes.length).set(headerBytes)
  scalars.forEach((v, i) => view.setFloat32(4 + padded + i * 4, v, true))
  return buf
}

describe('decodeVolume', () => {
  it('reads dimensions, spacing and the scalars behind the header', () => {
    const v = decodeVolume(volumeBuffer([2, 1, 1], [10, 20]))
    expect(v.dimensions).toEqual([2, 1, 1])
    expect(Array.from(v.scalars)).toEqual([10, 20])
  })

  it('replaces NaN with the sentinel', () => {
    // The shader skips a voxel with `v > sentinel * 0.01`, and NaN fails EVERY
    // comparison — so a NaN would be neither skipped nor blended, and the
    // result is whatever the hardware does with NaN in a mix.
    const v = decodeVolume(volumeBuffer([3, 1, 1], [1, NaN, 3], { sentinel: -9999 }))
    expect(Array.from(v.scalars)).toEqual([1, -9999, 3])
  })

  it('derives a sentinel below the data when the server omits one', () => {
    const v = decodeVolume(volumeBuffer([1, 1, 1], [5], { scalarRange: [4, 9] }))
    expect(v.sentinel).toBe(3)
  })
})

describe('percentileRange', () => {
  it('clips outliers that would flatten the scale', () => {
    // One 87 °C cell in a 21-37 °C room stretches a min/max scale until the
    // real data is a single colour.
    const values = new Float32Array([...Array.from({ length: 99 }, () => 25), 87])
    const [lo, hi] = percentileRange(values, 0.02, 0.98)!
    expect(hi).toBeLessThan(30)
    expect(lo).toBe(25)
  })

  it('ignores sentinel voxels', () => {
    const values = new Float32Array([-9999, -9999, 20, 30])
    const [lo, hi] = percentileRange(values, 0, 1, -9999)!
    expect(lo).toBe(20)
    expect(hi).toBe(30)
  })

  it('ignores NaN', () => {
    const [lo, hi] = percentileRange(new Float32Array([NaN, 5, 15]), 0, 1)!
    expect([lo, hi]).toEqual([5, 15])
  })

  it('returns null when nothing survives, rather than a NaN range', () => {
    expect(percentileRange(new Float32Array([-9999, -9999]), 0, 1, -9999)).toBeNull()
    expect(percentileRange(new Float32Array([]), 0, 1)).toBeNull()
  })

  it('retains the exact constant percentile range', () => {
    // A constant display is valid; shaders explicitly normalize its LUT to zero.
    const [lo, hi] = percentileRange(new Float32Array([7, 7, 7]), 0, 1)!
    expect([lo, hi]).toEqual([7, 7])
  })
})

// Controlled numeric regression: -200 and -100 are legitimate raw pressure,
// not outside-fluid markers. The original threshold deleted these values.
it('preserves negative physical samples while excluding only the actual sentinel', () => {
 expect(percentileRange(new Float32Array([-9999,-200,-100,-50,10]),0,1,-9999)).toEqual([-200,10])
})
it('configures the actual shader for point nodes and exact sentinel masking', () => {
 const payload={dimensions:[16,16,16]as[number,number,number],origin:[2,3,4]as[number,number,number],spacing:[.1,.2,.3]as[number,number,number],scalarRange:[-200,20]as[number,number],sentinel:-9999,scalars:new Float32Array(4096)}
 const volume=buildVolume(payload,'viridis',[-200,20],.25,16)
 const material=volume.object.material as import('three').ShaderMaterial
 expect(material.fragmentShader).toContain('tc * (uVolDims - 1.0)')
 expect(material.fragmentShader).toContain('texture(uValidity, base + offset * step).r > 0.001')
 expect(material.fragmentShader).not.toContain('uSentinel * 0.01')
 expect(material.uniforms.uBoxMin?.value.toArray()).toEqual([2,3,4])
 expect(material.uniforms.uBoxMax?.value.toArray()).toEqual([3.5,6,8.5])
 expect(material.uniforms.uOpacity?.value).toBe(.25)
 volume.dispose()
})
