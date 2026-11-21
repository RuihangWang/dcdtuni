import { beforeEach, describe, expect, it } from 'vitest'
import type { CfdProject, Construction } from '../src/schema/cfdproj'
import { stateV4 } from '../src/legacy/cfdprojModules'
// Imported for the side effect: it installs `window.geometryPure`, so these
// tests exercise the SHARED eligibility rules the exporter uses rather than
// this module's fallback.
import '../src/legacy/geometryBuilder'
import { createModel } from '../src/legacy/factories'
import { addModel, getProject, loadProject, undo } from '../src/projectStore'
import * as store from '../src/projectStore'
import {
  isPrimaryFaceReversed,
  orientationEligible,
  primaryOrientationFaceId,
  togglePrimaryFaceSolidNormal,
} from '../src/geometry/surfaceOrientation'

function loadWith(kind: string): string {
  const project = stateV4().normalizeStateV4(stateV4().getInitialStateV4()) as unknown as CfdProject
  loadProject(project, null)
  const { bucket, model } = createModel(getProject() as CfdProject, kind)
  return addModel(bucket, model)
}

function modelOf(uid: string): Construction {
  return store.findConstruction(getProject() as CfdProject, uid)!.model
}

describe('primaryOrientationFaceId', () => {
  it('is a wall front and a floor top', () => {
    expect(primaryOrientationFaceId(modelOf(loadWith('wall')))).toBe('front')
    expect(primaryOrientationFaceId(modelOf(loadWith('floor')))).toBe('top')
  })

  it('is nothing for a kind with no inside and outside', () => {
    expect(primaryOrientationFaceId(modelOf(loadWith('box')))).toBeNull()
    expect(primaryOrientationFaceId(null)).toBeNull()
  })
})

describe('orientationEligible', () => {
  it('accepts a wall', () => {
    expect(orientationEligible(modelOf(loadWith('wall')))).toBe(true)
  })

  it('rejects a baffle and a box, which are wetted on both sides', () => {
    expect(orientationEligible(modelOf(loadWith('baffle')))).toBe(false)
    expect(orientationEligible(modelOf(loadWith('box')))).toBe(false)
  })

  it('rejects a floor once it is an internal wallBaffle', () => {
    const uid = loadWith('floor')
    store.edit('seed', uid, (draft) => {
      const model = store.findConstruction(draft, uid)!.model as unknown as {
        cooling?: Record<string, unknown>
      }
      model.cooling = { ...(model.cooling ?? {}), defaultBoundaryType: 'wallBaffle' }
    })
    expect(orientationEligible(modelOf(uid))).toBe(false)
  })
})

describe('togglePrimaryFaceSolidNormal', () => {
  let uid = ''
  beforeEach(() => {
    uid = loadWith('wall')
  })

  it('starts un-reversed and flips on', () => {
    expect(isPrimaryFaceReversed(modelOf(uid))).toBe(false)
    togglePrimaryFaceSolidNormal(uid)
    expect(isPrimaryFaceReversed(modelOf(uid))).toBe(true)
  })

  it('flips back', () => {
    togglePrimaryFaceSolidNormal(uid)
    togglePrimaryFaceSolidNormal(uid)
    expect(isPrimaryFaceReversed(modelOf(uid))).toBe(false)
  })

  it('writes the flag on the primary face only', () => {
    togglePrimaryFaceSolidNormal(uid)
    const faces = modelOf(uid).geometry!.faces as Record<string, Record<string, unknown>>
    expect(faces.front!.reverseSolidNormal).toBe(true)
    for (const [id, face] of Object.entries(faces)) {
      if (id !== 'front') expect(face.reverseSolidNormal).not.toBe(true)
    }
  })

  it('is one undo step', () => {
    togglePrimaryFaceSolidNormal(uid)
    undo()
    expect(isPrimaryFaceReversed(modelOf(uid))).toBe(false)
  })

  it('does nothing on an ineligible kind', () => {
    const baffle = loadWith('baffle')
    togglePrimaryFaceSolidNormal(baffle)
    expect(isPrimaryFaceReversed(modelOf(baffle))).toBe(false)
  })
})

/**
 * The fallback exists so the panel renders before `geometry_pure.js` loads. A
 * fallback that answered differently would offer the flip on the wrong models,
 * so it is pinned against the shared module rather than trusted.
 */
describe('fallback without geometry_pure.js', () => {
  it('agrees with the shared module on every kind', () => {
    const kinds = ['wall', 'floor', 'baffle', 'box', 'acu']
    const models = kinds.map((kind) => modelOf(loadWith(kind)))
    const shared = models.map((m) => [orientationEligible(m), primaryOrientationFaceId(m)])
    const w = window as unknown as { geometryPure?: unknown }
    const saved = w.geometryPure
    delete w.geometryPure
    try {
      expect(models.map((m) => [orientationEligible(m), primaryOrientationFaceId(m)])).toEqual(
        shared,
      )
    } finally {
      w.geometryPure = saved
    }
  })
})
