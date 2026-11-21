import { describe, expect, it } from 'vitest'
import { logRows, MAX_RENDERED_LINES } from '../src/components/logRows'

const batch = (at: number, n: number) => ({
  at,
  action: 'dctwin',
  detail: Array.from({ length: n }, (_, i) => `b${at} l${i}`).join('\n'),
})

describe('logRows', () => {
  it('caps the rendered rows however many lines each batch holds', () => {
    // Newest first, as the store keeps them: CMI-Guichou's mesh streamed 99 693 lines.
    const entries = Array.from({ length: 300 }, (_, i) => batch(300 - i, 400))
    const { rows, hiddenOlder } = logRows(entries)
    expect(rows).toHaveLength(MAX_RENDERED_LINES)
    expect(rows[rows.length - 1]?.line).toBe('b300 l399')
    expect(hiddenOlder).toBeGreaterThan(0)
  })

  it('keeps every line, oldest first, when under the cap', () => {
    const { rows, hiddenOlder } = logRows([batch(2, 2), batch(1, 2)])
    expect(rows.map((r) => r.line)).toEqual(['b1 l0', 'b1 l1', 'b2 l0', 'b2 l1'])
    expect(hiddenOlder).toBe(0)
  })

  it('counts a batch cut part-way as not fully shown and keeps its newest lines', () => {
    const { rows, hiddenOlder } = logRows([batch(2, 1), batch(1, 3)], 2)
    expect(rows.map((r) => r.line)).toEqual(['b1 l2', 'b2 l0'])
    expect(hiddenOlder).toBe(1)
  })

  it('renders an entry with no detail as one empty row', () => {
    expect(logRows([{ at: 1, action: 'x', detail: '' }]).rows.map((r) => r.line)).toEqual([''])
  })
})
