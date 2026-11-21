import { describe, expect, it } from 'vitest'
import {
  applyPatchSnapRect,
  applyStickyResize,
  bestEdgeTarget,
  bestPatchSnap,
  clampToProjectBounds,
  dragCreatePlacement,
  dragCreateStarted,
  edgeCenters,
  edgeProjection,
  edgeRangeProjection,
  faceNormal,
  localAxisAlongDir,
  ownEdgeRange,
  faceToLocal,
  isCircleRingGrab,
  localToFace,
  modelFaceCornersLocal,
  movedRect,
  partitionWallCornersLocal,
  projectBoxRange,
  rectCornersLocal,
  resizedCircle,
  resizedRect,
  stickyHolds,
  stickyTarget,
  wallResizeStep,
  withinProjectBounds,
  type FaceBasis,
} from '../src/geometry/faceDragMath'

/** The `front` layout of a 2 x 3 x 0.5 box, from js/geometry_pure.js:544. */
const FRONT: FaceBasis = {
  origin: { x: 2, y: 0, z: 0 },
  right: { x: -1, y: 0, z: 0 },
  up: { x: 0, y: 1, z: 0 },
}

describe('face coordinates', () => {
  it('round-trips a point through the face basis', () => {
    const local = faceToLocal(FRONT, 0.4, 1.2)
    expect(local).toEqual({ x: 1.6, y: 1.2, z: 0 })
    const back = localToFace(local, FRONT)
    expect(back.x).toBeCloseTo(0.4, 12)
    expect(back.y).toBeCloseTo(1.2, 12)
  })

  it('mirrors x on the front face, which is why the basis is not the identity', () => {
    // A point at model-local x=0 is at the FAR end of the front face: its
    // `right` runs backwards so the face reads correctly from outside.
    expect(localToFace({ x: 0, y: 0, z: 0 }, FRONT).x).toBe(2)
  })

  it('derives the outward normal as right x up', () => {
    expect(faceNormal(FRONT)).toEqual({ x: 0, y: 0, z: -1 })
  })
})

describe('handle geometry', () => {
  it('places edge handles at the midpoints of the quad, bottom first', () => {
    const corners = rectCornersLocal(FRONT, { x: 0, y: 0, w: 1, h: 2 })
    expect(corners[0]).toEqual({ x: 2, y: 0, z: 0 })
    expect(corners[2]).toEqual({ x: 1, y: 2, z: 0 })
    const centres = edgeCenters(corners)
    expect(centres.bottom).toEqual({ x: 1.5, y: 0, z: 0 })
    expect(centres.top).toEqual({ x: 1.5, y: 2, z: 0 })
    expect(centres.left).toEqual({ x: 2, y: 1, z: 0 })
    expect(centres.right).toEqual({ x: 1, y: 1, z: 0 })
  })

  it('builds a model face quad along the DIMENSION axes, not the layout', () => {
    // The front face's layout runs `right` along -X; the handles must run
    // along +X so the one labelled `right` is the one that grows W.
    const corners = modelFaceCornersLocal({ x: 2, y: 3, z: 0.5 }, 'front', {
      wType: 'W',
      hType: 'H',
    })
    expect(corners?.[0]).toEqual({ x: 0, y: 0, z: 0 })
    expect(corners?.[1]).toEqual({ x: 2, y: 0, z: 0 })
    expect(corners?.[2]).toEqual({ x: 2, y: 3, z: 0 })
  })

  it('offsets the quad to the far face for right/back/top', () => {
    const top = modelFaceCornersLocal({ x: 2, y: 3, z: 0.5 }, 'top', { wType: 'W', hType: 'D' })
    expect(top?.[0]).toEqual({ x: 0, y: 3, z: 0 })
    expect(top?.[2]).toEqual({ x: 2, y: 3, z: 0.5 })
  })

  it('refuses a collapsed face — a baffle has no depth to put handles on', () => {
    expect(modelFaceCornersLocal({ x: 1, y: 1, z: 0 }, 'left', { wType: 'D', hType: 'H' })).toBeNull()
  })

  it('puts partition-wall handles in local corner space, on the drawn face', () => {
    const front = partitionWallCornersLocal({ x: 0.2, y: 0.5, w: 0.6, h: 1 }, 0.05, 'front')
    expect(front?.[0]).toEqual({ x: 0.2, y: 0.5, z: 0 })
    const back = partitionWallCornersLocal({ x: 0.2, y: 0.5, w: 0.6, h: 1 }, 0.05, 'back')
    expect(back?.[0]).toEqual({ x: 0.2, y: 0.5, z: 0.05 })
  })
})

describe('rectangle drags', () => {
  const start = { x: 0.5, y: 0.5, w: 1, h: 1 }

  it('moves by the pointer travel, keeping the size', () => {
    const moved = movedRect(start, { x: 0.6, y: 0.6 }, { x: 0.9, y: 0.4 })
    expect(moved.x).toBeCloseTo(0.8, 12)
    expect(moved.y).toBeCloseTo(0.3, 12)
    expect(moved.w).toBe(1)
    expect(moved.h).toBe(1)
  })

  it('holds the opposite edge still on every resize', () => {
    expect(resizedRect(start, 'left', { x: 0.2, y: 0 })).toEqual({ x: 0.2, y: 0.5, w: 1.3, h: 1 })
    expect(resizedRect(start, 'right', { x: 2, y: 0 })).toEqual({ x: 0.5, y: 0.5, w: 1.5, h: 1 })
    expect(resizedRect(start, 'bottom', { x: 0, y: 0.25 })).toEqual({
      x: 0.5,
      y: 0.25,
      w: 1,
      h: 1.25,
    })
    expect(resizedRect(start, 'top', { x: 0, y: 2 })).toEqual({ x: 0.5, y: 0.5, w: 1, h: 1.5 })
  })

  it('resizes a circle by distance from its centre', () => {
    expect(resizedCircle({ x: 1, y: 1 }, { x: 1.3, y: 1.4 })).toBeCloseTo(0.5, 12)
  })

  it('reads a press near the ring as a resize and one inside it as a move', () => {
    const circle = { cx: 1, cy: 1, r: 0.5 }
    // Face 1.2 x 2: the band is 6% of 1.2 = 0.072, capped at 0.04, x1.4 = 0.056.
    expect(isCircleRingGrab({ x: 1.53, y: 1 }, circle, 1.2, 2)).toBe(true)
    expect(isCircleRingGrab({ x: 1.2, y: 1 }, circle, 1.2, 2)).toBe(false)
    // A tiny face still gets a 10 mm floor, or the ring is unhittable.
    expect(isCircleRingGrab({ x: 1.013, y: 1 }, { cx: 1, cy: 1, r: 0.02 }, 0.05, 0.05)).toBe(true)
  })
})

describe('model edge resize', () => {
  const common = {
    baseStart: { W: 2, H: 3, D: 0.5 },
    anchorStart: { x: 1, y: 0, z: 4 },
    worldRight: { x: 1, y: 0, z: 0 },
    worldUp: { x: 0, y: 1, z: 0 },
    wType: 'W',
    hType: 'H',
  }

  it('grows away from the anchor on the right edge', () => {
    const out = wallResizeStep({ ...common, edge: 'right', worldDelta: { x: 0.5, y: 0, z: 0 } })
    expect(out.size).toEqual({ x: 2.5, y: 3, z: 0.5 })
    expect(out.location).toEqual({ x: 1, y: 0, z: 4 })
  })

  it('moves the anchor on the left edge so the far edge stays put', () => {
    const out = wallResizeStep({ ...common, edge: 'left', worldDelta: { x: 0.5, y: 0, z: 0 } })
    expect(out.size.x).toBeCloseTo(1.5, 12)
    expect(out.location.x).toBeCloseTo(1.5, 12)
    // far edge: 1 + 2 = 3 before, 1.5 + 1.5 = 3 after
    expect(out.location.x + out.size.x).toBeCloseTo(3, 12)
  })

  it('moves the anchor along the DRAG direction, so a rotated model still works', () => {
    const out = wallResizeStep({
      ...common,
      worldRight: { x: 0, y: 0, z: 1 },
      edge: 'left',
      worldDelta: { x: 0, y: 0, z: 0.5 },
    })
    expect(out.location.x).toBe(1)
    expect(out.location.z).toBeCloseTo(4.5, 12)
  })

  it('never lets a dimension collapse past the minimum', () => {
    const out = wallResizeStep({ ...common, edge: 'right', worldDelta: { x: -10, y: 0, z: 0 } })
    expect(out.size.x).toBe(0.01)
  })

  it('keeps a baffle flat in Z and a floor flat in Y', () => {
    const baffle = wallResizeStep({
      ...common,
      baseStart: { W: 2, H: 3, D: 0.4 },
      edge: 'top',
      worldDelta: { x: 0, y: 1, z: 0 },
      kind: 'baffle',
    })
    expect(baffle.size).toEqual({ x: 2, y: 4, z: 0 })
    const floor = wallResizeStep({ ...common, edge: 'top', worldDelta: { x: 0, y: 1, z: 0 }, kind: 'floor' })
    expect(floor.size.y).toBe(0)
  })
})

describe('sticky edge snapping', () => {
  it('projects the moving edge onto its drag direction', () => {
    const anchor = { x: 1, y: 0, z: 0 }
    const dir = { x: 1, y: 0, z: 0 }
    expect(edgeProjection(anchor, dir, 2, 'right')).toBe(3)
    expect(edgeProjection(anchor, dir, 2, 'left')).toBe(1)
  })

  it('takes the nearest target inside the threshold', () => {
    expect(bestEdgeTarget(3.02, [2.5, 3.05, 3.4], 0.1, null)).toBe(3.05)
    expect(bestEdgeTarget(3.02, [2.5, 3.4], 0.1, null)).toBeNull()
  })

  it('ignores a target the edge already started on, or the drag would be pinned', () => {
    expect(bestEdgeTarget(3.0, [3.0], 0.1, 3.0)).toBeNull()
    expect(bestEdgeTarget(3.0, [3.0], 0.1, 5.0)).toBe(3.0)
  })

  it('holds a target through the frames where the search finds nothing', () => {
    let target = stickyTarget(null, 3.02, 3.05, 0.1)
    expect(target).toBe(3.05)
    // Throttled frame: no search result, edge has barely moved — stays locked.
    target = stickyTarget(target, 3.08, null, 0.1)
    expect(target).toBe(3.05)
    expect(stickyHolds(target, 3.08, 0.1)).toBe(true)
  })

  it('releases once the raw edge travels 2.5 thresholds away', () => {
    expect(stickyTarget(3.05, 3.4, null, 0.1)).toBeNull()
    expect(stickyHolds(3.05, 3.4, 0.1)).toBe(false)
  })

  it('applies a held snap to the size, and to the anchor on a near edge', () => {
    const grow = applyStickyResize({
      size: { x: 2, y: 3, z: 0.5 },
      location: { x: 1, y: 0, z: 0 },
      edge: 'right',
      dimKey: 'W',
      worldRight: { x: 1, y: 0, z: 0 },
      worldUp: { x: 0, y: 1, z: 0 },
      snapDelta: 0.03,
    })
    expect(grow.size.x).toBeCloseTo(2.03, 12)
    expect(grow.location.x).toBe(1)

    const near = applyStickyResize({
      size: { x: 2, y: 3, z: 0.5 },
      location: { x: 1, y: 0, z: 0 },
      edge: 'left',
      dimKey: 'W',
      worldRight: { x: 1, y: 0, z: 0 },
      worldUp: { x: 0, y: 1, z: 0 },
      snapDelta: 0.03,
    })
    expect(near.size.x).toBeCloseTo(1.97, 12)
    expect(near.location.x).toBeCloseTo(1.03, 12)
    expect(near.location.x + near.size.x).toBeCloseTo(3, 12)
  })
})

describe('patch snapping', () => {
  const base = { minR: 1, maxR: 2, minU: 0, maxU: 1, threshold: 0.1 }

  it('snaps either edge of a moving patch to any neighbour edge', () => {
    const snap = bestPatchSnap({ ...base, isResize: false, edge: null, targetsR: [2.04], targetsU: [] })
    expect(snap.dr).toBeCloseTo(0.04, 12)
    expect(snap.du).toBe(0)
  })

  it('snaps only the dragged edge during a resize', () => {
    const right = bestPatchSnap({
      ...base,
      isResize: true,
      edge: 'right',
      targetsR: [1.03, 2.04],
      targetsU: [],
    })
    // The left edge is 0.03 from a target and the right edge 0.04, but a right
    // resize may only move the right edge.
    expect(right.dr).toBeCloseTo(0.04, 12)
  })

  it('leaves the cross axis alone during a horizontal resize', () => {
    const out = bestPatchSnap({ ...base, isResize: true, edge: 'right', targetsR: [], targetsU: [1.02] })
    expect(out.du).toBe(0)
  })

  it('moves a patch by the snap, and resizes only the dragged side', () => {
    const rect = { x: 1, y: 0, w: 1, h: 1 }
    expect(applyPatchSnapRect(rect, false, null, 0.04, -0.02)).toEqual({
      x: 1.04,
      y: -0.02,
      w: 1,
      h: 1,
    })
    const resized = applyPatchSnapRect(rect, true, 'left', 0.04, 0)
    expect(resized.x).toBeCloseTo(1.04, 12)
    expect(resized.w).toBeCloseTo(0.96, 12)
    expect(resized.x + resized.w).toBeCloseTo(2, 12)
  })

  it('projects a box onto an arbitrary direction', () => {
    const [lo, hi] = projectBoxRange({ x: 0, y: 0, z: 0 }, { x: 2, y: 1, z: 3 }, { x: 0, y: 0, z: 1 })
    expect([lo, hi]).toEqual([0, 3])
  })
})

describe('project bounds', () => {
  const bounds = {
    xMin: { enabled: true, value: 0 },
    xMax: { enabled: true, value: 10 },
    yMin: { enabled: true, value: 0 },
    yMax: { enabled: false, value: 3 },
    zMin: { enabled: true, value: 0 },
    zMax: { enabled: true, value: 6 },
  }

  it('accepts a box inside and rejects one that pokes out', () => {
    expect(withinProjectBounds({ x: 1, y: 0, z: 1 }, { x: 2, y: 3, z: 0.5 }, bounds)).toBe(true)
    expect(withinProjectBounds({ x: 9, y: 0, z: 1 }, { x: 2, y: 3, z: 0.5 }, bounds)).toBe(false)
    expect(withinProjectBounds({ x: -0.001, y: 0, z: 1 }, { x: 2, y: 3, z: 0.5 }, bounds)).toBe(false)
  })

  it('pulls an anchor back so the whole box fits', () => {
    expect(clampToProjectBounds({ x: 9.5, y: 0, z: -1 }, { x: 2, y: 3, z: 0.5 }, bounds)).toEqual({
      x: 8,
      y: 0,
      z: 0,
    })
  })

  it('refuses a model that cannot fit between an enabled Min and Max', () => {
    expect(clampToProjectBounds({ x: 0, y: 0, z: 0 }, { x: 12, y: 3, z: 0.5 }, bounds)).toBeNull()
  })

  it('places a drag-created model on the floor, clamped to the bounds', () => {
    expect(dragCreatePlacement({ x: 9.6, y: 1.4, z: 3 }, { x: 1, y: 1, z: 0 }, bounds)).toEqual({
      x: 9,
      y: 0,
      z: 3,
    })
  })

  it('needs 5 px of travel before a button press becomes a drag', () => {
    expect(dragCreateStarted(3, 3)).toBe(false)
    expect(dragCreateStarted(4, 3)).toBe(true)
  })
})

describe('rotated-model edge snap (containment baffle regression)', () => {
  // Shinaide's con_f: local W runs vertically (quaternion maps local x to world
  // +Z), location z=2.8149, W=1.205 -> top at 4.0199, i.e. 0.42 m through a
  // 3.6 m ceiling. The old snap formula added the LOCAL H along the world up
  // direction and resized H, so the vertical edge never snapped and the drag
  // could push W past the ceiling.
  const q = { x: 0, y: -0.7071067811865476, z: 0, w: 0.7071067811865476 }
  const up = { x: 0, y: 0, z: 1 }

  it('maps the world up direction onto the baffle local W axis', () => {
    expect(localAxisAlongDir(q, up)).toEqual({ axis: 'x', sign: 1 })
  })

  it('identity rotation keeps the W/H mapping', () => {
    expect(localAxisAlongDir(null, { x: 1, y: 0, z: 0 })).toEqual({ axis: 'x', sign: 1 })
    expect(localAxisAlongDir({ x: 0, y: 0, z: 0, w: 1 }, { x: 0, y: 1, z: 0 })).toEqual({
      axis: 'y',
      sign: 1,
    })
  })

  it('returns null when no local axis aligns (45 degrees)', () => {
    const q45 = { x: 0, y: Math.sin(Math.PI / 8), z: 0, w: Math.cos(Math.PI / 8) }
    expect(localAxisAlongDir(q45, up)).toBeNull()
  })

  it('ownEdgeRange projects the oriented box, not location+size along dir', () => {
    // the rotated baffle: local W runs along world +Z, so its extent in `up` is
    // z in [2.8149, 4.0199] and its x location never enters
    const [lo, hi] = ownEdgeRange(
      {
        location: { x: 1.4907374382019039, y: 0, z: 2.8148724731755945 },
        size: { x: 1.20499971539252, y: 2.046999931335449, z: 0 },
        quaternion: q,
      },
      up,
    )
    expect(lo).toBeCloseTo(2.8148724731755945, 9)
    expect(hi).toBeCloseTo(4.019872188568114, 9)
    // the old formula would have said location.z + size.y = 4.8619
    expect(edgeRangeProjection([lo, hi], 'top')).toBeCloseTo(4.019872188568114, 9)
  })

  it('ownEdgeRange follows the box it is handed, frame after frame', () => {
    // The spasm this pins down: measuring the LIVE model instead reads
    // geometry that still holds the previous frame's snapped box, so the raw
    // edge feeds on the last output — S(n) = P(n) + T - S(n-1), which
    // alternates instead of converging. A pure function of the step cannot.
    const location = { x: 1.4907374382019039, y: 0, z: 2.8148724731755945 }
    const top = (w: number) =>
      edgeRangeProjection(
        ownEdgeRange({ location, size: { x: w, y: 2.046999931335449, z: 0 }, quaternion: q }, up),
        'top',
      )
    expect(top(1)).toBeCloseTo(location.z + 1, 9)
    expect(top(1.25) - top(1)).toBeCloseTo(0.25, 9)
    // and the near edge does not move while W grows
    expect(
      edgeRangeProjection(
        ownEdgeRange({ location, size: { x: 1.25, y: 2.047, z: 0 }, quaternion: q }, up),
        'bottom',
      ),
    ).toBeCloseTo(location.z, 9)
  })

  it('ownEdgeRange degenerates to the axis-aligned box without a rotation', () => {
    const box = {
      location: { x: 1, y: 0, z: 2 },
      size: { x: 3, y: 2, z: 0.5 },
      quaternion: { x: 0, y: 0, z: 0, w: 1 },
    }
    const dir = { x: 0.6, y: 0.8, z: 0 }
    const max = { x: 4, y: 2, z: 2.5 }
    const aabb = projectBoxRange(box.location, max, dir)
    for (const quat of [box.quaternion, null, undefined]) {
      const range = ownEdgeRange({ ...box, quaternion: quat }, dir)
      expect(range[0]).toBeCloseTo(aabb[0], 12)
      expect(range[1]).toBeCloseTo(aabb[1], 12)
    }
  })

  it('ownEdgeRange carries a negative scale the way getModelOBB does', () => {
    // A mirrored model: the centre moves to the other side of the anchor while
    // the extent stays the same width.
    const location = { x: 0, y: 0, z: 0 }
    const size = { x: 2, y: 1, z: 1 }
    const dir = { x: 1, y: 0, z: 0 }
    expect(ownEdgeRange({ location, size, scale: { x: -1, y: 1, z: 1 } }, dir)).toEqual([-2, 0])
    expect(ownEdgeRange({ location, size, scale: { x: 1, y: 1, z: 1 } }, dir)).toEqual([0, 2])
  })

  it('applyStickyResize applies the world delta to the mapped local axis', () => {
    const out = applyStickyResize({
      size: { x: 1.20499971539252, y: 2.046999931335449, z: 0 },
      location: { x: 1.4907374382019039, y: 0, z: 2.8148724731755945 },
      edge: 'top',
      dimKey: 'H',
      worldRight: { x: 0, y: 1, z: 0 },
      worldUp: up,
      snapDelta: 3.6 - 4.019872188568114,
      kind: 'baffle',
      axis: 'x',
      sign: 1,
    })
    // top lands exactly on the 3.6 m ceiling; H untouched
    expect(out.size.x).toBeCloseTo(3.6 - 2.8148724731755945, 9)
    expect(out.size.y).toBeCloseTo(2.046999931335449, 9)
    expect(out.location.z).toBeCloseTo(2.8148724731755945, 9)
  })
})

describe('sticky snap hysteresis (twitch regression)', () => {
  // Near a rack the target list is dense; re-picking the nearest target every
  // frame flip-flops the lock between neighbours and the resizing edge
  // twitches. The held target must win while it is still within threshold.
  const thr = 0.24

  it('keeps the held target while a nearer competitor is within threshold', () => {
    expect(stickyTarget(3.0, 3.02, 3.01, thr)).toBe(3.0)
  })

  it('takes a new target once the held one leaves the threshold', () => {
    expect(stickyTarget(3.0, 3.3, 3.31, thr)).toBe(3.31)
  })

  it('releases past the release zone', () => {
    expect(stickyTarget(3.0, 3.0 + thr * 2.5 + 0.01, null, thr)).toBeNull()
  })

  it('holds inside the release zone with no competitor', () => {
    expect(stickyTarget(3.0, 3.0 + thr * 2, null, thr)).toBe(3.0)
  })
})
