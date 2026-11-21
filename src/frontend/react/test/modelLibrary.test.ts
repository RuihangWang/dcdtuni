import { beforeEach, describe, expect, it } from 'vitest'
import type { CfdProject } from '../src/schema/cfdproj'
import {
  addInstanceFromTemplate,
  instanceName,
  libraryTemplates,
  pinModelToLibrary,
  unpinTemplate,
} from '../src/geometry/modelLibrary'
import * as store from '../src/projectStore'

/**
 * A project whose rack carries real configuration — a non-default size, a named
 * server, a cooling block. The whole claim of the feature is that an instance
 * comes back with those, so a bare `{uid, meta}` fixture would pass while the
 * feature did nothing.
 */
function project(): CfdProject {
  return {
    name: 'T',
    models: { geometryModels: { racks: [], acus: [] } },
    constructions: {
      racks: {
        m_1: {
          uid: 'm_1',
          meta: { name: 'Rack_A', enabled: true, display: { opacity: 1 } },
          geometry: {
            location: { x: 3, y: 0, z: 4 },
            size: { x: 0.6, y: 2.1, z: 1.2 },
            faces: { front: { patches: [{ id: 'p_1', name: 'inlet' }] } },
          },
          cooling: { type: 'air', blankingPanelType: 'typicalSmallGap' },
          // A well-formed V4 server record: `loadProject` now normalises every
          // rack once (stacked-servers spec §3.2), and the normalizer re-keys
          // the dict by `srv.uid` — a record without one comes back as
          // `e_sync_1`. Real documents always carry uid + geometry.
          constructions: {
            servers: {
              e_1: {
                uid: 'e_1',
                type: 'fanServer',
                geometry: { slotPosition: 0, slotOccupation: 1 },
                meta: { name: 'srv', enabled: true },
                cooling: { power: 4200 },
              },
            },
          },
          moveTool: { enabled: true, axes: { x: true, y: false, z: true } },
        },
      },
      acus: {
        m_2: {
          uid: 'm_2',
          meta: { name: 'ACU_1', enabled: true },
          geometry: { location: { x: 1, y: 0, z: 1 }, size: { x: 1.2, y: 2, z: 0.6 } },
        },
      },
      boxes: {
        m_3: { uid: 'm_3', meta: { name: 'Box_1', enabled: true } },
      },
    },
  } as unknown as CfdProject
}

beforeEach(() => store.loadProject(project(), '/tmp/T.cfdproj'))

describe('pinModelToLibrary', () => {
  it('stores the template on the geometryModels shelf, not in constructions', () => {
    const id = pinModelToLibrary('m_1', 'Row A rack')
    expect(id).toMatch(/^t_/)
    const saved = store.getProject() as unknown as {
      models: { geometryModels: { racks: Record<string, { label: string }> } }
    }
    expect(Object.keys(saved.models.geometryModels.racks)).toEqual([id])
    expect(saved.models.geometryModels.racks[id!]!.label).toBe('Row A rack')
    // Pinning must not add a model: a template is a recipe, and an extra
    // construction here would export an STL solid nobody asked for.
    expect(Object.keys(store.getProject()?.constructions?.racks ?? {})).toEqual(['m_1'])
  })

  it('replaces the empty ARRAY the schema initialises the shelf with', () => {
    // `emptyModels()` writes `racks: {}` but every saved .cfdproj in this repo
    // round-trips it as `[]`. Indexing a `t_` key onto an array saves a
    // template that `Object.entries` can still see but JSON writes as nothing.
    pinModelToLibrary('m_1', 'Row A rack')
    const shelf = (store.getProject() as unknown as { models: { geometryModels: { racks: unknown } } })
      .models.geometryModels.racks
    expect(Array.isArray(shelf)).toBe(false)
  })

  it('refuses a kind that is not a rack or an ACU', () => {
    expect(pinModelToLibrary('m_3', 'a box')).toBeNull()
    expect(libraryTemplates(store.getProject())).toEqual([])
  })

  it('refuses a blank label rather than saving an unnameable template', () => {
    expect(pinModelToLibrary('m_1', '   ')).toBeNull()
  })

  it('is one undo step', () => {
    pinModelToLibrary('m_1', 'Row A rack')
    store.undo()
    expect(libraryTemplates(store.getProject())).toEqual([])
  })
})

describe('libraryTemplates', () => {
  it('lists racks before ACUs, the order the toolbar draws them', () => {
    pinModelToLibrary('m_2', 'Perimeter CRAC')
    pinModelToLibrary('m_1', 'Row A rack')
    expect(libraryTemplates(store.getProject()).map((t) => [t.kind, t.label])).toEqual([
      ['rack', 'Row A rack'],
      ['acu', 'Perimeter CRAC'],
    ])
  })

  it('is empty for a project that has never been pinned to', () => {
    expect(libraryTemplates(store.getProject())).toEqual([])
    expect(libraryTemplates(null)).toEqual([])
  })
})

describe('addInstanceFromTemplate', () => {
  it('reproduces the pinned configuration, not a default box', () => {
    // The legacy path loses all of this: `createModelFromRaw` reads V3 fields,
    // and `kind` is non-enumerable so JSON drops it and the raw defaults to
    // 'box'. Every assertion here is one the legacy fails.
    const id = pinModelToLibrary('m_1', 'Row A rack')!
    const uid = addInstanceFromTemplate('rack', id)!
    const made = store.getProject()?.constructions?.racks?.[uid]
    expect(made).toBeDefined()
    expect(made!.kind).toBe('rack')
    expect(made!.geometry?.size).toEqual({ x: 0.6, y: 2.1, z: 1.2 })
    expect((made as unknown as { cooling: { blankingPanelType: string } }).cooling.blankingPanelType)
      .toBe('typicalSmallGap')
    expect(
      (made as unknown as { constructions: { servers: Record<string, { cooling: { power: number } }> } })
        .constructions.servers.e_1!.cooling.power,
    ).toBe(4200)
  })

  it('lands at the room origin with a fresh uid and the template link', () => {
    const id = pinModelToLibrary('m_1', 'Row A rack')!
    const uid = addInstanceFromTemplate('rack', id)!
    const made = store.getProject()!.constructions!.racks![uid]!
    expect(uid).toBe('m_4')
    expect(made.geometry?.location).toEqual({ x: 0, y: 0, z: 0 })
    expect((made as unknown as { fromModel: string }).fromModel).toBe(id)
    // The V3 identity fields the shared module stamps on must not survive into
    // the file, or it carries a second, stale copy of the name.
    expect('id' in made).toBe(false)
    expect('name' in made).toBe(false)
    expect('anchor' in made).toBe(false)
  })

  it('gives back the tool blocks the extract strips', () => {
    const id = pinModelToLibrary('m_1', 'Row A rack')!
    const uid = addInstanceFromTemplate('rack', id)!
    const made = store.getProject()!.constructions!.racks![uid] as unknown as {
      moveTool: { enabled: boolean }
      rotateTool: { axes: { yaw: boolean } }
    }
    expect(made.moveTool.enabled).toBe(false)
    expect(made.rotateTool.axes.yaw).toBe(true)
  })

  it('never repeats a model name, because the name IS the exported patch name', () => {
    const id = pinModelToLibrary('m_1', 'Rack_A')!
    const first = addInstanceFromTemplate('rack', id)!
    const second = addInstanceFromTemplate('rack', id)!
    const racks = store.getProject()!.constructions!.racks!
    expect(racks[first]!.meta.name).toBe('Rack_A_2')
    expect(racks[second]!.meta.name).toBe('Rack_A_3')
  })

  it('returns null for a template id that is not on the shelf', () => {
    expect(addInstanceFromTemplate('rack', 't_missing')).toBeNull()
  })

  it('is one undo step', () => {
    const id = pinModelToLibrary('m_1', 'Row A rack')!
    const uid = addInstanceFromTemplate('rack', id)!
    store.undo()
    expect(store.getProject()?.constructions?.racks?.[uid]).toBeUndefined()
    expect(libraryTemplates(store.getProject()).length).toBe(1)
  })
})

describe('unpinTemplate', () => {
  it('drops the template and leaves the instances standing', () => {
    const id = pinModelToLibrary('m_1', 'Row A rack')!
    const uid = addInstanceFromTemplate('rack', id)!
    unpinTemplate('rack', id)
    expect(libraryTemplates(store.getProject())).toEqual([])
    expect(store.getProject()?.constructions?.racks?.[uid]).toBeDefined()
  })
})

describe('instanceName', () => {
  it('takes the label unchanged when it is free', () => {
    expect(instanceName(store.getProject()!, 'Row A')).toBe('Row A')
  })

  it('suffixes past every name already in the project, not just its own bucket', () => {
    expect(instanceName(store.getProject()!, 'Box_1')).toBe('Box_1_2')
  })
})
