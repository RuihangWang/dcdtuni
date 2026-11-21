import { describe, expect, it } from 'vitest'
import { faceDisplayName, modelChildren, patchDetail } from '../src/interaction/treeNodes'
import { rackFaceLabel } from '../src/geometry/rackFaceLabels'
import type { Construction } from '../src/schema/cfdproj'

/** A model shaped like the V4 project, without dragging in the factories. */
function model(partial: Record<string, unknown>): Construction {
  return { uid: 'm_1', meta: { name: 'X' }, geometry: {}, ...partial } as unknown as Construction
}

const face = (patches: unknown[] = [], extra: Record<string, unknown> = {}) => ({
  label: 'Front',
  patches,
  ...extra,
})

describe('box-like models', () => {
  it('lists the six faces in the panel order, not the object order', () => {
    // Object key order follows whatever wrote the file. A tree that reorders
    // itself per project makes two cases impossible to compare side by side.
    const m = model({
      kind: 'box',
      geometry: {
        faces: {
          top: face(),
          front: face(),
          bottom: face(),
          back: face(),
          right: face(),
          left: face(),
        },
      },
    })
    expect(modelChildren(m).map((c) => c.key.split(':').pop())).toEqual([
      'front',
      'back',
      'left',
      'right',
      'top',
      'bottom',
    ])
  })

  it('hangs a face’s patches under it and counts them on the badge', () => {
    const m = model({
      kind: 'acu',
      geometry: {
        faces: {
          top: face([{ id: 'p_1', name: 'Return', color: '#0f0' }], { displayName: 'Top' }),
          bottom: face([{ id: 'p_2', name: 'supply', color: '#00f' }]),
        },
      },
    })
    const nodes = modelChildren(m)
    expect(nodes.map((n) => n.badge)).toEqual(['1', '1'])
    expect(nodes[0]?.children?.[0]?.label).toBe('Return')
    expect(nodes[0]?.children?.[0]?.select).toEqual({
      type: 'patch',
      uid: 'm_1',
      faceId: 'top',
      patchId: 'p_1',
    })
  })

  it('prefers a renamed face over its built-in label', () => {
    // The display name is what becomes the OpenFOAM patch name, so the tree has
    // to show the name the export will use.
    expect(faceDisplayName({ label: 'Front', displayName: 'ColdAisle' }, 'front')).toBe('ColdAisle')
    expect(faceDisplayName({ label: 'Front' }, 'front')).toBe('Front')
    expect(faceDisplayName(undefined, 'front')).toBe('front')
  })
})

describe('racks', () => {
  const rack = model({
    kind: 'rack',
    // V4 racks carry an ARRAY here. Read as a map it would produce six nodes
    // labelled "0".."5".
    geometry: { faces: [], slot: 42 },
    constructions: {
      servers: {
        e_2: {
          uid: 'e_2',
          meta: { name: 'fan-server_1' },
          faces: {
            inlet: { patches: [], color: '#d14b4b' },
            outlet: { patches: [{ id: 'p_14', type: 'rect', color: '#00cc66', w: 0.25, h: 1 }] },
          },
        },
      },
      partitions: [
        { index: 0, name: 'partition_1', deleted: true },
        { index: 1, name: 'partition_2' },
      ],
    },
  })

  it('groups servers and partitions, then opens each face into its U', () => {
    const nodes = modelChildren(rack)
    // The two faces come LAST: each holds one row per U, and a 42-row list
    // above the groups would push them off the panel. They are named by the
    // air they pass — the front is the inlet, the back the outlet — never by
    // the raw side key (it read "front" / "back").
    expect(nodes.map((n) => [n.label, n.badge])).toEqual([
      ['Equipments', '1'],
      ['Partitions', '2'],
      [`${rackFaceLabel('front')}`, '42'],
      [`${rackFaceLabel('back')}`, '42'],
    ])
    expect(rackFaceLabel('front')).toMatch(/Inlet/)
    expect(rackFaceLabel('back')).toMatch(/Outlet/)
    // The select payload keeps the DATA key.
    expect(nodes[2]?.select).toEqual({ type: 'face', uid: 'm_1', faceId: 'front' })
  })

  it('gives a server its six faces even when the project stores only two', () => {
    // A face with no patches still needs a row: it is where a boundary
    // condition gets attached.
    const server = modelChildren(rack)[0]?.children?.[0]
    expect(server?.label).toBe('fan-server_1')
    // Through the same shared labels as the rack's sides, so a server's inlet
    // and the rack side it faces read the same word.
    expect(server?.children?.map((c) => c.label)).toEqual(
      ['inlet', 'outlet', 'top', 'bottom', 'left', 'right'].map((f) => `◻ ${rackFaceLabel(f)}`),
    )
    expect(server?.children?.[1]?.label).toMatch(/Outlet/)
  })

  it('badges only the faces that HAVE patches', () => {
    const faces = modelChildren(rack)[0]?.children?.[0]?.children ?? []
    expect(faces[0]?.badge).toBeUndefined()
    expect(faces[1]?.badge).toBe('1 patch')
  })

  it('numbers server patches and shows their size', () => {
    const patch = modelChildren(rack)[0]?.children?.[0]?.children?.[1]?.children?.[0]
    expect(patch?.label).toBe('■ Patch 1')
    expect(patch?.badge).toBe('25×100%')
    expect(patch?.select).toEqual({
      type: 'serverPatch',
      uid: 'm_1',
      equipmentId: 'e_2',
      faceId: 'outlet',
      patchId: 'p_14',
    })
  })

  it('keeps deleted partitions in the list, marked', () => {
    // A deleted partition is a hole the air flows through. Dropping the row
    // would leave no way to put it back.
    const rows = modelChildren(rack)[1]?.children ?? []
    expect(rows.map((r) => r.label)).toEqual(['▫ partition_1 (deleted)', '▫ partition_2'])
  })

  it('describes a circular patch by radius', () => {
    expect(patchDetail({ type: 'circle', r: 0.3 })).toBe('r=30%')
  })
})

describe('walls, baffles and floors', () => {
  it('offers ONLY the primary face — top for a floor, front for a wall', () => {
    // A wall is one sheet. Six faces would invite a boundary condition on its
    // 20 mm edge.
    const wall = model({
      kind: 'wall',
      geometry: { faces: { front: face([{ id: 'p_1', name: 'door' }]), back: face([{ id: 'p_9' }]) } },
    })
    expect(modelChildren(wall).map((c) => c.label)).toEqual(['door'])

    const floor = model({
      kind: 'floor',
      geometry: { faces: { front: face([{ id: 'p_2', name: 'nope' }]), top: face([{ id: 'p_3', name: 'vent' }]) } },
    })
    expect(modelChildren(floor).map((c) => c.label)).toEqual(['vent'])
  })

  it('lists wall regions only once patches have split the sheet', () => {
    const bare = model({ kind: 'wall', geometry: { faces: { front: face([]) } } })
    expect(modelChildren(bare, { wallRegions: ['w1', 'w2'] })).toEqual([])

    const split = model({
      kind: 'wall',
      geometry: { faces: { front: face([{ id: 'p_1', name: 'door' }]) } },
    })
    expect(modelChildren(split, { wallRegions: ['w1', 'w2'] }).map((c) => c.label)).toEqual([
      'door',
      '▫ w1',
      '▫ w2',
    ])
  })
})

describe('imported solids', () => {
  it('lists the STL regions a model brought with it', () => {
    const m = model({
      kind: 'box',
      geometry: { faces: { front: face() } },
      solidRegions: ['shell', 'fan'],
    })
    const last = modelChildren(m).at(-1)
    expect(last?.badge).toBe('2')
    expect(last?.children?.map((c) => c.label)).toEqual(['▫ shell', '▫ fan'])
  })
})
