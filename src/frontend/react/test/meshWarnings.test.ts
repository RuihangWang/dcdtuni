import { beforeEach, describe, expect, it } from 'vitest'
import {
  blockingMeshWarnings,
  getMeshWarnings,
  setMeshWarnings,
  subscribeMeshWarnings,
} from '../src/run/meshWarnings'

/**
 * The shapes here are the server's, from `parse_checkmesh_warnings`
 * (mesh_quality.py): level 1 negative volumes, level 2 skewness, level 3 a
 * large secondary region — which is what a cold-aisle containment looks like.
 */
const NEGATIVE_VOLUME = {
  level: 1,
  code: 'negative_volume',
  message: 'Detected 12 negative-volume cell(s). Mesh has severe topological errors.',
}
const SKEWNESS = {
  level: 2,
  code: 'high_skewness',
  message: 'Max internal mesh skewness 6.2 exceeds threshold, may cause solver divergence.',
}
const CONTAINMENT = {
  level: 3,
  code: 'disconnected_regions',
  message: 'Detected 2 disconnected mesh regions',
}

describe('blockingMeshWarnings', () => {
  beforeEach(() => setMeshWarnings([]))

  it('is empty on a clean mesh, so the solve runs without a dialog', () => {
    expect(blockingMeshWarnings()).toEqual([])
  })

  it('lets a containment case through', () => {
    // Two mesh regions with a hot-aisle containment is the CORRECT result, and
    // gating on it would put a confirmation in front of every such case.
    setMeshWarnings([CONTAINMENT])
    expect(blockingMeshWarnings()).toEqual([])
    expect(getMeshWarnings()).toHaveLength(1)
  })

  it('stops a solve on level 1 or level 2', () => {
    setMeshWarnings([NEGATIVE_VOLUME, SKEWNESS, CONTAINMENT])
    expect(blockingMeshWarnings().map((w) => w.code)).toEqual([
      'negative_volume',
      'high_skewness',
    ])
  })

  it('notifies subscribers so the panel redraws its detail list', () => {
    let seen = 0
    const off = subscribeMeshWarnings(() => {
      seen += 1
    })
    setMeshWarnings([SKEWNESS])
    off()
    expect(seen).toBe(1)
  })
})
