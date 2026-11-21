import { beforeEach, describe, expect, it } from 'vitest'
import type { CfdProject, Construction } from '../src/schema/cfdproj'
import {
  floorPolygon,
  polygonArea,
  polygonBBox,
  rescalePolygon,
  updateFloorPolygonBBox,
  type PolygonPoint,
} from '../src/geometry/floorPolygon'
import { stateV4 } from '../src/legacy/cfdprojModules'
import { createModel } from '../src/legacy/factories'
import { addModel, getProject, loadProject, undo } from '../src/projectStore'
import * as store from '../src/projectStore'

/** An L-shape: not convex, and not centred on the origin. */
const L_SHAPE: PolygonPoint[] = [
  [2, 1],
  [8, 1],
  [8, 4],
  [5, 4],
  [5, 7],
  [2, 7],
]

function loadWithPolygonFloor(polygon: PolygonPoint[] = L_SHAPE): string {
  const project = stateV4().normalizeStateV4(stateV4().getInitialStateV4()) as unknown as CfdProject
  loadProject(project, null)
  const { bucket, model } = createModel(getProject() as CfdProject, 'floor')
  const uid = addModel(bucket, model)
  store.edit('seedPolygon', uid, (draft) => {
    const geometry = store.findConstruction(draft, uid)!.model.geometry as {
      polygon?: unknown
      size?: Record<string, number>
    }
    geometry.polygon = polygon.map((p) => [...p] as PolygonPoint)
    const box = polygonBBox(polygon)
    geometry.size = { ...(geometry.size as Record<string, number>), x: box.width, z: box.depth }
  })
  return uid
}

function floor(uid: string): Construction {
  return store.findConstruction(getProject() as CfdProject, uid)!.model
}

describe('polygonBBox', () => {
  it('is the extent of the vertices, not of the first and third', () => {
    expect(polygonBBox(L_SHAPE)).toEqual({
      minX: 2,
      maxX: 8,
      minY: 1,
      maxY: 7,
      width: 6,
      depth: 6,
    })
  })
})

describe('polygonArea', () => {
  it('measures a rectangle', () => {
    expect(
      polygonArea([
        [0, 0],
        [4, 0],
        [4, 3],
        [0, 3],
      ]),
    ).toBeCloseTo(12, 10)
  })

  it('measures the L-shape as the sum of its two rectangles', () => {
    // 6 x 3 foot plus 3 x 3 leg.
    expect(polygonArea(L_SHAPE)).toBeCloseTo(27, 10)
  })

  it('is winding-independent', () => {
    expect(polygonArea([...L_SHAPE].reverse())).toBeCloseTo(polygonArea(L_SHAPE), 10)
  })

  it('is zero for a degenerate outline', () => {
    expect(polygonArea([[0, 0]] as PolygonPoint[])).toBe(0)
  })
})

describe('rescalePolygon', () => {
  it('stretches to the requested width and leaves the depth alone', () => {
    const scaled = rescalePolygon(L_SHAPE, 'W', 12)!
    const box = polygonBBox(scaled)
    expect(box.width).toBeCloseTo(12, 10)
    expect(box.depth).toBeCloseTo(6, 10)
  })

  it('anchors at the minimum corner, so the floor grows away from its origin', () => {
    const scaled = rescalePolygon(L_SHAPE, 'W', 12)!
    expect(polygonBBox(scaled).minX).toBeCloseTo(2, 10)
  })

  it('scales the interior vertices proportionally, not just the extremes', () => {
    // The 5 in the L-notch sits halfway across a 6 m span; at 12 m it is 2 + 6.
    const scaled = rescalePolygon(L_SHAPE, 'W', 12)!
    expect(scaled[3]![0]).toBeCloseTo(8, 10)
    expect(scaled[3]![1]).toBeCloseTo(4, 10)
  })

  it('scales the area by the linear factor, since only one axis moved', () => {
    const scaled = rescalePolygon(L_SHAPE, 'D', 3)!
    expect(polygonArea(scaled)).toBeCloseTo(polygonArea(L_SHAPE) * 0.5, 10)
  })

  it('refuses a non-positive target rather than collapsing the floor', () => {
    expect(rescalePolygon(L_SHAPE, 'W', 0)).toBeNull()
    expect(rescalePolygon(L_SHAPE, 'W', -3)).toBeNull()
    expect(rescalePolygon(L_SHAPE, 'W', Number.NaN)).toBeNull()
  })

  it('refuses an outline with no extent on the axis being scaled', () => {
    const line: PolygonPoint[] = [
      [1, 0],
      [1, 2],
      [1, 4],
    ]
    expect(rescalePolygon(line, 'W', 5)).toBeNull()
  })
})

describe('updateFloorPolygonBBox', () => {
  let uid = ''
  beforeEach(() => {
    uid = loadWithPolygonFloor()
  })

  it('moves the vertices, not only the reported size', () => {
    updateFloorPolygonBBox(uid, 'W', 12)
    const model = floor(uid)
    expect(polygonBBox(floorPolygon(model)!).width).toBeCloseTo(12, 10)
    expect((model.geometry!.size as unknown as Record<string, number>).x).toBeCloseTo(12, 10)
  })

  it('keeps the box and the outline in agreement on both axes', () => {
    updateFloorPolygonBBox(uid, 'D', 9)
    const model = floor(uid)
    const box = polygonBBox(floorPolygon(model)!)
    const size = model.geometry!.size as unknown as Record<string, number>
    expect(size.x).toBeCloseTo(box.width, 10)
    expect(size.z).toBeCloseTo(box.depth, 10)
    expect(box.depth).toBeCloseTo(9, 10)
  })

  it('is one undo step', () => {
    updateFloorPolygonBBox(uid, 'W', 12)
    undo()
    expect(polygonBBox(floorPolygon(floor(uid))!).width).toBeCloseTo(6, 10)
  })

  it('does nothing to a rectangular floor, which has no polygon to rescale', () => {
    const project = stateV4().normalizeStateV4(
      stateV4().getInitialStateV4(),
    ) as unknown as CfdProject
    loadProject(project, null)
    const created = createModel(getProject() as CfdProject, 'floor')
    const plain = addModel(created.bucket, created.model)
    const before = { ...(floor(plain).geometry!.size as unknown as Record<string, number>) }
    updateFloorPolygonBBox(plain, 'W', 99)
    expect(floor(plain).geometry!.size).toEqual(before)
  })
})
