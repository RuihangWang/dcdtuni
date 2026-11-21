import { describe, expect, it } from 'vitest'
import type { CfdProject, Construction } from '../src/schema/cfdproj'
import { stateV4, type V3Seed } from '../src/legacy/cfdprojModules'
import { floorPolygon, polygonArea } from '../src/geometry/floorPolygon'
import { loadProject, getProject } from '../src/projectStore'
import { readProjectFileText } from '../src/io/projectFiles'

const FOOTPRINT: [number, number][] = [[0, 0], [0, 6], [10, 6], [6.6, 1.8]]

function normalizeFloor(floor: unknown): Construction {
  const project = stateV4().getInitialStateV4() as unknown as CfdProject
  project.constructions!.floors = { legacy_floor: floor as Construction }
  const normalized = stateV4().normalizeStateV4(project) as unknown as CfdProject
  return normalized.constructions!.floors!.legacy_floor!
}

describe('legacy polygon floors remain polygonal after loading', () => {
  it('restores an already-v5 file through the real file reader and project-store load path', () => {
    const project = stateV4().newDocumentV4() as unknown as CfdProject
    project.schemaVersion = 5
    project.constructions!.floors = { legacy_floor: { uid: 'legacy_floor', polygon: FOOTPRINT,
      geometry: { size: { x: 10, y: 0, z: 6 } }, meta: { name: 'Room_Floor' },
    } as unknown as Construction }
    const parsed = readProjectFileText(JSON.stringify(project))
    if (!parsed.ok) throw new Error(parsed.error)
    loadProject(parsed.project, '/case/Polygon_room.cfdproj')
    const floor = getProject()!.constructions!.floors!.legacy_floor!
    expect(floorPolygon(floor)).toEqual(FOOTPRINT)
    expect(polygonArea(floorPolygon(floor)!)).toBeCloseTo(40.8, 12)
  })

  it('retains the real 40.8 m² footprint rather than its 60 m² bounding box', () => {
    const raw = { uid: 'legacy_floor', polygon: FOOTPRINT,
      geometry: { size: { x: 10, y: 0, z: 6 } }, meta: { name: 'Room_Floor' } }
    const floor = normalizeFloor(raw)
    expect(floorPolygon(floor)).toEqual(FOOTPRINT)
    expect(polygonArea(floorPolygon(floor)!)).toBeCloseTo(40.8, 12)
    expect(raw).toHaveProperty('polygon', FOOTPRINT)
    expect(raw.geometry).not.toHaveProperty('polygon')
  })

  it('keeps a newer canonical footprint when a stale legacy field is also present', () => {
    const canonical: [number, number][] = [[0, 0], [2, 0], [0, 2]]
    const floor = normalizeFloor({ uid: 'legacy_floor', polygon: FOOTPRINT,
      geometry: { polygon: canonical }, meta: { name: 'Room_Floor' } })
    expect(floorPolygon(floor)).toEqual(canonical)
    expect(polygonArea(floorPolygon(floor)!)).toBe(2)
  })

  it('also preserves floor footprints migrated from v3 construction seeds', () => {
    const floor = stateV4().finalizeNewModelV4({ id: 'legacy_floor', kind: 'floor',
      name: 'Room_Floor', polygon: FOOTPRINT, base: { W: 10, D: 6, H: 0 },
      anchor: { x: 0, y: 0, z: 0 }, cfd: { defaultBC: 'wall' },
    } as unknown as V3Seed)
    expect(floorPolygon(floor)).toEqual(FOOTPRINT)
    expect(polygonArea(floorPolygon(floor)!)).toBeCloseTo(40.8, 12)
  })
})
