import { describe, expect, it } from 'vitest'
import { coplanarWindow, hitPriority, isSelectableHit, pickNearestHit } from '../src/interaction/picking'

const hit = (distance: number, userData: Record<string, unknown>) => ({
  distance,
  object: { userData: { modelId: 'm1', ...userData } as Record<string, unknown> },
})

describe('hitPriority', () => {
  it('ranks a patch above a slot guide above the server above the wall', () => {
    expect(hitPriority({ refIds: ['p1'] })).toBeGreaterThan(hitPriority({ isRackSlot: true }))
    expect(hitPriority({ isRackSlot: true })).toBeGreaterThan(hitPriority({ isRackEquipment: true }))
    expect(hitPriority({ isRackEquipment: true })).toBeGreaterThan(hitPriority({ isBaseWall: true }))
    expect(hitPriority({})).toBe(0)
  })

  it('treats an EMPTY refIds as no patch', () => {
    expect(hitPriority({ refIds: [] })).toBe(0)
  })
})

describe('pickNearestHit', () => {
  it('prefers the more specific target among coplanar hits', () => {
    // The panel is nearer by a hair; the slot guide is what was aimed at.
    const picked = pickNearestHit([
      hit(1.0, { isBaseWall: true }),
      hit(1.0008, { isRackSlot: true, slotIndex: 7 }),
    ])
    expect(picked?.object.userData.slotIndex).toBe(7)
  })

  it('does not let a distant specific hit beat a near one', () => {
    // A patch on a rack two metres behind must not win over the wall in front.
    const picked = pickNearestHit([
      hit(1.0, { isBaseWall: true }),
      hit(3.0, { refIds: ['p1'] }),
    ])
    expect(picked?.object.userData.isBaseWall).toBe(true)
  })

  it('restricts candidates to the nearest hit’s own model', () => {
    const other = { distance: 1.0005, object: { userData: { modelId: 'm2', refIds: ['p'] } } }
    const picked = pickNearestHit([hit(1.0, { isBaseWall: true }), other])
    expect(picked?.object.userData.modelId).toBe('m1')
  })

  it('breaks a priority tie by distance', () => {
    const picked = pickNearestHit([
      hit(1.0, { isRackEquipment: true, equipmentId: 'a' }),
      hit(1.0002, { isRackEquipment: true, equipmentId: 'b' }),
    ])
    expect(picked?.object.userData.equipmentId).toBe('a')
  })

  it('returns null for no hits', () => {
    expect(pickNearestHit([])).toBeNull()
  })

  it('uses a 5 mm floor for the window on very close hits', () => {
    // At 10 cm, 0.1% is 0.1 mm — smaller than the offsets the builders use to
    // avoid z-fighting, so the floor is what makes the rule work up close.
    expect(coplanarWindow(0.1)).toBe(0.005)
    expect(coplanarWindow(20)).toBeCloseTo(0.02, 12)
  })
})

/**
 * Only MESHES are selectable from the viewport.
 *
 * The shared rack builder draws a U-slot boundary guide as a THREE.Line tagged
 * with the rack's `modelId`, 1 mm in front of each multi-U blank range. A Line
 * is hit anywhere within `Raycaster.params.Line.threshold` — one world unit by
 * default — so a raycast over the whole scene reported it IN FRONT of the real
 * inlet face. `pickNearestHit` then restricted the candidates to the Line's
 * model and kept the Line: a front-face click selected the neighbouring rack in
 * room view, or no server face at all in rack detail. The legacy never saw it,
 * because it raycasts `renderMeshes` only.
 */
describe('isSelectableHit', () => {
  const on = (
    distance: number,
    kind: 'mesh' | 'line',
    userData: Record<string, unknown>,
    visible = true,
  ) => ({
    distance,
    object: {
      visible,
      ...(kind === 'mesh' ? { isMesh: true } : { isLine: true }),
      userData,
    },
  })

  it('looks past a guide line to the slot and server behind it (rack detail)', () => {
    // The distances measured in the browser on DAML R1's CPU1 inlet.
    const hits = [
      on(1.1932, 'line', { modelId: 'm_22', isSlotBoundaryGuide: true }),
      on(1.1954, 'line', { modelId: 'm_22', isSlotBoundaryGuide: true }),
      on(1.1994, 'mesh', { modelId: 'm_22', isRackSlot: true, slotIndex: 32 }),
      on(1.2002, 'mesh', { modelId: 'm_22', isRackEquipment: true, equipmentId: 'e_8', faceId: 'inlet' }),
    ]
    // Unfiltered, the 5 mm window ends before the slot guide and the Line wins.
    expect(pickNearestHit(hits)?.object.userData.isSlotBoundaryGuide).toBe(true)
    const picked = pickNearestHit(hits.filter(isSelectableHit))
    expect(picked?.object.userData.isRackSlot).toBe(true)
  })

  it('does not let another rack’s guide line take the click (room view)', () => {
    const hits = [
      on(0.485, 'line', { modelId: 'm_23', isSlotBoundaryGuide: true }),
      on(0.495, 'line', { modelId: 'm_23', isSlotBoundaryGuide: true }),
      on(0.553, 'mesh', { modelId: 'm_22', isRackEquipment: true, equipmentId: 'e_8', faceId: 'inlet' }),
    ]
    expect(pickNearestHit(hits)?.object.userData.modelId).toBe('m_23')
    const picked = pickNearestHit(hits.filter(isSelectableHit))
    expect(picked?.object.userData).toMatchObject({ modelId: 'm_22', faceId: 'inlet' })
  })

  it('keeps a visible mesh that names a model, by modelId or uid', () => {
    expect(isSelectableHit(on(1, 'mesh', { modelId: 'm_1' }))).toBe(true)
    expect(isSelectableHit(on(1, 'mesh', { uid: 'm_1' }))).toBe(true)
  })

  it('drops hidden meshes, anonymous meshes and every non-mesh', () => {
    expect(isSelectableHit(on(1, 'mesh', { modelId: 'm_1' }, false))).toBe(false)
    expect(isSelectableHit(on(1, 'mesh', {}))).toBe(false)
    expect(isSelectableHit(on(1, 'line', { modelId: 'm_1' }))).toBe(false)
  })
})
