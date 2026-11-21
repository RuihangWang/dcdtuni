import { describe, expect, it } from 'vitest'
import type { CfdProject } from '../src/schema/cfdproj'
import {
  applyProjectionDefaults,
  autoFillRect,
  baseWHD,
  circleToPoly,
  createPatchRecord,
  customPatchDefaultSize,
  defaultPatchVector,
  dominantFaceFromNormal,
  enforceCustomPatchSafety,
  enforcePatchSafety,
  faceExtent,
  nextPatchSeq,
  partitionWallEditableFaceId,
  PATCH_PALETTE,
  rectToPoly,
  type FaceRecord,
  type PatchRecord,
} from '../src/geometry/patchGeometry'

/**
 * These numbers end up as OpenFOAM boundary faces, so they are checked against
 * the legacy formulas rather than against what the panel happens to draw.
 */

/** An ACU-shaped box: 1.2 wide (x), 2.0 tall (y), 0.6 deep (z). */
const acu = { kind: 'acu', geometry: { size: { x: 1.2, y: 2.0, z: 0.6 } } }
const faceOf = (wType: string, hType: string, patches: PatchRecord[] = []): FaceRecord => ({
  wType,
  hType,
  patches,
})

describe('face coordinates', () => {
  it('maps V4 x/y/z onto the W/H/D names the face records still use', () => {
    expect(baseWHD(acu)).toEqual({ W: 1.2, H: 2.0, D: 0.6 })
  })

  it('gives each face the pair of extents that actually lie in it', () => {
    // Getting these the wrong way round puts a full-width ACU return on its
    // 0.6 m edge instead of its 1.2 m top.
    expect(faceExtent(acu, 'front', faceOf('W', 'H'))).toEqual({ w: 1.2, h: 2.0 })
    expect(faceExtent(acu, 'left', faceOf('D', 'H'))).toEqual({ w: 0.6, h: 2.0 })
    expect(faceExtent(acu, 'top', faceOf('W', 'D'))).toEqual({ w: 1.2, h: 0.6 })
  })

  it('falls back to the face id when the record carries no wType', () => {
    expect(faceExtent(acu, 'top', undefined)).toEqual({ w: 1.2, h: 0.6 })
    expect(faceExtent(acu, 'right', {})).toEqual({ w: 0.6, h: 2.0 })
  })
})

describe('createPatchRecord', () => {
  it('centres a circle on the face at the legacy 0.15 m radius', () => {
    const patch = createPatchRecord({
      model: acu,
      faceId: 'top',
      face: faceOf('W', 'D'),
      type: 'circle',
      seq: 7,
      colorIndex: 0,
    })
    expect(patch).toEqual({
      id: 'p_7',
      type: 'circle',
      name: 'Circle_Patch_7',
      color: '#e06c75',
      r: 0.15,
      cx: 0.6,
      cy: 0.3,
    })
  })

  it('starts a rectangle in the corner at half the face', () => {
    const patch = createPatchRecord({
      model: acu,
      faceId: 'front',
      face: faceOf('W', 'H'),
      type: 'rect',
      seq: 3,
      colorIndex: 1,
    })
    expect(patch).toEqual({
      id: 'p_3',
      type: 'rect',
      name: 'Rect_Patch_3',
      color: '#61afef',
      x: 0,
      y: 0,
      w: 0.6,
      h: 1.0,
    })
  })

  it('writes its keys in the legacy order, which is the saved byte order', () => {
    // JSON.stringify follows insertion order, and the .cfdproj is diffed
    // against the legacy app's output byte for byte.
    const circle = createPatchRecord({
      model: acu, faceId: 'front', face: faceOf('W', 'H'), type: 'circle', seq: 1, colorIndex: 0,
    })
    expect(Object.keys(circle)).toEqual(['id', 'type', 'name', 'color', 'r', 'cx', 'cy'])
    const rect = createPatchRecord({
      model: acu, faceId: 'front', face: faceOf('W', 'H'), type: 'rect', seq: 1, colorIndex: 0,
    })
    expect(Object.keys(rect)).toEqual(['id', 'type', 'name', 'color', 'x', 'y', 'w', 'h'])
  })

  it('cycles the palette by position within the face', () => {
    const colors = [0, 1, 2, 3, 4, 5, 6].map(
      (i) =>
        createPatchRecord({
          model: acu, faceId: 'front', face: faceOf('W', 'H'), type: 'rect', seq: 1, colorIndex: i,
        }).color,
    )
    expect(colors).toEqual([...PATCH_PALETTE, PATCH_PALETTE[0]])
  })
})

describe('projection defaults', () => {
  it('points every face inward, so the beam cuts into the body', () => {
    // Outward would project the cut away from the mesh and remove nothing.
    expect(defaultPatchVector('front')).toEqual({ x: 0, y: 0, z: 1 })
    expect(defaultPatchVector('back')).toEqual({ x: 0, y: 0, z: -1 })
    expect(defaultPatchVector('left')).toEqual({ x: 1, y: 0, z: 0 })
    expect(defaultPatchVector('right')).toEqual({ x: -1, y: 0, z: 0 })
    expect(defaultPatchVector('top')).toEqual({ x: 0, y: -1, z: 0 })
    expect(defaultPatchVector('bottom')).toEqual({ x: 0, y: 1, z: 0 })
  })

  it('treats an unknown face as the front rather than as a zero vector', () => {
    // A zero vector normalises to NaN and the patch would cut nothing.
    expect(defaultPatchVector('nonsense')).toEqual({ x: 0, y: 0, z: 1 })
  })

  it('fills in a missing vector and leaves a set one alone', () => {
    const fresh = { id: 'p_1', type: 'rect', name: 'n', color: '#fff' } as PatchRecord
    applyProjectionDefaults('top', fresh)
    expect(fresh).toMatchObject({ showVector: true, vx: 0, vy: -1, vz: 0 })
    expect(Object.keys(fresh).slice(-4)).toEqual(['showVector', 'vx', 'vy', 'vz'])

    const edited = { ...fresh, showVector: false, vx: 0.5, vy: 0, vz: 0.5 } as PatchRecord
    applyProjectionDefaults('top', edited)
    expect(edited).toMatchObject({ showVector: false, vx: 0.5, vy: 0, vz: 0.5 })
  })

  it('repairs a half-written vector rather than leaving a NaN component', () => {
    const broken = { id: 'p_1', type: 'rect', name: 'n', color: '#fff', vx: 1 } as PatchRecord
    applyProjectionDefaults('left', broken)
    expect(broken).toMatchObject({ vx: 1, vy: 0, vz: 0 })
  })
})

describe('enforcePatchSafety — rectangles', () => {
  const face = faceOf('W', 'H')
  const rect = (over: Partial<PatchRecord>): PatchRecord =>
    ({ id: 'p_1', type: 'rect', name: 'n', color: '#fff', x: 0, y: 0, w: 0.6, h: 1, ...over })

  it('leaves a patch that already fits untouched and reports no clamp', () => {
    const patch = rect({})
    expect(enforcePatchSafety(acu, 'front', face, patch)).toBe(false)
    expect(patch).toMatchObject({ x: 0, y: 0, w: 0.6, h: 1 })
  })

  it('shrinks a patch wider than its face', () => {
    const patch = rect({ w: 5 })
    expect(enforcePatchSafety(acu, 'front', face, patch)).toBe(true)
    expect(patch.w).toBe(1.2)
  })

  it('slides a patch back inside instead of shrinking it', () => {
    // Moving it keeps the opening the size the user asked for; shrinking would
    // silently change the flow area.
    const patch = rect({ x: 1.0, w: 0.6 })
    expect(enforcePatchSafety(acu, 'front', face, patch)).toBe(true)
    expect(patch).toMatchObject({ x: 1.2 - 0.6, w: 0.6 })
  })

  it('never lets a dimension collapse to zero', () => {
    const patch = rect({ w: 0, h: -1 })
    enforcePatchSafety(acu, 'front', face, patch)
    expect(patch.w).toBe(0.01)
    expect(patch.h).toBe(0.01)
  })
})

describe('enforcePatchSafety — circles', () => {
  const face = faceOf('W', 'D')

  it('keeps a 10% margin on every side', () => {
    // A hole touching the edge leaves a knife-edge sliver of wall that
    // snappyHexMesh cannot resolve.
    const patch: PatchRecord = { id: 'p_1', type: 'circle', name: 'n', color: '#fff', r: 0.15, cx: 0, cy: 0.3 }
    expect(enforcePatchSafety(acu, 'top', face, patch)).toBe(true)
    expect(patch.cx).toBeCloseTo(1.2 * 0.1 + 0.15, 12)
  })

  it('caps the radius at the smaller half-extent less its margin', () => {
    const patch: PatchRecord = { id: 'p_1', type: 'circle', name: 'n', color: '#fff', r: 9, cx: 0.6, cy: 0.3 }
    expect(enforcePatchSafety(acu, 'top', face, patch)).toBe(true)
    // min(1.2/2 - 0.12, 0.6/2 - 0.06) = 0.24
    expect(patch.r).toBeCloseTo(0.24, 12)
  })

  it('falls back to a 0.01 m hole on a face too small for any margin', () => {
    const sliver = { kind: 'box', geometry: { size: { x: 0.02, y: 0.02, z: 0.02 } } }
    const patch: PatchRecord = { id: 'p_1', type: 'circle', name: 'n', color: '#fff', r: 1, cx: 0, cy: 0 }
    enforcePatchSafety(sliver, 'front', faceOf('W', 'H'), patch)
    expect(patch.r).toBe(0.01)
  })
})

describe('enforceCustomPatchSafety', () => {
  it('only stops the beam collapsing — there is no face to stay inside', () => {
    const patch: PatchRecord = { id: 'p_1', type: 'rect', name: 'n', color: '#fff', w: 0, h: -2, depth: 0 }
    enforceCustomPatchSafety(patch)
    expect(patch).toMatchObject({ w: 0.01, h: 0.01, depth: 0.01 })
  })

  it('gives a circle a radius even when the field never existed', () => {
    const patch: PatchRecord = { id: 'p_1', type: 'circle', name: 'n', color: '#fff' }
    enforceCustomPatchSafety(patch)
    expect(patch.r).toBe(0.01)
    expect(patch.depth).toBe(0.01)
  })
})

describe('autoFillRect', () => {
  it('reaches both edges exactly, which typing never does', () => {
    const patch: PatchRecord = { id: 'p_1', type: 'rect', name: 'n', color: '#fff', x: 0.3, y: 0.4, w: 0.2, h: 0.2 }
    autoFillRect(acu, 'front', faceOf('W', 'H'), patch, 'H')
    expect(patch).toMatchObject({ x: 0, w: 1.2, y: 0.4, h: 0.2 })
    autoFillRect(acu, 'front', faceOf('W', 'H'), patch, 'V')
    expect(patch).toMatchObject({ x: 0, w: 1.2, y: 0, h: 2.0 })
  })

  it('fills the face extents, not the model box, on a side face', () => {
    const patch: PatchRecord = { id: 'p_1', type: 'rect', name: 'n', color: '#fff', x: 0, y: 0, w: 0.1, h: 0.1 }
    autoFillRect(acu, 'left', faceOf('D', 'H'), patch, 'H')
    expect(patch.w).toBe(0.6)
  })

  it('survives the safety pass it is followed by', () => {
    const face = faceOf('W', 'H')
    const patch: PatchRecord = { id: 'p_1', type: 'rect', name: 'n', color: '#fff', x: 0.5, y: 0, w: 0.2, h: 0.2 }
    autoFillRect(acu, 'front', face, patch, 'H')
    expect(enforcePatchSafety(acu, 'front', face, patch)).toBe(false)
  })
})

describe('cavity polygons', () => {
  it('closes the circle ring, as polygon-clipping requires', () => {
    const poly = circleToPoly(1, 2, 0.5)
    const ring = poly[0]![0]!
    expect(ring).toHaveLength(33)
    expect(ring[0]).toEqual(ring[32])
    expect(ring[0]).toEqual([1.5, 2])
  })

  it('closes the rectangle ring at its start corner', () => {
    const ring = rectToPoly(1, 2, 3, 4)[0]![0]!
    expect(ring).toEqual([
      [1, 2],
      [4, 2],
      [4, 6],
      [1, 6],
      [1, 2],
    ])
  })
})

describe('nextPatchSeq', () => {
  const project = (patches: unknown): CfdProject =>
    ({
      constructions: {
        acus: { m_1: { uid: 'm_1', meta: { name: 'A' }, geometry: { faces: { top: { patches } } } } },
      },
    }) as unknown as CfdProject

  it('continues past the highest id already in the file', () => {
    // A counter starting at zero each page load would hand out an id that
    // collides with one in the project just opened, and two patches would
    // share a boundary condition.
    expect(nextPatchSeq(project([{ id: 'p_3' }, { id: 'p_11' }, { id: 'p_2' }]))).toBe(12)
  })

  it('ignores ids that are not patch ids', () => {
    expect(nextPatchSeq(project([{ id: 'm_99' }, { id: 'e_4' }, { id: 'p_1' }]))).toBe(2)
  })

  it('starts at 1 on an empty project', () => {
    expect(nextPatchSeq(null)).toBe(1)
    expect(nextPatchSeq({ constructions: {} } as unknown as CfdProject)).toBe(1)
  })
})

describe('partition walls and imported meshes', () => {
  it('routes both large faces of a partition wall to the single hole store', () => {
    expect(partitionWallEditableFaceId('front')).toBe('front')
    expect(partitionWallEditableFaceId('back')).toBe('front')
    // A 50 mm edge has nothing to punch a hole through.
    expect(partitionWallEditableFaceId('left')).toBeNull()
    expect(partitionWallEditableFaceId('top')).toBeNull()
  })

  it('picks the face an imported region most nearly faces', () => {
    expect(dominantFaceFromNormal({ x: 0, y: 0, z: 1 })).toBe('top')
    expect(dominantFaceFromNormal({ x: 0, y: 0, z: -1 })).toBe('bottom')
    expect(dominantFaceFromNormal({ x: 0.9, y: 0.1, z: 0.2 })).toBe('right')
    expect(dominantFaceFromNormal({ x: -0.9, y: 0.1, z: 0.2 })).toBe('left')
    expect(dominantFaceFromNormal({ x: 0.1, y: 0.9, z: 0.2 })).toBe('back')
    expect(dominantFaceFromNormal({ x: 0.1, y: -0.9, z: 0.2 })).toBe('front')
  })

  it('sizes a custom patch from its region, so it suits a connector and a hall alike', () => {
    const small = customPatchDefaultSize({ x: 0.04, y: 0.04, z: 0.04 })
    const large = customPatchDefaultSize({ x: 40, y: 8, z: 20 })
    expect(small.radius).toBe(0.04)
    expect(small.depth).toBe(0.08)
    expect(large.radius).toBeCloseTo(40 * 0.12, 12)
    expect(large.width).toBeCloseTo(40 * 0.28, 12)
    expect(large.height).toBeCloseTo(20 * 0.28, 12)
    expect(large.depth).toBeCloseTo(40 * 0.35, 12)
  })
})
