import { describe, expect, it } from 'vitest'
import {
  type CfdProject,
  type Construction,
  flowTotals,
  nextSortMode,
  patchPhysicsMap,
  sortModelsForTree,
} from '../src/schema/cfdproj'

function model(name: string, extra: Partial<Construction> = {}): Construction {
  return { uid: `u_${name}`, meta: { name }, ...extra }
}

describe('sortModelsForTree', () => {
  it('orders numerically, so Rack_C2 comes before Rack_C10', () => {
    // This is the reported bug: plain lexicographic order puts C10 first, which
    // is what made a freshly added Rack_C10 look misplaced.
    const models = [model('Rack_C10'), model('Rack_C2'), model('Rack_C1')]
    expect(sortModelsForTree(models, 'name-asc').map((m) => m.meta.name)).toEqual([
      'Rack_C1',
      'Rack_C2',
      'Rack_C10',
    ])
  })

  it('does not mutate the input array', () => {
    // Ordering is a view concern; reordering the caller's array would leak into
    // export order and change the .cfdproj bytes.
    const models = [model('B'), model('A')]
    sortModelsForTree(models, 'name-asc')
    expect(models.map((m) => m.meta.name)).toEqual(['B', 'A'])
  })

  it('returns the same array untouched in manual mode', () => {
    const models = [model('B'), model('A')]
    expect(sortModelsForTree(models, 'manual')).toBe(models)
  })

  it('cycles asc -> desc -> manual -> asc', () => {
    expect(nextSortMode('name-asc')).toBe('name-desc')
    expect(nextSortMode('name-desc')).toBe('manual')
    expect(nextSortMode('manual')).toBe('name-asc')
  })
})

describe('patchPhysicsMap', () => {
  it('reads physics bound to geometry faces', () => {
    const m = model('ACU', {
      geometry: {
        faces: {
          top: { patches: [{ id: 'p_1', physics: { role: 'return', volumeFlowRate: 4 } }] },
          bottom: { patches: [{ id: 'p_2', physics: { role: 'supply', volumeFlowRate: 4 } }] },
        },
      },
    } as Partial<Construction>)
    expect(Object.keys(patchPhysicsMap(m)).sort()).toEqual(['p_1', 'p_2'])
  })

  it('lets a bound patch override an unbound entry with the same id', () => {
    // An unbound entry is a leftover for a patch that no longer exists. If the
    // patch is back, the unbound copy is stale by definition.
    const m = model('ACU', {
      cooling: { unboundPatchPhysics: { p_1: { role: 'supply', volumeFlowRate: 99 } } },
      geometry: {
        faces: { top: { patches: [{ id: 'p_1', physics: { role: 'return', volumeFlowRate: 4 } }] } },
      },
    } as Partial<Construction>)
    expect(patchPhysicsMap(m).p_1).toEqual({ role: 'return', volumeFlowRate: 4 })
  })

  it('returns nothing for a model with no faces', () => {
    expect(patchPhysicsMap(model('Box'))).toEqual({})
  })
})

describe('flowTotals', () => {
  const project = (): CfdProject => ({
    constructions: {
      acus: {
        a1: model('ACU_1', {
          geometry: {
            faces: {
              top: { patches: [{ id: 'p_9', physics: { role: 'return', volumeFlowRate: 4 } }] },
              bottom: { patches: [{ id: 'p_10', physics: { role: 'supply', volumeFlowRate: 4 } }] },
            },
          },
        } as Partial<Construction>),
      },
      // A rack carrying supply physics must NOT count: the monitor is an ACU
      // balance check, and folding server flow into it would mask a real
      // imbalance.
      racks: {
        r1: model('Rack_1', {
          geometry: {
            faces: { front: { patches: [{ id: 'p_1', physics: { role: 'supply', volumeFlowRate: 7 } }] } },
          },
        } as Partial<Construction>),
      },
    },
  })

  it('sums ACU supply and return separately', () => {
    expect(flowTotals(project())).toEqual({ supply: 4, return: 4 })
  })

  it('is zero for a project with no ACUs', () => {
    expect(flowTotals({ constructions: {} })).toEqual({ supply: 0, return: 0 })
  })

  it('is zero for no project at all', () => {
    expect(flowTotals(null)).toEqual({ supply: 0, return: 0 })
  })
})

// Legacy JSON may have array faces; the current typed document stores a keyed face record.
const owner = (geometry: Construction['geometry'], unbound: Record<string, object> = {}): Construction => ({
  uid: 'acu', meta: { name: 'ACU' }, geometry, cooling: { unboundPatchPhysics: unbound },
})
const projectOf = (value: Construction): CfdProject => ({ constructions: { acus: { acu: value } } })

describe('current geometry ACU flow accounting', () => {
  it('counts current patch physics even without supplyFaces or returnFaces mirrors', () => {
    const model = owner({ faces: {
      front: { patches: [{ id: 's', physics: { role: 'supply', volumeFlowRate: 2.5 } }] },
      back: { patches: [{ id: 'r', physics: { role: 'return', volumeFlowRate: 2.5 } }] },
    } })
    expect(flowTotals(projectOf(model))).toEqual({ supply: 2.5, return: 2.5 })
  })

  it('uses current bound physics rather than a same-ID orphan and preserves the generic map contract', () => {
    const model = owner({ faces: { front: { patches: [{ id: 'p', physics: { role: 'supply', volumeFlowRate: 4 } }] } } },
      { p: { role: 'supply', volumeFlowRate: 99 } })
    const before = JSON.stringify(model)
    expect(flowTotals(projectOf(model))).toEqual({ supply: 4, return: 0 })
    expect(patchPhysicsMap(model).p).toEqual({ role: 'supply', volumeFlowRate: 4 })
    expect(JSON.stringify(model)).toBe(before)
  })

  it('retains orphan records for general callers while omitting them from live flow totals', () => {
    const model = owner({ faces: {} }, { old: { role: 'supply', volumeFlowRate: 99 } })
    const before = JSON.stringify(model)
    expect(patchPhysicsMap(model).old).toEqual({ role: 'supply', volumeFlowRate: 99 })
    expect(flowTotals(projectOf(model))).toEqual({ supply: 0, return: 0 })
    expect(JSON.stringify(model)).toBe(before)
  })

  it('does not invent live physics from an orphan when the current same-ID patch has no physics', () => {
    const model = owner({ faces: { front: { patches: [{ id: 'p' }] } } },
      { p: { role: 'supply', volumeFlowRate: 99 } })
    expect(patchPhysicsMap(model).p).toEqual({ role: 'supply', volumeFlowRate: 99 })
    expect(flowTotals(projectOf(model))).toEqual({ supply: 0, return: 0 })
  })

  it('counts supported array face records using their current patch physics', () => {
    const legacyGeometry: unknown = { faces: [{ patches: [{ id: 'r', physics: { role: 'return', volumeFlowRate: 3 } }] }] }
    const model = owner(legacyGeometry as Construction['geometry'])
    expect(flowTotals(projectOf(model))).toEqual({ supply: 0, return: 3 })
  })
})
