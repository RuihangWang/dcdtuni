import { beforeAll, describe, expect, it } from 'vitest'

/**
 * The naming rules, tested against the shared module both editors call.
 *
 * These are OpenFOAM patch names. A test that only checked "some string comes
 * back" would pass while a solid was routed to the wrong merged file and its
 * boundary condition landed on a different wall — which still meshes, still
 * passes mesh acceptance, and reports the wrong temperatures.
 */

type Naming = {
  sanitizeName(t: string | null | undefined): string
  usesSyntheticSingleFaceExportName(m: unknown): boolean
  getFaceDisplayName(f: { displayName?: string; label?: string }): string | undefined
  classifyRackSolidToBucketFile(solid: string, model: unknown, opts?: { blankingPerRack?: boolean }): string | null
  getRackThroughZoneGroups(model: unknown): { startUnit: number; endUnit: number }[]
  blankingBucketOptions(project: unknown): { blankingPerRack: boolean }
}

let naming: Naming

beforeAll(async () => {
  await import('../../../shared/js/stl_naming.js')
  naming = (globalThis as unknown as { stlNaming: Naming }).stlNaming
})

describe('sanitizeName', () => {
  it('keeps only [A-Za-z0-9_]', () => {
    expect(naming.sanitizeName('Rack A-1')).toBe('Rack_A_1'.replace(/ /g, '_'))
    expect(naming.sanitizeName('机柜 01')).toBe('_01')
    expect(naming.sanitizeName('a//b__c')).toBe('a_b__c')
  })

  it('falls back to "model" for an empty name', () => {
    // An empty solid name would produce `solid \n`, which OpenFOAM reads as a
    // patch with no name.
    expect(naming.sanitizeName('')).toBe('model')
    expect(naming.sanitizeName(null)).toBe('model')
    expect(naming.sanitizeName(undefined)).toBe('model')
  })
})

describe('single-face export names', () => {
  it('applies to walls, floors and baffles only', () => {
    for (const kind of ['wall', 'floor', 'baffle']) {
      expect(naming.usesSyntheticSingleFaceExportName({ kind })).toBe(true)
    }
    for (const kind of ['rack', 'acu', 'box', 'partitionWall', 'cdu']) {
      expect(naming.usesSyntheticSingleFaceExportName({ kind })).toBe(false)
    }
  })

  it('handles a missing model', () => {
    expect(naming.usesSyntheticSingleFaceExportName(null)).toBe(false)
  })
})

describe('rack solid routing', () => {
  const porous = { kind: 'rack', cooling: { blankingPanelType: 'typicalSmallGap' } }
  const solid = { kind: 'rack', cooling: { blankingPanelType: 'fullBlock' } }

  it('leaves frame solids to the scene-level group', () => {
    expect(naming.classifyRackSolidToBucketFile('R1_frame_outer_left', porous)).toBeNull()
    expect(naming.classifyRackSolidToBucketFile('R1_frame', porous)).toBeNull()
  })

  it('routes partitions to the 1U partition bucket', () => {
    // Partitions are always a single 1U horizontal divider.
    expect(naming.classifyRackSolidToBucketFile('R1_u5_partition', porous)).toBe(
      'merged_partition_1U.stl'
    )
  })

  it('routes blanking panels by the rack blankingPanelType', () => {
    // The SAME solid name goes to a different file depending on the rack — this
    // is the routing that decides porous jump versus solid wall.
    expect(naming.classifyRackSolidToBucketFile('R1_inlet_u10_u13', porous)).toBe(
      'merged_perforatedBlankingPanel_4U.stl'
    )
    expect(naming.classifyRackSolidToBucketFile('R1_inlet_u10_u13', solid)).toBe(
      'merged_wallBlankingPanel_4U.stl'
    )
    // fully open panels stay cyclic baffles (σ 1 in the manifest), not walls
    const open = { kind: 'rack', cooling: { blankingPanelType: 'fullyOpen' } }
    expect(naming.classifyRackSolidToBucketFile('R1_inlet_u10_u13', open)).toBe(
      'merged_perforatedBlankingPanel_4U.stl'
    )
  })

  it('treats a single-slot panel as 1U', () => {
    expect(naming.classifyRackSolidToBucketFile('R1_outlet_u7', porous)).toBe(
      'merged_perforatedBlankingPanel_1U.stl'
    )
  })

  it('spans the U range inclusively', () => {
    // u10..u13 is FOUR slots, not three. An off-by-one here merges a panel into
    // the wrong thickness bucket and the mesh resolves it at the wrong scale.
    expect(naming.classifyRackSolidToBucketFile('R1_inlet_u1_u1', porous)).toBe(
      'merged_perforatedBlankingPanel_1U.stl'
    )
    expect(naming.classifyRackSolidToBucketFile('R1_inlet_u1_u42', porous)).toBe(
      'merged_perforatedBlankingPanel_42U.stl'
    )
  })

  it('gives each rack its own blanking bucket when meshConfig asks for it', () => {
    const perRack = naming.blankingBucketOptions({ runDefaults: { meshConfig: { blankingPanelPatches: 'perRack' } } })
    expect(perRack).toEqual({ blankingPerRack: true })
    expect(naming.blankingBucketOptions({ runDefaults: { meshConfig: {} } })).toEqual({ blankingPerRack: false })
    expect(naming.blankingBucketOptions(null)).toEqual({ blankingPerRack: false })
    expect(naming.classifyRackSolidToBucketFile('A01_inlet_u10_u13', porous, perRack)).toBe(
      'merged_perforatedBlankingPanel_A01_4U.stl'
    )
    expect(naming.classifyRackSolidToBucketFile('Rack_B_06_outlet_u7', solid, perRack)).toBe(
      'merged_wallBlankingPanel_Rack_B_06_1U.stl'
    )
    // the default stays one bucket per span for all racks
    expect(naming.classifyRackSolidToBucketFile('A01_inlet_u10_u13', porous, { blankingPerRack: false })).toBe(
      'merged_perforatedBlankingPanel_4U.stl'
    )
  })

  it('ignores non-rack models', () => {
    expect(naming.classifyRackSolidToBucketFile('X_inlet_u1', { kind: 'acu' })).toBeNull()
    expect(naming.classifyRackSolidToBucketFile('', porous)).toBeNull()
  })
})
