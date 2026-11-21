import * as THREE from 'three'
import { describe, expect, it } from 'vitest'
import {
  AI_BOX_FACES,
  aiBoxFaceCursor,
  moveAiBox,
  resizeAiBox,
  slideAlongNormal,
  type AiBoxBounds,
} from '../src/postprocess/ppDragMath'
import { AI_BOX_COLOR, AI_BOX_DRAG_COLOR, AI_BOX_HOVER_COLOR, buildAiBox, updateAiBoxVisuals } from '../src/postprocess/ppRenderer'

function box(): AiBoxBounds {
  return { min: [1, 2, 3], max: [4, 6, 9] }
}

describe('the AI box face table', () => {
  it('is in the legacy BM_FACE_INFO order', () => {
    // The index IS the identity: the raycast reports which plane was hit and
    // nothing else, so -X..+Z in this exact order is what makes the plane under
    // the cursor the one that moves.
    expect(AI_BOX_FACES.map((f) => f.name)).toEqual(['-X', '+X', '-Y', '+Y', '-Z', '+Z'])
    expect(AI_BOX_FACES.map((f) => f.axis)).toEqual([0, 0, 1, 1, 2, 2])
    expect(AI_BOX_FACES.map((f) => f.isMin)).toEqual([true, false, true, false, true, false])
  })

  it('shows the legacy cursor per axis', () => {
    expect(aiBoxFaceCursor(0)).toBe('ew-resize')
    expect(aiBoxFaceCursor(3)).toBe('ns-resize')
    expect(aiBoxFaceCursor(5)).toBe('row-resize')
  })
})

describe('moving the AI box', () => {
  it('translates both corners and keeps the size', () => {
    const moved = moveAiBox(box(), [0.5, -1, 2])
    expect(moved.min).toEqual([1.5, 1, 5])
    expect(moved.max).toEqual([4.5, 5, 11])
  })

  it('does not mutate the bounds it started from', () => {
    // The drag re-applies the delta from the SAME start on every pointermove.
    // Mutating it would compound the movement and the box would fly off.
    const start = box()
    moveAiBox(start, [1, 1, 1])
    moveAiBox(start, [1, 1, 1])
    expect(start).toEqual({ min: [1, 2, 3], max: [4, 6, 9] })
  })
})

describe('resizing the AI box', () => {
  it('moves only the dragged face, along only its own axis', () => {
    // A sideways component in the delta must not drift the box: that is what
    // the centre sphere is for, and a face that also translates makes the two
    // handles do the same thing badly.
    const resized = resizeAiBox(box(), 1, [0.5, 3, -7])
    expect(resized.max).toEqual([4.5, 6, 9])
    expect(resized.min).toEqual([1, 2, 3])
  })

  it('moves a min face down when pulled outward', () => {
    const resized = resizeAiBox(box(), 4, [0, 0, -1.5])
    expect(resized.min[2]).toBeCloseTo(1.5, 10)
    expect(resized.max[2]).toBe(9)
  })

  it('never lets a face pass its partner', () => {
    // An inverted box is read by the server as an EMPTY region, so an
    // over-pulled face has to stop rather than produce an inference that
    // returns nothing and looks like a model failure.
    const pushed = resizeAiBox(box(), 0, [100, 0, 0])
    expect(pushed.min[0]).toBeCloseTo(3.99, 10)
    expect(pushed.min[0]).toBeLessThan(pushed.max[0] as number)

    const pulled = resizeAiBox(box(), 3, [0, -100, 0])
    expect(pulled.max[1]).toBeCloseTo(2.01, 10)
    expect(pulled.max[1]).toBeGreaterThan(pulled.min[1] as number)
  })
})

describe('sliding a plane along its normal', () => {
  it('takes only the component on the normal', () => {
    const next = slideAlongNormal([0, 1, 0], [0, 1, 0], [5, 0.25, -3])
    expect(next).toEqual([0, 1.25, 0])
  })

  it('normalises the normal, so an unnormalised one does not scale the move', () => {
    // The custom-normal boxes accept any three numbers, and [0,3,0] means the
    // same plane as [0,1,0]. Skipping the normalise would move the plane three
    // times as far for the same drag.
    const next = slideAlongNormal([0, 0, 0], [0, 3, 0], [0, 2, 0])
    expect(next[1]).toBeCloseTo(2, 10)
  })

  it('slides on a diagonal normal by the projected distance', () => {
    const k = Math.SQRT1_2
    const next = slideAlongNormal([0, 0, 0], [1, 1, 0], [1, 1, 0])
    expect(next[0]).toBeCloseTo(k * Math.SQRT2, 10)
    expect(next[1]).toBeCloseTo(k * Math.SQRT2, 10)
    expect(next[2]).toBe(0)
  })

  it('leaves the origin alone when the normal is zero', () => {
    // Three empty custom-normal boxes are a plane with no direction. Dividing
    // by its length would put NaN into the ring's transform, and a NaN position
    // silently removes the object from every raycast afterwards.
    const next = slideAlongNormal([1, 2, 3], [0, 0, 0], [9, 9, 9])
    expect(next).toEqual([1, 2, 3])
  })
})

describe('the AI box visuals', () => {
  it('builds a wireframe, six pick planes and a centre sphere', () => {
    const built = buildAiBox([0, 0, 0], [2, 4, 6])
    const parts = built.object.children.map((c) => c.userData as { ppAiPart?: string; ppAiFace?: number })
    expect(parts.filter((p) => p.ppAiPart === 'wireframe')).toHaveLength(1)
    expect(parts.filter((p) => p.ppAiPart === 'centre')).toHaveLength(1)
    expect(parts.filter((p) => typeof p.ppAiFace === 'number').map((p) => p.ppAiFace)).toEqual([0, 1, 2, 3, 4, 5])
    expect(built.object.userData.ppLayer).toBe('aiBox')
    built.dispose()
  })

  it('scales a UNIT wireframe rather than rebuilding geometry', () => {
    // A face drag changes the size sixty times a second. Rebuilding the buffer
    // each frame would allocate and free geometry for something a transform
    // already says.
    const built = buildAiBox([0, 0, 0], [2, 4, 6])
    const wire = built.object.children.find(
      (c) => (c.userData as { ppAiPart?: string }).ppAiPart === 'wireframe',
    )!
    expect([wire.scale.x, wire.scale.y, wire.scale.z]).toEqual([2, 4, 6])
    expect([wire.position.x, wire.position.y, wire.position.z]).toEqual([1, 2, 3])
    built.dispose()
  })

  it('puts each pick plane on its own face, sized to the other two axes', () => {
    const built = buildAiBox([0, 0, 0], [2, 4, 6])
    const plane = (idx: number) =>
      built.object.children.find((c) => (c.userData as { ppAiFace?: number }).ppAiFace === idx)!
    expect(plane(1).position.x).toBe(2)
    // PlaneGeometry lies in XY, so a +X face takes (sz, sy) — the pair that is
    // NOT its normal. The wrong pair leaves a thin strip of pickable face.
    expect([plane(1).scale.x, plane(1).scale.y]).toEqual([6, 4])
    expect(plane(2).position.y).toBe(0)
    expect([plane(2).scale.x, plane(2).scale.y]).toEqual([2, 6])
    built.dispose()
  })

  it('recolours on hover and on drag, sphere and wireframe separately', () => {
    const built = buildAiBox([0, 0, 0], [1, 1, 1])
    const wire = built.object.children.find(
      (c) => (c.userData as { ppAiPart?: string }).ppAiPart === 'wireframe',
    ) as THREE.LineSegments
    const sphere = built.object.children.find(
      (c) => (c.userData as { ppAiPart?: string }).ppAiPart === 'centre',
    ) as THREE.Mesh

    const wireHex = () => (wire.material as THREE.LineBasicMaterial).color.getHex()
    const sphereHex = () => (sphere.material as THREE.MeshBasicMaterial).color.getHex()

    expect(wireHex()).toBe(AI_BOX_COLOR)
    // Hovering a FACE lights the box but leaves the sphere alone, so the two
    // grabs stay distinguishable while the cursor is between them.
    updateAiBoxVisuals(built.object, [0, 0, 0], [1, 1, 1], { hover: 'face' })
    expect(wireHex()).toBe(AI_BOX_HOVER_COLOR)
    expect(sphereHex()).toBe(AI_BOX_COLOR)

    updateAiBoxVisuals(built.object, [0, 0, 0], [1, 1, 1], { hover: 'centre' })
    expect(sphereHex()).toBe(AI_BOX_HOVER_COLOR)

    updateAiBoxVisuals(built.object, [0, 0, 0], [1, 1, 1], { hover: 'face', dragging: true })
    expect(wireHex()).toBe(AI_BOX_DRAG_COLOR)
    expect(sphereHex()).toBe(AI_BOX_DRAG_COLOR)
    built.dispose()
  })

  it('keeps a zero-thickness box pickable', () => {
    // min === max on an axis is reachable by typing, and a zero scale collapses
    // the pick planes to nothing — the box would be on screen and ungrabbable.
    const built = buildAiBox([0, 0, 0], [2, 0, 6])
    const wire = built.object.children.find(
      (c) => (c.userData as { ppAiPart?: string }).ppAiPart === 'wireframe',
    )!
    expect(wire.scale.y).toBeGreaterThan(0)
    const plane = built.object.children.find((c) => (c.userData as { ppAiFace?: number }).ppAiFace === 0)!
    expect(plane.scale.y).toBeGreaterThan(0)
    built.dispose()
  })
})
