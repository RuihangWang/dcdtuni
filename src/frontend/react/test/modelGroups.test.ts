import { describe, expect, it, vi } from 'vitest'
import {
  addToGroup,
  autoDissolve,
  compatibleGroups,
  createGroup,
  dissolveGroup,
  groupedIds,
  groupOf,
  type ModelGroup,
  newGroupId,
  nextGroupName,
  normalizeGroups,
  pruneGroups,
  removeFromGroup,
  renameGroup,
  sameGroups,
  sortGroupsByName,
} from '../src/geometry/modelGroups'

const group = (id: string, name: string, memberIds: string[]): ModelGroup => ({
  id,
  name,
  memberIds,
})

describe('createGroup', () => {
  it('takes its members out of the group that held them', () => {
    const before = [group('g1', 'A', ['m1', 'm2', 'm3'])]
    const after = createGroup(before, 'B', ['m1', 'm2'], 'g2')
    expect(after.map((g) => g.id)).toEqual(['g2'])
    // g1 kept only m3 — one member is not a group, so it dissolved.
    expect(after[0]!.memberIds).toEqual(['m1', 'm2'])
  })

  it('leaves a big enough source group behind', () => {
    const before = [group('g1', 'A', ['m1', 'm2', 'm3'])]
    const after = createGroup(before, 'B', ['m1'], 'g2')
    expect(after.map((g) => g.id)).toEqual(['g1', 'g2'])
    expect(after[0]!.memberIds).toEqual(['m2', 'm3'])
  })

  it('does not dissolve the group it is creating', () => {
    // The auto-dissolve runs before the push (STL_generate.html:4530-4534), so
    // a one-member group can be created; it goes on the next mutation.
    const after = createGroup([], 'Solo', ['m1'], 'g1')
    expect(after).toHaveLength(1)
    expect(autoDissolve(after)).toEqual([])
  })

  it('does not mutate the array it was given', () => {
    const before = [group('g1', 'A', ['m1', 'm2'])]
    createGroup(before, 'B', ['m1', 'm2'], 'g2')
    expect(before[0]!.memberIds).toEqual(['m1', 'm2'])
  })
})

describe('addToGroup', () => {
  it('moves a model out of its old group', () => {
    const before = [group('g1', 'A', ['m1', 'm2', 'm3']), group('g2', 'B', ['m4', 'm5'])]
    const after = addToGroup(before, 'g2', ['m1'])
    expect(after.find((g) => g.id === 'g1')!.memberIds).toEqual(['m2', 'm3'])
    expect(after.find((g) => g.id === 'g2')!.memberIds).toEqual(['m4', 'm5', 'm1'])
  })

  it('adds no duplicate when the model is already a member', () => {
    const after = addToGroup([group('g1', 'A', ['m1', 'm2'])], 'g1', ['m1', 'm3'])
    expect(after[0]!.memberIds).toEqual(['m1', 'm2', 'm3'])
  })

  it('dissolves a source group emptied to one member', () => {
    const before = [group('g1', 'A', ['m1', 'm2']), group('g2', 'B', ['m3', 'm4'])]
    const after = addToGroup(before, 'g2', ['m1'])
    expect(after.map((g) => g.id)).toEqual(['g2'])
  })

  it('ignores an unknown group id', () => {
    const before = [group('g1', 'A', ['m1', 'm2'])]
    expect(addToGroup(before, 'nope', ['m1'])).toBe(before)
  })
})

describe('removeFromGroup', () => {
  it('removes the model and keeps a group of two or more', () => {
    const after = removeFromGroup([group('g1', 'A', ['m1', 'm2', 'm3'])], 'm2')
    expect(after[0]!.memberIds).toEqual(['m1', 'm3'])
  })

  it('dissolves the group when one member is left', () => {
    expect(removeFromGroup([group('g1', 'A', ['m1', 'm2'])], 'm2')).toEqual([])
  })

  it('is a no-op for a model in no group', () => {
    const before = [group('g1', 'A', ['m1', 'm2'])]
    expect(removeFromGroup(before, 'm9')).toBe(before)
  })
})

describe('renameGroup', () => {
  it('renames in place', () => {
    const after = renameGroup([group('g1', 'A', ['m1', 'm2'])], 'g1', 'Row 4')
    expect(after[0]!.name).toBe('Row 4')
  })

  it('returns the same array for an unchanged name, so nothing is logged', () => {
    const before = [group('g1', 'A', ['m1', 'm2'])]
    expect(renameGroup(before, 'g1', 'A')).toBe(before)
    expect(renameGroup(before, 'missing', 'X')).toBe(before)
  })
})

describe('dissolveGroup', () => {
  it('drops only the named group', () => {
    const before = [group('g1', 'A', ['m1', 'm2']), group('g2', 'B', ['m3', 'm4'])]
    expect(dissolveGroup(before, 'g1').map((g) => g.id)).toEqual(['g2'])
  })
})

describe('normalizeGroups', () => {
  it('drops members whose model no longer exists', () => {
    const raw = [{ id: 'g1', name: 'A', memberIds: ['m1', 'gone', 'm2'] }]
    const out = normalizeGroups(raw, new Set(['m1', 'm2']))
    expect(out[0]!.memberIds).toEqual(['m1', 'm2'])
  })

  it('dissolves a group left with fewer than two live models', () => {
    const raw = [{ id: 'g1', name: 'A', memberIds: ['m1', 'gone'] }]
    expect(normalizeGroups(raw, new Set(['m1']))).toEqual([])
  })

  it('rejects malformed entries and a non-array blob', () => {
    const raw = [null, 42, { name: 'no id', memberIds: ['m1', 'm2'] }, { id: 'g', memberIds: 'x' }]
    expect(normalizeGroups(raw, new Set(['m1', 'm2']))).toEqual([])
    expect(normalizeGroups(undefined, new Set())).toEqual([])
    expect(normalizeGroups('{}', new Set())).toEqual([])
  })

  it('names a nameless group "Group"', () => {
    const out = normalizeGroups([{ id: 'g1', memberIds: ['m1', 'm2'] }], new Set(['m1', 'm2']))
    expect(out[0]!.name).toBe('Group')
  })
})

describe('pruneGroups', () => {
  it('drops a deleted model from every group it was in', () => {
    const before = [group('g1', 'A', ['m1', 'm2', 'm3']), group('g2', 'B', ['m4', 'm5'])]
    const after = pruneGroups(before, new Set(['m1', 'm3', 'm4', 'm5']))
    expect(after[0]!.memberIds).toEqual(['m1', 'm3'])
    expect(after[1]!.memberIds).toEqual(['m4', 'm5'])
  })
})

describe('sameGroups', () => {
  it('is true only for identical content', () => {
    expect(sameGroups([group('g', 'A', ['m1'])], [group('g', 'A', ['m1'])])).toBe(true)
    expect(sameGroups([group('g', 'A', ['m1'])], [group('g', 'B', ['m1'])])).toBe(false)
    expect(sameGroups([group('g', 'A', ['m1'])], [group('g', 'A', ['m2'])])).toBe(false)
    expect(sameGroups([], [group('g', 'A', ['m1'])])).toBe(false)
  })
})

describe('nextGroupName', () => {
  it('capitalises the kind and takes the first free number', () => {
    expect(nextGroupName([], 'rack')).toBe('Rack Group 1')
    const taken = [group('g1', 'Rack Group 1', ['a', 'b'])]
    expect(nextGroupName(taken, 'rack')).toBe('Rack Group 2')
  })

  it('skips a gap left by a renamed group', () => {
    const taken = [group('g1', 'Rack Group 1', ['a', 'b']), group('g2', 'Rack Group 3', ['c', 'd'])]
    expect(nextGroupName(taken, 'rack')).toBe('Rack Group 2')
  })
})

describe('compatibleGroups', () => {
  const kindOf = (uid: string) => (uid.startsWith('r') ? 'rack' : 'acu')

  it('offers same-kind groups that do not already hold the whole selection', () => {
    const groups = [
      group('g1', 'Racks', ['r1', 'r2']),
      group('g2', 'Acus', ['a1', 'a2']),
      group('g3', 'Mine', ['r3', 'r4']),
    ]
    const out = compatibleGroups(groups, ['r3', 'r4'], kindOf, 'rack')
    expect(out.map((g) => g.id)).toEqual(['g1'])
  })
})

describe('helpers', () => {
  it('finds a model group and the full grouped set', () => {
    const groups = [group('g1', 'A', ['m1', 'm2']), group('g2', 'B', ['m3', 'm4'])]
    expect(groupOf(groups, 'm3')!.id).toBe('g2')
    expect(groupOf(groups, 'm9')).toBeNull()
    expect([...groupedIds(groups)]).toEqual(['m1', 'm2', 'm3', 'm4'])
  })

  it('sorts naturally, so Group 2 precedes Group 10', () => {
    const groups = [group('a', 'Rack Group 10', []), group('b', 'Rack Group 2', [])]
    expect(sortGroupsByName(groups).map((g) => g.name)).toEqual([
      'Rack Group 2',
      'Rack Group 10',
    ])
    expect(sortGroupsByName(groups, true).map((g) => g.name)).toEqual([
      'Rack Group 10',
      'Rack Group 2',
    ])
  })

  it('mints the legacy id shape, with a random tail against same-ms collisions', () => {
    // Two groups made in the same millisecond — "group each of these four rows"
    // does exactly that — would share a timestamp, so the tail is what keeps
    // them apart.
    expect(newGroupId()).toMatch(/^grp_\d+_[0-9a-z]{1,4}$/)
    const values = [0.111111, 0.777777]
    const spy = vi.spyOn(Math, 'random').mockImplementation(() => values.shift() ?? 0)
    try {
      expect(newGroupId()).not.toBe(newGroupId())
    } finally {
      spy.mockRestore()
    }
  })
})
