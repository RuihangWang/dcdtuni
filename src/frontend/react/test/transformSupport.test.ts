import { describe, expect, it } from 'vitest'
import type { Construction } from '../src/schema/cfdproj'
import type { TreeSelection } from '../src/interaction/treeNodes'
import { inspectorPanelFor } from '../src/interaction/inspectorPanel'
import {
  transformScopeForPanel,
  transformSupport,
  transformTarget,
} from '../src/interaction/transformSupport'

/**
 * Which transform controls an object offers, and which panel shows them.
 *
 * One predicate, read by BOTH the inspector (to decide whether the transform
 * block is drawn, and with which rows) and the R / V shortcuts (to decide
 * whether a key does anything). Deriving the rule twice is how a key ends up
 * turning on rings that the panel would never have offered.
 */

const model = (kind: string, extra: Record<string, unknown> = {}) =>
  ({ uid: 'm_1', kind, meta: { name: `A ${kind}` }, geometry: {}, ...extra }) as unknown as Construction

const node = (sel: Partial<TreeSelection> & { type: TreeSelection['type'] }): TreeSelection =>
  ({ uid: 'm_1', ...sel }) as TreeSelection

describe('transformSupport', () => {
  it('gives the box-like kinds both gizmos and both number groups', () => {
    for (const kind of [
      'rack',
      'acu',
      'box',
      'wall',
      'baffle',
      'floor',
      'partitionWall',
      'custom',
      'heat_source',
      'crh',
      'cold_plate',
      'cdu',
    ]) {
      expect(transformSupport(model(kind)), kind).toEqual({
        move: true,
        rotate: true,
        position: true,
        rotation: true,
      })
    }
  })

  it('gives an air pipe both gizmos but no anchor or angle boxes — its shape is its waypoints', () => {
    expect(transformSupport(model('pipe'))).toEqual({
      move: true,
      rotate: true,
      position: false,
      rotation: false,
    })
  })

  it('gives a liquid pipe nothing: its shape is world waypoints and the renderer ignores its quaternion', () => {
    expect(transformSupport(model('pipe_segment'))).toEqual({
      move: false,
      rotate: false,
      position: false,
      rotation: false,
    })
  })

  it('gives a T-junction move but not rotate — its orientation is cooling.quaternion, not the ring gizmo’s', () => {
    expect(transformSupport(model('pipe_junction'))).toEqual({
      move: true,
      rotate: false,
      position: true,
      rotation: false,
    })
  })
})

describe('inspectorPanelFor', () => {
  it('shows the model panel with no node, a model node, or an incomplete node', () => {
    expect(inspectorPanelFor(model('box'), null)).toBe('model')
    expect(inspectorPanelFor(model('box'), node({ type: 'model' }))).toBe('model')
    // A patch node with no patch id falls through to the model, as the
    // inspector's branch chain always has.
    expect(inspectorPanelFor(model('box'), node({ type: 'patch', faceId: 'front' }))).toBe('model')
  })

  it('dispatches the kinds with their own panel', () => {
    expect(inspectorPanelFor(model('cdu'), null)).toBe('cdu')
    expect(inspectorPanelFor(model('pipe'), null)).toBe('airPipe')
    expect(inspectorPanelFor(model('pipe_segment'), null)).toBe('pipeSegment')
    expect(inspectorPanelFor(model('pipe_segment'), node({ type: 'fitting', fittingId: 'v1' }))).toBe(
      'pipeSegment',
    )
  })

  it('opens a planar kind’s face as the model, and any other face as a face', () => {
    for (const kind of ['wall', 'baffle', 'floor', 'partitionWall']) {
      expect(inspectorPanelFor(model(kind), node({ type: 'face', faceId: 'front' })), kind).toBe(
        'planarFace',
      )
    }
    expect(inspectorPanelFor(model('box'), node({ type: 'face', faceId: 'top' }))).toBe('face')
    expect(inspectorPanelFor(model('acu'), node({ type: 'face', faceId: 'front' }))).toBe('face')
  })

  it('tests the imported-mesh branch before the generic patch branch', () => {
    expect(inspectorPanelFor(model('custom'), node({ type: 'region', regionName: 'a' }))).toBe('region')
    expect(
      inspectorPanelFor(model('custom'), node({ type: 'patch', faceId: 'f', patchId: 'p' })),
    ).toBe('customPatch')
    expect(inspectorPanelFor(model('box'), node({ type: 'patch', faceId: 'f', patchId: 'p' }))).toBe(
      'patch',
    )
  })

  it('names every rack sub-selection', () => {
    const rack = model('rack')
    expect(inspectorPanelFor(rack, node({ type: 'rackSlot', index: 3, side: 'front' }))).toBe('rackSlot')
    expect(inspectorPanelFor(rack, node({ type: 'equipment', equipmentId: 'e' }))).toBe('equipment')
    expect(
      inspectorPanelFor(rack, node({ type: 'serverFace', equipmentId: 'e', faceId: 'inlet' })),
    ).toBe('serverFace')
    expect(
      inspectorPanelFor(
        rack,
        node({ type: 'serverPatch', equipmentId: 'e', faceId: 'inlet', patchId: 'p' }),
      ),
    ).toBe('patch')
    expect(inspectorPanelFor(rack, node({ type: 'partition', index: 0 }))).toBe('partition')
    expect(
      inspectorPanelFor(model('wall'), node({ type: 'wallRegion', faceId: 'front', index: 1 })),
    ).toBe('wallRegion')
  })
})

describe('transformScopeForPanel', () => {
  it('gives the whole block to a panel that IS the object', () => {
    for (const panel of ['model', 'planarFace', 'cdu', 'airPipe', 'pipeSegment', 'region'] as const) {
      expect(transformScopeForPanel(panel), panel).toBe('full')
    }
  })

  it('gives the whole block, marked as acting on the parent, to a panel on part of the object’s own body', () => {
    // Same rows as a panel that IS the object: a box body click selects a
    // face, and its numbers must not be one tree click away when a wall's
    // are right there.
    for (const panel of ['face', 'rackSlot', 'wallRegion'] as const) {
      expect(transformScopeForPanel(panel), panel).toBe('part')
    }
  })

  it('gives nothing to a patch, or to a separate thing inside the object', () => {
    for (const panel of ['patch', 'customPatch', 'equipment', 'serverFace', 'partition'] as const) {
      expect(transformScopeForPanel(panel), panel).toBe('none')
    }
  })
})

describe('transformTarget', () => {
  it('combines the panel scope and the kind’s support', () => {
    expect(transformTarget(model('rack'), null)).toEqual({
      scope: 'full',
      support: { move: true, rotate: true, position: true, rotation: true },
    })
    expect(transformTarget(model('box'), node({ type: 'face', faceId: 'top' }))).toEqual({
      scope: 'part',
      support: { move: true, rotate: true, position: true, rotation: true },
    })
  })

  it('is null where the panel shows no transform, or the kind has no gizmo at all', () => {
    expect(transformTarget(model('box'), node({ type: 'patch', faceId: 'f', patchId: 'p' }))).toBe(null)
    expect(transformTarget(model('rack'), node({ type: 'equipment', equipmentId: 'e' }))).toBe(null)
    expect(transformTarget(model('pipe_segment'), null)).toBe(null)
    expect(transformTarget(null, null)).toBe(null)
  })
})
