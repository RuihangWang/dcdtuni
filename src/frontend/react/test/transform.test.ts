import * as THREE from 'three'
import { describe, expect, it } from 'vitest'
import { addModel, applyModelTransform, getProject, loadProject, undo } from '../src/projectStore'
import type { CfdProject } from '../src/schema/cfdproj'
import { stateV4 } from '../src/legacy/cfdprojModules'
import { createModel } from '../src/legacy/factories'

/** A store loaded with an empty project plus one box, and that box's uid. */
function loadWithBox(): string {
  const project = stateV4().normalizeStateV4(stateV4().getInitialStateV4()) as unknown as CfdProject
  loadProject(project, null)
  const { bucket, model } = createModel(getProject() as CfdProject, 'box')
  return addModel(bucket, model)
}

function quaternionOf(uid: string) {
  const p = getProject() as CfdProject
  let found: { x: number; y: number; z: number; w: number } | null = null
  const walk = (o: unknown) => {
    if (!o || typeof o !== 'object') return
    const rec = o as Record<string, unknown>
    if (rec.uid === uid) found = (rec.geometry as Record<string, never>)?.quaternion ?? null
    Object.values(rec).forEach(walk)
  }
  walk(p)
  return found as { x: number; y: number; z: number; w: number } | null
}

function scaleOf(uid: string) {
  const p = getProject() as CfdProject
  let found: { x: number; y: number; z: number } | null = null
  const walk = (o: unknown) => {
    if (!o || typeof o !== 'object') return
    const rec = o as Record<string, unknown>
    if (rec.uid === uid) found = (rec.geometry as Record<string, never>)?.scale ?? null
    Object.values(rec).forEach(walk)
  }
  walk(p)
  return found as { x: number; y: number; z: number } | null
}

describe('toolbar transforms', () => {
  it('rotates 90° about Y to a quaternion of (0, √½, 0, √½)', () => {
    const uid = loadWithBox()
    applyModelTransform(uid, { kind: 'rot', axis: 'Y', deg: 90 })
    const q = quaternionOf(uid)!
    expect(q.x).toBeCloseTo(0, 9)
    expect(q.y).toBeCloseTo(Math.SQRT1_2, 9)
    expect(q.z).toBeCloseTo(0, 9)
    expect(q.w).toBeCloseTo(Math.SQRT1_2, 9)
  })

  it('composes two rotations about the WORLD axis, not the model’s own', () => {
    // Premultiply. Post-multiplying would rotate about the already-turned
    // axes, and a second 90° about Y after a 90° about X would tip the model
    // sideways instead of turning it in the room.
    const uid = loadWithBox()
    applyModelTransform(uid, { kind: 'rot', axis: 'X', deg: 90 })
    applyModelTransform(uid, { kind: 'rot', axis: 'Y', deg: 90 })
    const q = quaternionOf(uid)!
    // Cross-checked against THREE's own premultiply, which is what the legacy
    // editor calls — the point of the test is the ORDER, and hand-written
    // expectations are exactly where an order error hides.
    const expected = new THREE.Quaternion(Math.SQRT1_2, 0, 0, Math.SQRT1_2).premultiply(
      new THREE.Quaternion(0, Math.SQRT1_2, 0, Math.SQRT1_2),
    )
    expect(q.x).toBeCloseTo(expected.x, 9)
    expect(q.y).toBeCloseTo(expected.y, 9)
    expect(q.z).toBeCloseTo(expected.z, 9)
    expect(q.w).toBeCloseTo(expected.w, 9)
    // …and it is NOT the post-multiplied order, which differs in z.
    expect(q.z).toBeCloseTo(-0.5, 9)
  })

  it('mirrors by negating a scale component, leaving the rotation alone', () => {
    const uid = loadWithBox()
    applyModelTransform(uid, { kind: 'rot', axis: 'Y', deg: 90 })
    applyModelTransform(uid, { kind: 'mirror', axis: 'X' })
    expect(scaleOf(uid)).toEqual({ x: -1, y: 1, z: 1 })
    expect(quaternionOf(uid)!.y).toBeCloseTo(Math.SQRT1_2, 9)
  })

  it('mirroring twice returns to the original', () => {
    const uid = loadWithBox()
    applyModelTransform(uid, { kind: 'mirror', axis: 'Z' })
    applyModelTransform(uid, { kind: 'mirror', axis: 'Z' })
    expect(scaleOf(uid)).toEqual({ x: 1, y: 1, z: 1 })
  })

  it('reset clears both the rotation and the mirror', () => {
    const uid = loadWithBox()
    applyModelTransform(uid, { kind: 'rot', axis: 'Z', deg: 45 })
    applyModelTransform(uid, { kind: 'mirror', axis: 'Y' })
    applyModelTransform(uid, { kind: 'reset' })
    expect(quaternionOf(uid)).toEqual({ x: 0, y: 0, z: 0, w: 1 })
    expect(scaleOf(uid)).toEqual({ x: 1, y: 1, z: 1 })
  })

  it('is ONE undo step per button press', () => {
    const uid = loadWithBox()
    applyModelTransform(uid, { kind: 'rot', axis: 'Y', deg: 90 })
    undo()
    expect(quaternionOf(uid)).toEqual({ x: 0, y: 0, z: 0, w: 1 })
  })
})
