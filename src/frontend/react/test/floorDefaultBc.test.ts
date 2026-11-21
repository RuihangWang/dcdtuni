import { beforeEach, describe, expect, it } from 'vitest'
import type { CfdProject, Construction } from '../src/schema/cfdproj'
import { stateV4 } from '../src/legacy/cfdprojModules'
import { createModel } from '../src/legacy/factories'
import * as store from '../src/projectStore'
import {
  DEFAULT_PERFORATION,
  floorDefaultBcPatch,
  isInternalBoundaryType,
  setFloorDefaultBoundaryType,
} from '../src/geometry/floorDefaultBc'

/**
 * The coupling is the whole point. `wallBaffle` and `perforated` are INTERNAL
 * surfaces with air on both sides, so a floor carrying one of them cannot also
 * be the boundary the domain ends at — a state the legacy cannot reach and the
 * runner has no meaning for.
 */

function loadWithFloor(): string {
  const project = stateV4().normalizeStateV4(stateV4().getInitialStateV4()) as unknown as CfdProject
  store.loadProject(project, null)
  const { bucket, model } = createModel(store.getProject() as CfdProject, 'floor')
  return store.addModel(bucket, model)
}

function floor(uid: string): Construction & {
  cooling?: Record<string, unknown>
  isRoomBoundary?: boolean
} {
  return store.findConstruction(store.getProject() as CfdProject, uid)!.model
}

describe('isInternalBoundaryType', () => {
  it('counts the two types that keep mesh on both sides', () => {
    expect(isInternalBoundaryType('wallBaffle')).toBe(true)
    expect(isInternalBoundaryType('perforated')).toBe(true)
    expect(isInternalBoundaryType('wall')).toBe(false)
  })
})

describe('floorDefaultBcPatch', () => {
  it('seeds a perforation block so the hole fields have values to render', () => {
    const { cooling } = floorDefaultBcPatch({}, 'perforated')
    expect(cooling.defaultPerforation).toEqual(DEFAULT_PERFORATION)
  })

  it('leaves a perforation block the user already tuned', () => {
    const existing = { shape: 'rect', holeDiameterMm: 8, openAreaPct: 20 }
    const { cooling } = floorDefaultBcPatch({ defaultPerforation: existing }, 'perforated')
    expect(cooling.defaultPerforation).toBe(existing)
  })

  it('clears the room-boundary claim when the floor becomes internal', () => {
    const patch = floorDefaultBcPatch(
      { roomBoundaryLocation: 'bottom', defaultBoundaryType: 'wall' },
      'wallBaffle',
    )
    expect(patch.cooling.roomBoundaryLocation).toBe('none')
    expect(patch.isRoomBoundary).toBe(false)
  })

  it('leaves the room-boundary fields alone when the floor stays a wall', () => {
    const patch = floorDefaultBcPatch({ roomBoundaryLocation: 'bottom' }, 'wall')
    expect(patch.cooling.roomBoundaryLocation).toBe('bottom')
    expect(patch.isRoomBoundary).toBeUndefined()
  })
})

describe('setFloorDefaultBoundaryType', () => {
  let uid = ''
  beforeEach(() => {
    uid = loadWithFloor()
  })

  it('cannot leave a perforated tile claiming to be the room boundary', () => {
    store.edit('seed', uid, (draft) => {
      const target = store.findConstruction(draft, uid)!.model as unknown as {
        cooling: Record<string, unknown>
        isRoomBoundary: boolean
      }
      target.cooling = { ...(target.cooling ?? {}), roomBoundaryLocation: 'bottom' }
      target.isRoomBoundary = true
    })
    setFloorDefaultBoundaryType(uid, 'perforated')
    expect(floor(uid).cooling?.defaultBoundaryType).toBe('perforated')
    expect(floor(uid).cooling?.roomBoundaryLocation).toBe('none')
    expect(floor(uid).isRoomBoundary).toBe(false)
    expect(floor(uid).cooling?.defaultPerforation).toEqual(DEFAULT_PERFORATION)
  })

  it('costs one undo step, so the switch can be taken back whole', () => {
    const seeded = String(floor(uid).cooling?.defaultBoundaryType ?? 'wall')
    const before = store.undoDepth()
    setFloorDefaultBoundaryType(uid, 'perforated')
    expect(store.undoDepth()).toBe(before + 1)
    store.undo()
    expect(floor(uid).cooling?.defaultBoundaryType).toBe(seeded)
    expect(floor(uid).cooling?.defaultPerforation).toBeUndefined()
  })
})
