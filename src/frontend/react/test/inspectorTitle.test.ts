import { describe, expect, it } from 'vitest'
import { inspectorTitle } from '../src/ui/inspectorTitle'
import type { TreeSelection } from '../src/interaction/treeNodes'

/**
 * The header is the only line above the panel that says what the fields belong
 * to. With two states — "Model Settings" and "Project Settings" — a rack, a
 * U-slot, a CDU, a server face and a patch all read the same, and on a CDU the
 * panel is effectively unlabelled.
 */

const node = (type: TreeSelection['type']): TreeSelection => ({ type, uid: 'm_1' })

describe('inspectorTitle', () => {
  it('names the project when nothing is selected', () => {
    expect(inspectorTitle({ node: null, kind: null, count: 0 })).toEqual({
      key: 'inspector.project_settings',
    })
  })

  it('names the KIND of a single selected model', () => {
    expect(inspectorTitle({ node: null, kind: 'wall', count: 1 })).toEqual({
      key: 'inspector.wall_settings',
    })
    expect(inspectorTitle({ node: null, kind: 'baffle', count: 1 })).toEqual({
      key: 'inspector.baffle_settings',
    })
    expect(inspectorTitle({ node: null, kind: 'floor', count: 1 })).toEqual({
      key: 'inspector.floor_settings',
    })
    expect(inspectorTitle({ node: null, kind: 'acu', count: 1 })).toEqual({
      key: 'inspector.acu_settings',
    })
    expect(inspectorTitle({ node: null, kind: 'cdu', count: 1 })).toEqual({
      key: 'liquid.cdu_settings',
    })
  })

  it('falls back to the generic label for a kind with no name of its own', () => {
    expect(inspectorTitle({ node: null, kind: 'rack', count: 1 })).toEqual({
      key: 'inspector.model_settings',
    })
  })

  it('counts a multi-selection instead of naming one of them', () => {
    expect(inspectorTitle({ node: null, kind: 'wall', count: 3 })).toEqual({
      key: 'inspector.multi_select',
      vars: { count: 3 },
    })
  })

  it('names the narrower selection when the tree picked one', () => {
    expect(inspectorTitle({ node: node('rackSlot'), kind: 'rack', count: 1 })).toEqual({
      key: 'inspector.uslot_settings',
    })
    expect(inspectorTitle({ node: node('equipment'), kind: 'rack', count: 1 })).toEqual({
      key: 'inspector.equipment_settings',
    })
    expect(inspectorTitle({ node: node('partition'), kind: 'rack', count: 1 })).toEqual({
      key: 'inspector.partition_settings',
    })
    expect(inspectorTitle({ node: node('wallRegion'), kind: 'floor', count: 1 })).toEqual({
      key: 'inspector.wall_region',
    })
    expect(inspectorTitle({ node: node('face'), kind: 'acu', count: 1 })).toEqual({
      key: 'inspector.face_projection',
    })
    expect(inspectorTitle({ node: node('patch'), kind: 'floor', count: 1 })).toEqual({
      key: 'inspector.patch_editor',
    })
  })

  it('gives the two server-face selections the legacy literal, since neither has a key', () => {
    expect(inspectorTitle({ node: node('serverFace'), kind: 'rack', count: 1 })).toEqual({
      literal: 'Server Face',
    })
    expect(inspectorTitle({ node: node('serverPatch'), kind: 'rack', count: 1 })).toEqual({
      literal: 'Server Face',
    })
  })

  it('names the model when the node IS the model', () => {
    expect(inspectorTitle({ node: node('model'), kind: 'acu', count: 1 })).toEqual({
      key: 'inspector.acu_settings',
    })
  })

  it('keeps the kind title for a planar body whose FACE was clicked', () => {
    expect(inspectorTitle({ node: node('face'), kind: 'wall', count: 1 })).toEqual({
      key: 'inspector.wall_settings',
    })
    expect(inspectorTitle({ node: node('face'), kind: 'floor', count: 1 })).toEqual({
      key: 'inspector.floor_settings',
    })
  })
})
