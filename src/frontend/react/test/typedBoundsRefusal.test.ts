import { beforeEach, describe, expect, it } from 'vitest'
import type { CfdProject, Construction } from '../src/schema/cfdproj'
import { stateV4 } from '../src/legacy/cfdprojModules'
import { createModel } from '../src/legacy/factories'
import * as store from '../src/projectStore'
import { updateRackSize } from '../src/geometry/rackGeometry'

/**
 * A TYPED geometry edit that would leave the project bounds is refused.
 *
 * The bounds are not decoration — `yMin` enabled at 0 is the usual one, and a
 * model below it meshes geometry outside the domain. Every DRAG path already
 * enforced them: the move gizmo reverts to its last valid frame, drag-create
 * clamps or cancels, a duplicate that cannot fit is deleted. The typed paths did
 * not, so the same illegal state was one keystroke away in the box beside the
 * gizmo, and it reached the .cfdproj and the mesh with nothing on screen saying
 * so. The legacy refuses both (`updateModel`, STL_generate.html:14015).
 */

let uid: string

function model(): Construction {
  return store.findConstruction(store.getProject() as CfdProject, uid)!.model
}

function location() {
  return model().geometry!.location as { x: number; y: number; z: number }
}

function size() {
  return model().geometry!.size as { x: number; y: number; z: number }
}

function setBounds(entries: Record<string, { enabled: boolean; value: number }>) {
  for (const [key, entry] of Object.entries(entries)) {
    store.setProjectBound(key, 'value', entry.value)
    store.setProjectBound(key, 'enabled', entry.enabled)
  }
}

beforeEach(() => {
  store.loadProject(
    stateV4().normalizeStateV4(stateV4().getInitialStateV4()) as unknown as CfdProject,
    null,
  )
  const created = createModel(store.getProject() as CfdProject, 'box')
  uid = store.addModel(created.bucket, created.model)
  store.setModelLocation(uid, { x: 1, y: 1, z: 1 })
})

describe('with no bounds declared', () => {
  it('accepts anything, because nothing was asked for', () => {
    expect(store.setModelLocation(uid, { x: -50, y: -50, z: -50 })).toBe(true)
    expect(location().x).toBe(-50)
  })
})

describe('a typed position', () => {
  beforeEach(() => setBounds({ yMin: { enabled: true, value: 0 } }))

  it('is refused when it would put the model below the floor', () => {
    const before = { ...location() }
    expect(store.setModelLocation(uid, { x: 1, y: -0.5, z: 1 })).toBe(false)
    expect(location()).toEqual(before)
  })

  it('leaves NO undo entry behind — nothing happened to undo', () => {
    const depth = store.undoDepth()
    store.setModelLocation(uid, { x: 1, y: -0.5, z: 1 })
    expect(store.undoDepth()).toBe(depth)
  })

  it('still accepts a position inside them', () => {
    expect(store.setModelLocation(uid, { x: 2, y: 0.25, z: 2 })).toBe(true)
    expect(location().y).toBe(0.25)
  })

  it('refuses a single component write too', () => {
    expect(store.setGeometryComponent(uid, 'location', 'y', -3)).toBe(false)
    expect(location().y).toBe(1)
  })
})

describe('a typed size', () => {
  beforeEach(() => {
    setBounds({ yMax: { enabled: true, value: 3 } })
    store.setModelLocation(uid, { x: 1, y: 1, z: 1 })
  })

  it('is refused when growing would push the top past yMax', () => {
    const before = size().y
    // Anchored at y = 1, so anything over 2 m tall crosses 3.
    expect(store.setGeometryComponent(uid, 'size', 'y', 2.5)).toBe(false)
    expect(size().y).toBe(before)
  })

  it('accepts a size that still fits', () => {
    expect(store.setGeometryComponent(uid, 'size', 'y', 1.5)).toBe(true)
    expect(size().y).toBe(1.5)
  })

  it('never refuses SHRINKING', () => {
    store.setGeometryComponent(uid, 'size', 'y', 1.9)
    expect(store.setGeometryComponent(uid, 'size', 'y', 0.2)).toBe(true)
  })
})

describe('a rack', () => {
  let rackUid: string

  beforeEach(() => {
    const created = createModel(store.getProject() as CfdProject, 'rack')
    rackUid = store.addModel(created.bucket, created.model)
    store.setModelLocation(rackUid, { x: 0, y: 1, z: 0 })
    setBounds({ yMax: { enabled: true, value: 3 } })
  })

  function rackSize() {
    return (store.findConstruction(store.getProject() as CfdProject, rackUid)!.model.geometry!
      .size as { y: number }).y
  }

  it('refuses a height that leaves the domain, like the generic box', () => {
    const before = rackSize()
    // The rack panel's own H box goes through `updateRackSize`, not
    // `setGeometryComponent`, because a rack re-derives its slots on every edit.
    expect(updateRackSize(rackUid, 'H', 2.5)).toBe(false)
    expect(rackSize()).toBe(before)
  })

  it('accepts one that fits', () => {
    expect(updateRackSize(rackUid, 'H', 1.5)).toBe(true)
  })
})
