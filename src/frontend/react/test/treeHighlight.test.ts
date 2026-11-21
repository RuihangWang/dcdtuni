import { describe, expect, it } from 'vitest'
import { isTreeNodeHighlighted } from '../src/interaction/treeHighlight'
import type { TreeChild, TreeSelection } from '../src/interaction/treeNodes'

/**
 * Which tree sub-row is lit for the current narrow selection.
 *
 * The table is the legacy tree builder's, row type by row type
 * (STL_generate.html `getTreeHtmlForModel`: isFaceTreeHighlighted,
 * isCustomRegionTreeHighlighted and the inline `selected` expressions of the
 * rack, server, partition, wall-region and solid-region rows). React drew every
 * sub-row plain, so a face, a server or a server face picked in the viewport
 * was never lit in the tree.
 */

const U = 'm_1'
const node = (s: Omit<TreeSelection, 'uid'> & { uid?: string }): TreeSelection =>
  ({ uid: U, ...s }) as TreeSelection
const lit = (row: TreeSelection, current: TreeSelection | null, children?: TreeChild[]) =>
  isTreeNodeHighlighted(row, current, children)

describe('isTreeNodeHighlighted', () => {
  it('lights nothing without a row target, without a current node, or on another model', () => {
    const row = node({ type: 'face', faceId: 'top' })
    expect(isTreeNodeHighlighted(undefined, node({ type: 'face', faceId: 'top' }))).toBe(false)
    expect(lit(row, null)).toBe(false)
    expect(lit(row, node({ type: 'face', faceId: 'top', uid: 'm_2' }))).toBe(false)
  })

  describe('a face row', () => {
    const top = node({ type: 'face', faceId: 'top' })
    it('is lit by that face, a patch on it and a wall region of it', () => {
      expect(lit(top, node({ type: 'face', faceId: 'top' }))).toBe(true)
      expect(lit(top, node({ type: 'patch', faceId: 'top', patchId: 'p_1' }))).toBe(true)
      expect(lit(top, node({ type: 'wallRegion', faceId: 'top', index: 0 }))).toBe(true)
    })
    it('is not lit by another face, a model selection or a fitting', () => {
      expect(lit(top, node({ type: 'face', faceId: 'right' }))).toBe(false)
      expect(lit(top, node({ type: 'patch', faceId: 'front', patchId: 'p_1' }))).toBe(false)
      expect(lit(top, node({ type: 'model' }))).toBe(false)
      expect(lit(top, node({ type: 'fitting', fittingId: 'v_1' }))).toBe(false)
    })
    it('on a rack: lit by a slot on that side, and by a server on that side or on no side', () => {
      const front = node({ type: 'face', faceId: 'front' })
      const back = node({ type: 'face', faceId: 'back' })
      const slot = node({ type: 'rackSlot', side: 'front', index: 3 })
      expect(lit(front, slot)).toBe(true)
      expect(lit(back, slot)).toBe(false)
      const sidedServer = node({ type: 'equipment', equipmentId: 'e_1', side: 'back' })
      expect(lit(front, sidedServer)).toBe(false)
      expect(lit(back, sidedServer)).toBe(true)
      // No side: both rack faces, as the legacy's rackEquipment without side.
      const server = node({ type: 'equipment', equipmentId: 'e_1' })
      expect(lit(front, server)).toBe(true)
      expect(lit(back, server)).toBe(true)
      expect(lit(top, server)).toBe(false)
    })
    it('on a rack: NOT lit by a server face or a server patch', () => {
      const front = node({ type: 'face', faceId: 'front' })
      expect(lit(front, node({ type: 'serverFace', equipmentId: 'e_1', faceId: 'inlet', side: 'front' }))).toBe(false)
      expect(lit(front, node({ type: 'serverPatch', equipmentId: 'e_1', faceId: 'inlet', patchId: 'p_1' }))).toBe(false)
    })
  })

  it('a patch row is lit only by that patch', () => {
    const row = node({ type: 'patch', faceId: 'top', patchId: 'p_1' })
    expect(lit(row, node({ type: 'patch', faceId: 'top', patchId: 'p_1' }))).toBe(true)
    expect(lit(row, node({ type: 'patch', faceId: 'top', patchId: 'p_2' }))).toBe(false)
    expect(lit(row, node({ type: 'face', faceId: 'top' }))).toBe(false)
  })

  it('a wall-region row is lit only by that region of that face', () => {
    const row = node({ type: 'wallRegion', faceId: 'front', index: 1 })
    expect(lit(row, node({ type: 'wallRegion', faceId: 'front', index: 1 }))).toBe(true)
    expect(lit(row, node({ type: 'wallRegion', faceId: 'front', index: 0 }))).toBe(false)
    expect(lit(row, node({ type: 'wallRegion', faceId: 'top', index: 1 }))).toBe(false)
    expect(lit(row, node({ type: 'patch', faceId: 'front', patchId: 'p_1' }))).toBe(false)
  })

  describe('a multi-U server row under a rack face (equipment WITH side)', () => {
    const row = node({ type: 'equipment', equipmentId: 'e_1', side: 'front' })
    const slots: TreeChild[] = [12, 11, 10].map((index) => ({
      key: `rack-slot:${U}:front:${index}`,
      label: `U${index + 1}`,
      select: node({ type: 'rackSlot', side: 'front', index }),
    }))
    it('is lit by that server, whichever side it was picked from', () => {
      expect(lit(row, node({ type: 'equipment', equipmentId: 'e_1' }), slots)).toBe(true)
      expect(lit(row, node({ type: 'equipment', equipmentId: 'e_1', side: 'back' }), slots)).toBe(true)
      expect(lit(row, node({ type: 'equipment', equipmentId: 'e_2' }), slots)).toBe(false)
    })
    it('is lit by a slot selection on the same side that touches its U range', () => {
      expect(lit(row, node({ type: 'rackSlot', side: 'front', index: 11 }), slots)).toBe(true)
      expect(lit(row, node({ type: 'rackSlot', side: 'front', index: 9, indices: [8, 9, 10] }), slots)).toBe(true)
      expect(lit(row, node({ type: 'rackSlot', side: 'front', index: 4, indices: [4, 5] }), slots)).toBe(false)
      expect(lit(row, node({ type: 'rackSlot', side: 'back', index: 11 }), slots)).toBe(false)
    })
    it('is not lit by one of the server’s faces', () => {
      expect(lit(row, node({ type: 'serverFace', equipmentId: 'e_1', faceId: 'inlet' }), slots)).toBe(false)
    })
  })

  describe('a server row under Equipments (equipment WITHOUT side)', () => {
    const row = node({ type: 'equipment', equipmentId: 'e_1' })
    it('is lit by the server, any of its faces and any of its patches', () => {
      expect(lit(row, node({ type: 'equipment', equipmentId: 'e_1' }))).toBe(true)
      expect(lit(row, node({ type: 'serverFace', equipmentId: 'e_1', faceId: 'outlet' }))).toBe(true)
      expect(lit(row, node({ type: 'serverPatch', equipmentId: 'e_1', faceId: 'inlet', patchId: 'p_1' }))).toBe(true)
    })
    it('is not lit by another server or by a slot', () => {
      expect(lit(row, node({ type: 'serverFace', equipmentId: 'e_2', faceId: 'outlet' }))).toBe(false)
      expect(lit(row, node({ type: 'rackSlot', side: 'front', index: 10 }))).toBe(false)
    })
  })

  it('a server-face row is lit by that face of that server only — not by a patch on it', () => {
    const row = node({ type: 'serverFace', equipmentId: 'e_1', faceId: 'outlet' })
    expect(lit(row, node({ type: 'serverFace', equipmentId: 'e_1', faceId: 'outlet' }))).toBe(true)
    expect(lit(row, node({ type: 'serverFace', equipmentId: 'e_1', faceId: 'inlet' }))).toBe(false)
    expect(lit(row, node({ type: 'serverFace', equipmentId: 'e_2', faceId: 'outlet' }))).toBe(false)
    // The legacy builder lights the server row and the patch row for a server
    // patch, and leaves the face row between them plain.
    expect(lit(row, node({ type: 'serverPatch', equipmentId: 'e_1', faceId: 'outlet', patchId: 'p_1' }))).toBe(false)
  })

  it('a server-patch row is lit by that patch of that server only', () => {
    const row = node({ type: 'serverPatch', equipmentId: 'e_1', faceId: 'inlet', patchId: 'p_1' })
    expect(lit(row, node({ type: 'serverPatch', equipmentId: 'e_1', faceId: 'inlet', patchId: 'p_1' }))).toBe(true)
    expect(lit(row, node({ type: 'serverPatch', equipmentId: 'e_1', faceId: 'inlet', patchId: 'p_2' }))).toBe(false)
    expect(lit(row, node({ type: 'serverPatch', equipmentId: 'e_2', faceId: 'inlet', patchId: 'p_1' }))).toBe(false)
    expect(lit(row, node({ type: 'serverFace', equipmentId: 'e_1', faceId: 'inlet' }))).toBe(false)
  })

  it('a U-slot row is lit when its index is in the slot selection on the same side', () => {
    const row = node({ type: 'rackSlot', side: 'front', index: 5 })
    expect(lit(row, node({ type: 'rackSlot', side: 'front', index: 5 }))).toBe(true)
    expect(lit(row, node({ type: 'rackSlot', side: 'front', index: 4, indices: [4, 5, 6] }))).toBe(true)
    expect(lit(row, node({ type: 'rackSlot', side: 'front', index: 4, indices: [4] }))).toBe(false)
    expect(lit(row, node({ type: 'rackSlot', side: 'back', index: 5 }))).toBe(false)
    expect(lit(row, node({ type: 'equipment', equipmentId: 'e_1', side: 'front' }))).toBe(false)
  })

  it('a partition row is lit by that partition only', () => {
    const row = node({ type: 'partition', index: 2 })
    expect(lit(row, node({ type: 'partition', index: 2 }))).toBe(true)
    expect(lit(row, node({ type: 'partition', index: 3 }))).toBe(false)
  })

  describe('a region row', () => {
    const patchChild = (patchId: string): TreeChild => ({
      key: `patch:${U}:front:${patchId}`,
      label: patchId,
      select: node({ type: 'patch', faceId: 'front', patchId, regionName: 'Body' }),
    })
    it('custom region: lit by that region, or by a patch listed under it', () => {
      const row = node({ type: 'region', regionName: 'Body' })
      const kids = [patchChild('p_1')]
      expect(lit(row, node({ type: 'region', regionName: 'Body' }), kids)).toBe(true)
      expect(lit(row, node({ type: 'region', regionName: 'Lid' }), kids)).toBe(false)
      expect(lit(row, node({ type: 'patch', faceId: 'front', patchId: 'p_1' }), kids)).toBe(true)
      expect(lit(row, node({ type: 'patch', faceId: 'front', patchId: 'p_9' }), kids)).toBe(false)
    })
    it('the whole-model region is its own key, distinct from any named one', () => {
      const whole = node({ type: 'region' })
      expect(lit(whole, node({ type: 'region' }))).toBe(true)
      expect(lit(whole, node({ type: 'region', regionName: 'Body' }))).toBe(false)
      expect(lit(node({ type: 'region', regionName: 'Body' }), node({ type: 'region' }))).toBe(false)
    })
    it('an original solid region (no children) is lit by that region name', () => {
      const row = node({ type: 'region', regionName: 'solid_a' })
      expect(lit(row, node({ type: 'region', regionName: 'solid_a' }))).toBe(true)
      expect(lit(row, node({ type: 'patch', faceId: 'front', patchId: 'p_1' }))).toBe(false)
    })
  })

  it('model and fitting rows are never lit (the legacy builder lights neither)', () => {
    expect(lit(node({ type: 'model' }), node({ type: 'model' }))).toBe(false)
    expect(lit(node({ type: 'fitting', fittingId: 'v_1' }), node({ type: 'fitting', fittingId: 'v_1' }))).toBe(false)
  })
})
