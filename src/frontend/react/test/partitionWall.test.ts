import { beforeEach, describe, expect, it } from 'vitest'
import type { CfdProject, Construction } from '../src/schema/cfdproj'
import { stateV4 } from '../src/legacy/cfdprojModules'
import '../src/legacy/geometryBuilder'
import { createModel } from '../src/legacy/factories'
import { addPatch } from '../src/geometry/patchActions'
import {
  clampLeakageRate,
  partitionPatches,
  setPartitionPatchLeakage,
  updatePartitionWallSize,
} from '../src/geometry/partitionWall'
import { addModel, getProject, loadProject, undo } from '../src/projectStore'
import * as store from '../src/projectStore'

function loadWithPartitionWall(): string {
  const project = stateV4().normalizeStateV4(stateV4().getInitialStateV4()) as unknown as CfdProject
  loadProject(project, null)
  const { bucket, model } = createModel(getProject() as CfdProject, 'partitionWall')
  return addModel(bucket, model)
}

function wall(uid: string): Construction {
  return store.findConstruction(getProject() as CfdProject, uid)!.model
}

function sizeOf(uid: string): Record<string, number> {
  return wall(uid).geometry!.size as unknown as Record<string, number>
}

describe('clampLeakageRate', () => {
  it('holds the percentage inside 0 – 100', () => {
    expect(clampLeakageRate(150)).toBe(100)
    expect(clampLeakageRate(-5)).toBe(0)
    expect(clampLeakageRate(37.5)).toBe(37.5)
  })

  it('reads anything unparseable as fully closed rather than NaN', () => {
    expect(clampLeakageRate('nonsense')).toBe(0)
    expect(clampLeakageRate(undefined)).toBe(0)
  })
})

describe('updatePartitionWallSize', () => {
  let uid = ''
  beforeEach(() => {
    uid = loadWithPartitionWall()
  })

  it('writes W, H and D onto x, y and z', () => {
    expect(updatePartitionWallSize(uid, 'W', 4)).toEqual({ ok: true })
    expect(updatePartitionWallSize(uid, 'H', 2.5)).toEqual({ ok: true })
    expect(updatePartitionWallSize(uid, 'D', 0.12)).toEqual({ ok: true })
    const size = sizeOf(uid)
    expect(size.x).toBeCloseTo(4, 10)
    expect(size.y).toBeCloseTo(2.5, 10)
    expect(size.z).toBeCloseTo(0.12, 10)
  })

  it('clamps the thickness up: a zero-thickness partition wall has no body to bore', () => {
    updatePartitionWallSize(uid, 'D', 0)
    expect(sizeOf(uid).z).toBeGreaterThan(0)
  })

  it('re-fits a hole that the new width would leave hanging off the edge', () => {
    const created = addPatch(uid, 'front', 'rect')
    expect(created.ok).toBe(true)
    updatePartitionWallSize(uid, 'W', 0.2)
    const faces = wall(uid).geometry!.faces as Record<string, { patches?: Record<string, number>[] }>
    const patch = faces.front!.patches![0]!
    expect(patch.x! + patch.w!).toBeLessThanOrEqual(sizeOf(uid).x! + 1e-9)
  })

  it('is one undo step', () => {
    updatePartitionWallSize(uid, 'W', 4)
    undo()
    expect(sizeOf(uid).x).toBeCloseTo(1.0, 10)
  })

  it('ignores a non-finite value', () => {
    expect(updatePartitionWallSize(uid, 'W', Number.NaN).ok).toBe(false)
    expect(sizeOf(uid).x).toBeCloseTo(1.0, 10)
  })
})

describe('partition wall leakage', () => {
  let uid = ''
  let patchId = ''
  beforeEach(() => {
    uid = loadWithPartitionWall()
    const created = addPatch(uid, 'front', 'circle')
    patchId = created.patchId ?? ''
  })

  it('lists each hole, defaulting to a fully open 100%', () => {
    const rows = partitionPatches(wall(uid))
    expect(rows.length).toBe(1)
    expect(rows[0]!.id).toBe(patchId)
    expect(rows[0]!.leakageRate).toBe(100)
  })

  it('writes the rate onto the patch physics where the exporter reads it', () => {
    setPartitionPatchLeakage(uid, patchId, 25)
    const faces = wall(uid).geometry!.faces as Record<
      string,
      { patches?: { id: string; physics?: { leakageRate?: number } }[] }
    >
    const patch = faces.front!.patches!.find((p) => p.id === patchId)!
    expect(patch.physics!.leakageRate).toBe(25)
    expect(partitionPatches(wall(uid))[0]!.leakageRate).toBe(25)
  })

  it('clamps what it stores, not only what the box accepts', () => {
    setPartitionPatchLeakage(uid, patchId, 400)
    expect(partitionPatches(wall(uid))[0]!.leakageRate).toBe(100)
  })

  it('reads a rate parked in unboundPatchPhysics for a patch id it does not hold', () => {
    setPartitionPatchLeakage(uid, 'p_not_here', 40)
    const unbound = (wall(uid) as unknown as {
      cooling?: { unboundPatchPhysics?: Record<string, { leakageRate?: number }> }
    }).cooling?.unboundPatchPhysics
    expect(unbound!.p_not_here!.leakageRate).toBe(40)
    // The listing only shows real holes; the parked entry is for a patch that
    // does not exist on this wall.
    expect(partitionPatches(wall(uid)).map((r) => r.id)).toEqual([patchId])
  })

  it('is one undo step', () => {
    setPartitionPatchLeakage(uid, patchId, 12)
    undo()
    expect(partitionPatches(wall(uid))[0]!.leakageRate).toBe(100)
  })

  it('is empty for anything that is not a partition wall', () => {
    expect(partitionPatches(null)).toEqual([])
  })
})
