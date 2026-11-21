import { beforeEach, describe, expect, it } from 'vitest'
import type { DecodedMesh } from '../src/postprocess'

describe('scanoSnapshots', () => {
  let s: typeof import('../src/postprocess/scanoSnapshots')
  const mesh = {} as DecodedMesh
  const input = (m = mesh, v = 1) => ({ mesh: m, field: 'T', values: new Float32Array([v, v + 1]), hidden: new Set<string>(),
    colorMap: 'coolwarm' as const, surfaceVisible: true, slices: [], volume: null })
  beforeEach(async () => {
    s = await import('../src/postprocess/scanoSnapshots')
    s.clearSnapshots()
  })

  it('copies the values, keeps at most MAX, and opens only with snapshots', () => {
    const data = input()
    const first = s.addSnapshot(data, ['ACU 2 m³/s'])
    data.values[0] = 99
    expect(first.values[0]).toBe(1)
    for (let i = 0; i < s.MAX_SNAPSHOTS + 2; i++) s.addSnapshot(input(mesh, i), [])
    expect(s.getSnapshots().length).toBe(s.MAX_SNAPSHOTS)
    s.setSnapshotViewOpen(true)
    expect(s.isSnapshotViewOpen()).toBe(true)
    s.clearSnapshots()
    expect(s.isSnapshotViewOpen()).toBe(false)
    s.setSnapshotViewOpen(true)
    expect(s.isSnapshotViewOpen()).toBe(false)
  })

  it('keeps snapshots of another mesh: results before and after a patch update compare side by side', () => {
    const before = {} as DecodedMesh
    const after = {} as DecodedMesh
    s.addSnapshot(input(before), [])
    s.addSnapshot(input(after), [])
    expect(s.getSnapshots().map((x) => x.mesh)).toEqual([before, after])
  })
})
