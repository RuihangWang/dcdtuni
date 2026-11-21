/**
 * A refinement level is an integer, and no caller may store otherwise.
 *
 * snappyHexMesh takes integer refinement levels; the runner's Pydantic schema
 * rejects a float outright, so a typed `1.5` does not misbehave subtly — it kills
 * the run at mesh time, long after the person who typed it has moved on.
 *
 * The legacy editor guarded this inside `setMeshRefineLevel`, its single write
 * path, and a backend test asserted the `Math.round(` was still there. React
 * rounded at the two CALL SITES in MeshPanel instead, which held only as long as
 * those were the only callers — and they stopped being the only callers the
 * moment the automation contract could write `meshConfig` directly:
 * `setMeshConfig('refinementLevel', [1.5, 2.7])` stored 1.5 and 2.7.
 *
 * So the rounding moves into the store, where it cannot be forgotten.
 */

import { beforeEach, describe, expect, it } from 'vitest'

import * as store from '../src/projectStore'
import { automation } from '../src/automation/contract'

function meshConfig(): Record<string, unknown> {
  const project = store.getProject() as unknown as {
    runDefaults?: { meshConfig?: Record<string, unknown> }
  }
  return project.runDefaults?.meshConfig ?? {}
}

describe('refinement levels are integers', () => {
  beforeEach(() => {
    automation.newProject()
  })

  it('rounds what setRefinementBound is given', () => {
    store.setRefinementBound('meshConfig', 'refinementLevel', 0, 1.5, [1, 1])
    store.setRefinementBound('meshConfig', 'refinementLevel', 1, 2.7, [1, 1])
    expect(meshConfig().refinementLevel).toEqual([2, 3])
  })

  it('rounds a level written straight through setMeshConfig', () => {
    // The automation contract's raw setter. This is the caller the call-site
    // rounding never covered.
    automation.setMeshConfig('refinementLevel', [1.5, 2.7])
    expect(meshConfig().refinementLevel).toEqual([2, 3])
  })

  it('leaves a genuinely non-integer meshConfig field alone', () => {
    // Only LEVELS are integers. Rounding everything would quietly destroy the
    // base cell size, which is the one number a mesh is most sensitive to.
    automation.setMeshConfig('meshBaseSize', 0.34)
    expect(meshConfig().meshBaseSize).toBe(0.34)
  })
})

describe('a zero-thickness sheet keeps no thickness', () => {
  beforeEach(() => {
    automation.newProject()
  })

  it('refuses a thickness typed onto a baffle, wall or rectangular floor', () => {
    // The inspector shows a sheet only its two in-plane boxes, so a user cannot
    // type this. A SCRIPT can — `setGeometryComponent` is on the contract — and
    // the value persisted into the .cfdproj, describing a solid nothing draws.
    // Same lesson as the refinement levels above: the rule belongs where the
    // value lands, not at the call sites that happen to exist today.
    for (const [create, dead] of [
      [automation.createDefaultBaffleModel, 'z'],
      [automation.createDefaultWallModel, 'z'],
      [automation.createDefaultFloorModel, 'y'],
    ] as const) {
      const uid = create()!
      store.setGeometryComponent(uid, 'size', dead, 0.35)
      const model = store.findConstruction(store.getProject()!, uid)!.model as {
        geometry?: { size?: Record<string, number> }
      }
      expect(model.geometry?.size?.[dead]).toBe(0)
    }
  })

  it('still writes the sheet\'s IN-PLANE dimensions', () => {
    const uid = automation.createDefaultBaffleModel()!
    store.setGeometryComponent(uid, 'size', 'x', 2.4)
    store.setGeometryComponent(uid, 'size', 'y', 1.8)
    const model = store.findConstruction(store.getProject()!, uid)!.model as {
      geometry?: { size?: Record<string, number> }
    }
    expect(model.geometry?.size?.x).toBe(2.4)
    expect(model.geometry?.size?.y).toBe(1.8)
  })
})
