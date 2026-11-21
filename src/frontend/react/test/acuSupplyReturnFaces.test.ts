/**
 * An ACU's supply and return faces must be registered as the patches are given
 * their roles — not only when a file is migrated.
 *
 * `geometry.supplyFaces` / `geometry.returnFaces` are what the .cfdrun exporter
 * reads to emit an ACU's boundary bindings (`js/cfdrun_export.js:529-627`). The
 * only thing that ever WROTE them was the v3->V4 migrator
 * (`js/cfdproj_migrate.js:396-408`), which runs on load. Nothing maintained them
 * at edit time — not this editor, not the legacy one.
 *
 * So an ACU built live and exported straight away carried NO supply and NO
 * return: a data centre with no cooling, in a manifest that validates and a UI
 * that looks right. The old paper pipeline never hit it only because it saved
 * the project and loaded it back, which ran the migrator by accident.
 *
 * Measured before the fix: patch physics `{role:'supply',velocity:2,
 * temperature:15,volumeFlowRate:4}` — identical to a known-good case — and
 * `boundaryBindings: {}` in the exported manifest.
 */

import { beforeEach, describe, expect, it } from 'vitest'

import * as store from '../src/projectStore'
import { automation } from '../src/automation/contract'

interface AcuGeometry {
  supplyFaces?: { faceId: string; patchId: string }[]
  returnFaces?: { faceId: string; patchId: string }[]
  faces?: Record<string, { patches?: { id: string }[] }>
}

function acuGeometry(uid: string): AcuGeometry {
  const project = store.getProject()!
  return store.findConstruction(project, uid)!.model.geometry as unknown as AcuGeometry
}

describe('ACU supply/return face registration', () => {
  let uid: string

  beforeEach(() => {
    uid = automation.createDefaultAcuModel()!
    expect(uid).toBeTruthy()
  })

  it('registers a face when its patch is given the supply role', () => {
    const patchId = automation.addPatch(uid, 'front', 'rect')
    expect(patchId).toBeTruthy()
    automation.updateAcuPatchPhysics(uid, patchId!, 'role', 'supply')

    expect(acuGeometry(uid).supplyFaces).toEqual([{ faceId: 'front', patchId }])
    expect(acuGeometry(uid).returnFaces).toEqual([])
  })

  it('registers supply and return independently', () => {
    const supply = automation.addPatch(uid, 'front', 'rect')!
    const ret = automation.addPatch(uid, 'top', 'rect')!
    automation.updateAcuPatchPhysics(uid, supply, 'role', 'supply')
    automation.updateAcuPatchPhysics(uid, ret, 'role', 'return')

    expect(acuGeometry(uid).supplyFaces).toEqual([{ faceId: 'front', patchId: supply }])
    expect(acuGeometry(uid).returnFaces).toEqual([{ faceId: 'top', patchId: ret }])
  })

  it('removes the registration when the role goes back to wall', () => {
    const patchId = automation.addPatch(uid, 'front', 'rect')!
    automation.updateAcuPatchPhysics(uid, patchId, 'role', 'supply')
    expect(acuGeometry(uid).supplyFaces).toHaveLength(1)

    automation.updateAcuPatchPhysics(uid, patchId, 'role', 'wall')
    // A stale entry is worse than none: the exporter would emit a supply
    // boundary for a patch the user has just told it is a wall.
    expect(acuGeometry(uid).supplyFaces).toEqual([])
  })

  it('does not touch a non-ACU model', () => {
    const wall = automation.createDefaultWallModel()!
    const patchId = automation.addPatch(wall, 'front', 'rect')
    if (!patchId) return // some kinds refuse patches on that face; nothing to check
    automation.updateAcuPatchPhysics(wall, patchId, 'role', 'supply')
    const geometry = acuGeometry(wall)
    expect(geometry.supplyFaces).toBeUndefined()
  })
})
