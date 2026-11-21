import { beforeEach, describe, expect, it } from 'vitest'
import * as store from '../src/projectStore'
import type { CfdProject } from '../src/schema/cfdproj'
import { stateV4 } from '../src/legacy/cfdprojModules'
import { createAirPipe, createModel } from '../src/legacy/factories'
import {
  airPipeGeometry,
  airPipeWorldPoints,
  deleteAirPipePoint,
  findAirPipeJoinTarget,
  insertAirPipePointAfter,
  joinAirPipes,
  joinAt,
  setAirPipeDiameter,
  setAirPipePointWorld,
  setAirPipeShape,
  setAirPipeWorldPoints,
} from '../src/geometry/airPipe'

function fresh() {
  store.loadProject(stateV4().getInitialStateV4() as unknown as CfdProject, null)
  return store.getProject()!
}

describe('air pipe factory', () => {
  beforeEach(() => {
    fresh()
  })

  it('lands in the pipes bucket with a local polyline and a 150 mm bore', () => {
    const created = createAirPipe(store.getProject()!)
    expect(created.bucket).toBe('pipes')
    expect(created.model.kind).toBe('pipe')
    const g = airPipeGeometry(created.model)
    expect(g.shape).toBe('round')
    expect(g.diameterM).toBeCloseTo(0.15)
    expect(g.waypoints.length).toBe(2)
    expect(g.joins).toEqual([])
    // `kind` is derived from the bucket on save, never written.
    expect(Object.keys(created.model)).not.toContain('kind')
  })

  it('is reachable through createModel, for the toolbar and the contract', () => {
    expect(createModel(store.getProject()!, 'pipe').bucket).toBe('pipes')
  })

  it('reports waypoints in world coordinates relative to the anchor', () => {
    const created = createAirPipe(store.getProject()!, {
      location: { x: 1, y: 2, z: 3 },
      waypoints: [
        { x: 0, y: 0, z: 0 },
        { x: 0, y: 0.5, z: 0 },
      ],
    })
    const pts = airPipeWorldPoints(created.model)
    expect(pts[1]).toEqual({ x: 1, y: 2.5, z: 3 })
  })
})

describe('air pipe edits', () => {
  let uid: string
  beforeEach(() => {
    fresh()
    const created = createAirPipe(store.getProject()!, { location: { x: 1, y: 1, z: 1 } })
    uid = store.addModel(created.bucket, created.model)
  })
  const model = () => store.findConstruction(store.getProject()!, uid)!.model

  it('typed world coordinates are stored local to the anchor', () => {
    setAirPipePointWorld(uid, 1, { x: 1, y: 3, z: 1 })
    expect(airPipeGeometry(model()).waypoints[1]).toEqual({ x: 0, y: 2, z: 0 })
    expect(airPipeWorldPoints(model())[1]).toEqual({ x: 1, y: 3, z: 1 })
  })

  it('inserts on the midpoint and refuses to delete below two points', () => {
    insertAirPipePointAfter(uid, 0)
    expect(airPipeGeometry(model()).waypoints[1]).toEqual({ x: 0, y: 0.5, z: 0 })
    deleteAirPipePoint(uid, 1)
    expect(airPipeGeometry(model()).waypoints.length).toBe(2)
    deleteAirPipePoint(uid, 0)
    expect(airPipeGeometry(model()).waypoints.length).toBe(2)
  })

  it('shape and bore edits are undoable steps', () => {
    setAirPipeShape(uid, 'rect')
    setAirPipeDiameter(uid, 0.2)
    expect(airPipeGeometry(model()).shape).toBe('rect')
    expect(airPipeGeometry(model()).diameterM).toBeCloseTo(0.2)
    store.undo()
    expect(airPipeGeometry(model()).diameterM).toBeCloseTo(0.15)
    store.undo()
    expect(airPipeGeometry(model()).shape).toBe('round')
  })

  it('rejects a bore no reducer could draw', () => {
    setAirPipeDiameter(uid, 0)
    expect(airPipeGeometry(model()).diameterM).toBeCloseTo(0.15)
  })
})

describe('air pipe joins', () => {
  let a: string
  let b: string
  beforeEach(() => {
    fresh()
    const p1 = createAirPipe(store.getProject()!, { location: { x: 0, y: 0, z: 0 } })
    a = store.addModel(p1.bucket, p1.model)
    const p2 = createAirPipe(store.getProject()!, {
      location: { x: 0.05, y: 1.02, z: 0 },
      diameterM: 0.2,
    })
    b = store.addModel(p2.bucket, p2.model)
  })
  const model = (uid: string) => store.findConstruction(store.getProject()!, uid)!.model

  it('finds the other pipe\'s free end within the snap distance, and nothing joined', () => {
    const target = findAirPipeJoinTarget(store.getProject()!, a, { x: 0, y: 1, z: 0 })
    expect(target?.uid).toBe(b)
    expect(target?.end).toBe('start')
    store.edit('join', '', (d) => {
      joinAirPipes(d, a, 'end', b, 'start')
    })
    expect(findAirPipeJoinTarget(store.getProject()!, a, { x: 0, y: 1, z: 0 })).toBeNull()
  })

  it('snaps the moving end onto the partner and records the pairing on both', () => {
    store.edit('join', '', (d) => {
      expect(joinAirPipes(d, a, 'end', b, 'start')).toBe(true)
    })
    expect(airPipeWorldPoints(model(a))[1]).toEqual({ x: 0.05, y: 1.02, z: 0 })
    expect(joinAt(model(a), 'end')).toEqual({ end: 'end', pipeUid: b, pipeEnd: 'start' })
    expect(joinAt(model(b), 'start')).toEqual({ end: 'start', pipeUid: a, pipeEnd: 'end' })
  })

  it('typing a joined end away breaks the join on both sides', () => {
    store.edit('join', '', (d) => {
      joinAirPipes(d, a, 'end', b, 'start')
    })
    setAirPipePointWorld(a, 1, { x: 0.5, y: 1.02, z: 0 })
    expect(joinAt(model(a), 'end')).toBeNull()
    expect(joinAt(model(b), 'start')).toBeNull()
  })

  it('replacing the polyline breaks every join', () => {
    store.edit('join', '', (d) => {
      joinAirPipes(d, a, 'end', b, 'start')
    })
    setAirPipeWorldPoints(a, [
      { x: 0, y: 0, z: 0 },
      { x: 0, y: 0.5, z: 0 },
      { x: 0.3, y: 0.5, z: 0 },
    ])
    expect(airPipeGeometry(model(a)).waypoints.length).toBe(3)
    expect(joinAt(model(b), 'start')).toBeNull()
  })
})
