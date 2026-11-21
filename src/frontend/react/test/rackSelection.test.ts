import { describe, expect, it } from 'vitest'
import { equipmentEndUnit, selectionContext, splitSpans, summarise } from '../src/geometry/rackSelection'

const server = { id: 'e1', kind: 'fanServer', startUnit: 4, spanUnits: 4 } // U4..U7
const occupants = (index: number) => (index >= 4 && index <= 7 ? server : null)
const noneDeleted = () => false

const sum = (indices: number[]) => summarise('front', indices, occupants, noneDeleted)

describe('summarise', () => {
  it('sorts the indices and reports a gap as not contiguous', () => {
    const s = summarise('front', [5, 3, 4], occupants, noneDeleted)!
    expect(s.slotIndices).toEqual([3, 4, 5])
    expect(s.contiguous).toBe(true)
    expect(summarise('front', [3, 5], occupants, noneDeleted)!.contiguous).toBe(false)
  })

  it('spots equipment anywhere in the range', () => {
    expect(sum([2, 3])!.hasEquipment).toBe(false)
    expect(sum([3, 4])!.hasEquipment).toBe(true)
  })

  it('returns null for an empty selection', () => {
    expect(summarise('front', [], occupants, noneDeleted)).toBeNull()
  })
})

describe('selectionContext removal modes', () => {
  const mode = (indices: number[]) => selectionContext(sum(indices), occupants)?.removalMode

  it('full when the selection covers the whole server', () => {
    expect(mode([4, 5, 6, 7])).toBe('full')
  })

  it('trimStart / trimEnd when it is flush with one end', () => {
    expect(mode([4, 5])).toBe('trimStart')
    expect(mode([6, 7])).toBe('trimEnd')
  })

  it('split when it is in the middle — the destructive one', () => {
    expect(mode([5, 6])).toBe('split')
  })

  it('offers no removal when the selection includes a FREE slot', () => {
    // Removing "some of a server and some empty air" has no meaning.
    const ctx = selectionContext(sum([3, 4, 5]), occupants)!
    expect(ctx.canRemoveFromEquipment).toBe(false)
  })

  it('offers nothing when two servers are involved', () => {
    const second = { id: 'e2', kind: 'nonFan', startUnit: 9, spanUnits: 1 }
    const two = (i: number) => (i === 9 ? second : occupants(i))
    const ctx = selectionContext(summarise('front', [4, 5, 6, 7, 8, 9], two, noneDeleted), two)!
    expect(ctx.singleEquipment).toBeNull()
    expect(ctx.canRemoveFromEquipment).toBe(false)
    expect(ctx.canExtendEquipment).toBe(false)
  })
})

describe('selectionContext extension', () => {
  it('extends when the whole server plus a free slot is selected', () => {
    expect(selectionContext(sum([3, 4, 5, 6, 7]), occupants)!.canExtendEquipment).toBe(true)
    expect(selectionContext(sum([4, 5, 6, 7, 8]), occupants)!.canExtendEquipment).toBe(true)
  })

  it('does NOT extend from a partial cover plus a free slot', () => {
    // That would MOVE the server, not lengthen it.
    expect(selectionContext(sum([3, 4, 5]), occupants)!.canExtendEquipment).toBe(false)
  })

  it('does not extend when nothing free is selected', () => {
    expect(selectionContext(sum([4, 5, 6, 7]), occupants)!.canExtendEquipment).toBe(false)
  })
})

describe('splitSpans', () => {
  it('leaves a server either side of the removed middle', () => {
    // U4..U7 with U5..U6 removed leaves U4 (1U) and U7 (1U).
    expect(splitSpans(server, 5, 6)).toEqual({ lowerSpan: 1, upperStart: 7, upperSpan: 1 })
  })
})

describe('equipmentEndUnit', () => {
  it('is inclusive', () => {
    expect(equipmentEndUnit(server)).toBe(7)
    expect(equipmentEndUnit({ id: 'x', startUnit: 2, spanUnits: 1 })).toBe(2)
  })
})
