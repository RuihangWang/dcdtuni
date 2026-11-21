import { describe, expect, it } from 'vitest'
import {
  highlightStyle,
  isMeshHighlighted,
  type HighlightSelection,
} from '../src/interaction/selectionHighlight'

/**
 * Which meshes light up for a given selection.
 *
 * A plain click on a rack's side selects that FACE, and the inspector says so —
 * but the viewport lit the whole cabinet, because the highlight effect only
 * looked at which MODEL was selected. The legacy decides per mesh, from the
 * selection's type (`_updateSelectionHighlight`, STL_generate.html:6510-6570):
 * a face selection lights the face, a patch selection the patch, a server the
 * server, and only a model selection the model.
 */

const RACK = 'm_7'

function sel(node: HighlightSelection['node'], uid = RACK, uids?: string[]): HighlightSelection {
  return { uid, node, uids: new Set(uids ?? (uid ? [uid] : [])) }
}

const noSpan = () => null

describe('isMeshHighlighted', () => {
  it('a model selection lights every mesh of that model, and nothing of another', () => {
    const s = sel(null)
    expect(isMeshHighlighted({ modelId: RACK, faceId: 'right', isBaseWall: true }, s, noSpan)).toBe(true)
    expect(isMeshHighlighted({ modelId: RACK, isRackEquipment: true, equipmentId: 'e_1' }, s, noSpan)).toBe(true)
    expect(isMeshHighlighted({ modelId: 'm_18', faceId: 'right' }, s, noSpan)).toBe(false)
  })

  it('a multi-selection lights every selected model', () => {
    const s = sel({ type: 'model', uid: RACK }, RACK, [RACK, 'm_18'])
    expect(isMeshHighlighted({ modelId: 'm_18', faceId: 'top' }, s, noSpan)).toBe(true)
    expect(isMeshHighlighted({ modelId: 'm_3', faceId: 'top' }, s, noSpan)).toBe(false)
  })

  it('a FACE selection lights that face only — not the cabinet', () => {
    const s = sel({ type: 'face', uid: RACK, faceId: 'right' })
    expect(isMeshHighlighted({ modelId: RACK, faceId: 'right', isBaseWall: true }, s, noSpan)).toBe(true)
    expect(isMeshHighlighted({ modelId: RACK, faceId: 'left', isBaseWall: true }, s, noSpan)).toBe(false)
    // A server face that happens to share the id is the server's, not the rack's.
    expect(
      isMeshHighlighted({ modelId: RACK, faceId: 'right', isRackEquipment: true, equipmentId: 'e_1' }, s, noSpan),
    ).toBe(false)
  })

  it('a PATCH selection lights the patch mesh that carries its id', () => {
    const s = sel({ type: 'patch', uid: RACK, faceId: 'top', patchId: 'p_2' })
    expect(isMeshHighlighted({ modelId: RACK, faceId: 'top', refIds: ['p_2'] }, s, noSpan)).toBe(true)
    expect(isMeshHighlighted({ modelId: RACK, faceId: 'top', refIds: ['p_9'] }, s, noSpan)).toBe(false)
    expect(isMeshHighlighted({ modelId: RACK, faceId: 'top', isBaseWall: true }, s, noSpan)).toBe(false)
  })

  it('a wall region lights that piece of the face', () => {
    const s = sel({ type: 'wallRegion', uid: RACK, faceId: 'top', index: 1 })
    expect(isMeshHighlighted({ modelId: RACK, faceId: 'top', wallIndex: 1 }, s, noSpan)).toBe(true)
    expect(isMeshHighlighted({ modelId: RACK, faceId: 'top', wallIndex: 0 }, s, noSpan)).toBe(false)
  })

  it('a region lights the imported solid with that name', () => {
    const s = sel({ type: 'region', uid: RACK, regionName: 'inlet_1' })
    expect(isMeshHighlighted({ modelId: RACK, isImportedRegion: true, regionName: 'inlet_1' }, s, noSpan)).toBe(true)
    expect(isMeshHighlighted({ modelId: RACK, isImportedRegion: true, regionName: 'other' }, s, noSpan)).toBe(false)
  })

  it('a SERVER selection lights the server — every one of its faces — and not the rack', () => {
    const s = sel({ type: 'equipment', uid: RACK, equipmentId: 'e_1' })
    expect(isMeshHighlighted({ modelId: RACK, isRackEquipment: true, equipmentId: 'e_1', faceId: 'inlet' }, s, noSpan)).toBe(true)
    expect(isMeshHighlighted({ modelId: RACK, isRackEquipment: true, equipmentId: 'e_1', faceId: 'top' }, s, noSpan)).toBe(true)
    expect(isMeshHighlighted({ modelId: RACK, isRackEquipment: true, equipmentId: 'e_2', faceId: 'top' }, s, noSpan)).toBe(false)
    expect(isMeshHighlighted({ modelId: RACK, faceId: 'right', isBaseWall: true }, s, noSpan)).toBe(false)
  })

  it('a server FACE lights that face, not its patches', () => {
    const s = sel({ type: 'serverFace', uid: RACK, equipmentId: 'e_1', faceId: 'outlet' })
    expect(
      isMeshHighlighted({ modelId: RACK, isRackEquipment: true, equipmentId: 'e_1', faceId: 'outlet', isPatch: false }, s, noSpan),
    ).toBe(true)
    expect(
      isMeshHighlighted({ modelId: RACK, isRackEquipment: true, equipmentId: 'e_1', faceId: 'outlet', isPatch: true, patchId: 'p_1' }, s, noSpan),
    ).toBe(false)
    expect(
      isMeshHighlighted({ modelId: RACK, isRackEquipment: true, equipmentId: 'e_1', faceId: 'inlet', isPatch: false }, s, noSpan),
    ).toBe(false)
  })

  it('a server PATCH lights that patch alone', () => {
    const s = sel({ type: 'serverPatch', uid: RACK, equipmentId: 'e_1', faceId: 'outlet', patchId: 'p_1' })
    expect(
      isMeshHighlighted({ modelId: RACK, isRackEquipment: true, equipmentId: 'e_1', faceId: 'outlet', isPatch: true, patchId: 'p_1' }, s, noSpan),
    ).toBe(true)
    expect(
      isMeshHighlighted({ modelId: RACK, isRackEquipment: true, equipmentId: 'e_1', faceId: 'outlet', isPatch: false }, s, noSpan),
    ).toBe(false)
  })

  it('a partition lights that partition', () => {
    const s = sel({ type: 'partition', uid: RACK, index: 3 })
    expect(isMeshHighlighted({ modelId: RACK, isRackPartition: true, partitionIndex: 3 }, s, noSpan)).toBe(true)
    expect(isMeshHighlighted({ modelId: RACK, isRackPartition: true, partitionIndex: 4 }, s, noSpan)).toBe(false)
  })

  it('a slot selection lights the guides in its range, and the server sitting in them', () => {
    const s = sel({ type: 'rackSlot', uid: RACK, side: 'front', index: 10, indices: [10, 11, 12] })
    const span = (id: string) => (id === 'e_1' ? { startUnit: 11, spanUnits: 4 } : null)
    expect(isMeshHighlighted({ modelId: RACK, isRackSlot: true, side: 'front', slotIndex: 11 }, s, span)).toBe(true)
    expect(isMeshHighlighted({ modelId: RACK, isRackSlot: true, side: 'front', slotIndex: 13 }, s, span)).toBe(false)
    expect(isMeshHighlighted({ modelId: RACK, isRackSlot: true, side: 'back', slotIndex: 11 }, s, span)).toBe(false)
    // The server whose U-range overlaps the selected slots (legacy :6558-6564).
    expect(isMeshHighlighted({ modelId: RACK, isRackEquipment: true, equipmentId: 'e_1', faceId: 'top' }, s, span)).toBe(true)
    expect(isMeshHighlighted({ modelId: RACK, isRackEquipment: true, equipmentId: 'e_2', faceId: 'top' }, s, span)).toBe(false)
  })

  it('a fitting is a pipe thing: no geometry mesh lights up', () => {
    const s = sel({ type: 'fitting', uid: RACK, fittingId: 'v_1' })
    expect(isMeshHighlighted({ modelId: RACK, faceId: 'right', isBaseWall: true }, s, noSpan)).toBe(false)
  })

  it('a narrow selection on a model that is NOT the active one lights nothing there', () => {
    const s = sel({ type: 'face', uid: 'm_18', faceId: 'top' }, RACK)
    expect(isMeshHighlighted({ modelId: 'm_18', faceId: 'top', isBaseWall: true }, s, noSpan)).toBe(true)
    expect(isMeshHighlighted({ modelId: RACK, faceId: 'top', isBaseWall: true }, s, noSpan)).toBe(false)
  })
})

describe('highlightStyle', () => {
  it('paints the rack interior solid — a tint on a textured vent is invisible', () => {
    expect(highlightStyle({ type: 'serverPatch', uid: 'm', equipmentId: 'e', faceId: 'outlet', patchId: 'p' })).toBe('solid')
    expect(highlightStyle({ type: 'serverFace', uid: 'm', equipmentId: 'e', faceId: 'inlet' })).toBe('solid')
    expect(highlightStyle({ type: 'equipment', uid: 'm', equipmentId: 'e' })).toBe('solid')
    expect(highlightStyle({ type: 'rackSlot', uid: 'm', index: 3, side: 'front' })).toBe('solid')
  })

  it('tints everything else, the model included', () => {
    expect(highlightStyle(null)).toBe('tint')
    expect(highlightStyle({ type: 'face', uid: 'm', faceId: 'top' })).toBe('tint')
    expect(highlightStyle({ type: 'patch', uid: 'm', faceId: 'top', patchId: 'p' })).toBe('tint')
  })
})
