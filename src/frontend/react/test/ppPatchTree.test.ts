import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import {
  DOMAIN_BOUNDARY_GROUP,
  decodeLiquidTransportUid,
  groupPatchesByModelKind,
  isFlatKind,
  kindLabel,
  kindPatchCount,
  missingEnclosurePatches,
  orderedKinds,
  patchGroupLabel,
  type ModelLike,
  type PatchLike,
  serverSubgroups,
} from '../src/postprocess/ppPatchTree'
import { reviveKinds, stateV4 } from '../src/legacy/cfdprojModules'
import type { CfdProject } from '../src/schema/cfdproj'

const patch = (name: string, nTriangles = 12): PatchLike => ({ name, nTriangles })
const model = (name: string, kind: string): ModelLike => ({ kind, meta: { name } })

describe('groupPatchesByModelKind', () => {
  it('puts a patch under the model whose name prefixes it', () => {
    const g = groupPatchesByModelKind(
      [patch('Rack_1_inlet'), patch('Rack_1_outlet')],
      [model('Rack_1', 'rack')],
    )
    expect(Object.keys(g)).toEqual(['rack'])
    expect(g.rack?.['Rack_1']?.map((p) => p.name)).toEqual(['Rack_1_inlet', 'Rack_1_outlet'])
  })

  it('gives a longer model name priority over a shorter one it contains', () => {
    // Both 'Rack' and 'Rack_1' prefix-match 'Rack_1_inlet'. Sorting models by
    // name length is the only thing that stops every Rack_N patch from being
    // filed under 'Rack'.
    const g = groupPatchesByModelKind(
      [patch('Rack_1_inlet')],
      [model('Rack', 'box'), model('Rack_1', 'rack')],
    )
    expect(g.rack?.['Rack_1']).toHaveLength(1)
    expect(g.box).toBeUndefined()
  })

  it('finds the model inside a merged_ name', () => {
    const g = groupPatchesByModelKind(
      [patch('merged_wall_Room_north')],
      [model('Room_north', 'wall')],
    )
    expect(g.wall?.['Room_north']).toHaveLength(1)
  })

  it('files a baffle master/slave pair under the model that owns the zone', () => {
    const g = groupPatchesByModelKind(
      [patch('Rack_1_inlet_u33_u38_master'), patch('Rack_1_inlet_u33_u38_slave')],
      [model('Rack_1', 'rack')],
    )
    expect(g.rack?.['Rack_1']).toHaveLength(2)
  })

  it('buckets merged_wall_baffle under BAFFLE with no model to match', () => {
    const g = groupPatchesByModelKind([patch('merged_wall_baffle_master')], [])
    expect(g.baffle?.['Wall Baffle']).toHaveLength(1)
  })

  it('attributes a blanking panel to the only rack in the room', () => {
    const g = groupPatchesByModelKind(
      [patch('merged_perforatedBlankingPanel_3U_master')],
      [model('Rack_1', 'rack')],
    )
    expect(g.rack?.['Rack_1']).toHaveLength(1)
  })

  it('refuses to attribute a blanking panel when several racks share it', () => {
    // The patch is ONE merged surface spanning every rack. Filing it under a
    // single rack would make hiding that rack hide other racks' panels.
    const g = groupPatchesByModelKind(
      [patch('merged_wallBlankingPanel_2U_master')],
      [model('Rack_1', 'rack'), model('Rack_2', 'rack')],
    )
    expect(g.rack).toBeUndefined()
    expect(g.baffle?.['Blanking Panel']).toHaveLength(1)
  })

  it('matches a model by the name its STL solid carries (Base2-9 ACUs)', () => {
    // The editor exports `AC-2-9-1` as the solid `AC_2_9_1`; the typed name
    // never prefixes the patch, and 40 ACU patches used to land in OTHER.
    const g = groupPatchesByModelKind(
      [patch('AC_2_9_1_Front'), patch('AC_2_9_1_Top_patch'), patch('AC_2_9_10_Back')],
      [model('AC-2-9-1', 'acu'), model('AC-2-9-10', 'acu')],
    )
    expect(g.acu?.['AC-2-9-1']?.map((p) => p.name)).toEqual(['AC_2_9_1_Front', 'AC_2_9_1_Top_patch'])
    expect(g.acu?.['AC-2-9-10']).toHaveLength(1)
    expect(g._other).toBeUndefined()
  })

  it('files a liquid pipe by the uid its solid name encodes (Shinaide_Liquid)', () => {
    expect(decodeLiquidTransportUid('63_64_75_5f_73_75_70_70_6c_79_5f_66_65_65_64')).toBe('cdu_supply_feed')
    const g = groupPatchesByModelKind(
      [patch('liquid_transport_6d_5f_33_31'), patch('liquid_transport_63_64_75_5f_73_75_70_70_6c_79_5f_66_65_65_64')],
      [
        { uid: 'm_31', kind: 'pipe_segment', meta: { name: 'Supply_Closed_Ring' } },
        { uid: 'cdu_supply_feed', kind: 'pipe_segment', meta: { name: 'CDU_supply_Feed' } },
      ],
    )
    expect(Object.keys(g.pipe_segment ?? {}).sort()).toEqual(['CDU_supply_Feed', 'Supply_Closed_Ring'])
    expect(g._other).toBeUndefined()
  })

  it('buckets a rack door merged across racks under BAFFLE (Shinaide)', () => {
    const g = groupPatchesByModelKind(
      [patch('merged_rack_door_master'), patch('merged_rack_door_slave')],
      [model('Rack_A_01', 'rack'), model('Rack_A_02', 'rack')],
    )
    expect(g.baffle?.['Rack Door']).toHaveLength(2)
    const one = groupPatchesByModelKind([patch('merged_rack_door_master')], [model('Rack_A_01', 'rack')])
    expect(one.rack?.['Rack_A_01']).toHaveLength(1)
  })

  it('keeps an unmatched patch rather than dropping it', () => {
    const g = groupPatchesByModelKind([patch('procBoundary0to1')], [model('Rack_1', 'rack')])
    expect(g._other?.['Other']?.[0]?.name).toBe('procBoundary0to1')
  })

  it('ignores models with no name instead of matching everything', () => {
    // An empty name would prefix-match every patch through startsWith('' + '_')
    // only for names beginning with '_', but === '' never matches; the filter
    // keeps such a model out of the table entirely.
    const g = groupPatchesByModelKind([patch('Rack_1_inlet')], [{ kind: 'box', meta: {} }])
    expect(g._other?.['Other']).toHaveLength(1)
  })
})

describe('tree shape helpers', () => {
  it('shows every kind it grouped, an unnamed one before OTHER (DAML air ducts)', () => {
    const g = groupPatchesByModelKind(
      [patch('Duct_riser_1'), patch('Rack_1_frame'), patch('stray'), patch('Thing_1_x')],
      [model('Duct_riser_1', 'pipe'), model('Rack_1', 'rack'), model('Thing_1', 'gadget')],
    )
    expect(orderedKinds(g)).toEqual(['rack', 'pipe', 'gadget', '_other'])
    const shown = orderedKinds(g).reduce((n, k) => n + kindPatchCount(g, k), 0)
    expect(shown).toBe(4)
    expect(kindLabel('pipe')).toBe('PIPE')
    expect(kindLabel('gadget')).toBe('GADGET')
    expect(kindLabel('_other')).toBe('OTHER')
  })

  it('orders kinds by the panel order, not by discovery order', () => {
    const g = groupPatchesByModelKind(
      [patch('W_a'), patch('R_a')],
      [model('W', 'wall'), model('R', 'rack')],
    )
    expect(orderedKinds(g)).toEqual(['rack', 'wall'])
  })

  it('counts patches across every model of a kind', () => {
    const g = groupPatchesByModelKind(
      [patch('R1_a'), patch('R1_b'), patch('R2_a')],
      [model('R1', 'rack'), model('R2', 'rack')],
    )
    expect(kindPatchCount(g, 'rack')).toBe(3)
  })

  it('draws one model with three patches flat, four with a sub-group', () => {
    const three = groupPatchesByModelKind(
      [patch('R1_a'), patch('R1_b'), patch('R1_c')],
      [model('R1', 'rack')],
    )
    expect(isFlatKind(three.rack as Record<string, PatchLike[]>)).toBe(true)

    const four = groupPatchesByModelKind(
      [patch('R1_a'), patch('R1_b'), patch('R1_c'), patch('R1_d')],
      [model('R1', 'rack')],
    )
    expect(isFlatKind(four.rack as Record<string, PatchLike[]>)).toBe(false)
  })

  it('never draws two models flat, however few patches they have', () => {
    const g = groupPatchesByModelKind(
      [patch('R1_a'), patch('R2_a')],
      [model('R1', 'rack'), model('R2', 'rack')],
    )
    expect(isFlatKind(g.rack as Record<string, PatchLike[]>)).toBe(false)
  })
})

const DOMAIN_SIDES = [
  'domain_xMin',
  'domain_xMax',
  'domain_yMin',
  'domain_yMax',
  'domain_zMin',
  'domain_zMax',
]

describe('dcCart domain sides', () => {
  it('files the six domain_* sides under WALL, in one fixed group', () => {
    // dcCartMesh names the faces of its bounding box domain_<axis><Min|Max>.
    // They match no model; under OTHER they left a leaked Base604 mesh with no
    // WALL head at all.
    const g = groupPatchesByModelKind(
      DOMAIN_SIDES.map((n) => patch(n)),
      [model('Rack_1', 'rack')],
    )
    expect(g.wall?.[DOMAIN_BOUNDARY_GROUP]?.map((p) => p.name)).toEqual(DOMAIN_SIDES)
    expect(g._other).toBeUndefined()
    expect(orderedKinds(g)).toEqual(['wall'])
  })

  it('keeps the group key fixed and translates only the label', () => {
    // The key is also the collapse-state key and the data-model attribute, so
    // it must not change with the locale.
    expect(DOMAIN_BOUNDARY_GROUP).toBe('Domain boundary')
    expect(patchGroupLabel(DOMAIN_BOUNDARY_GROUP, '计算域边界')).toBe('计算域边界')
    expect(patchGroupLabel('Room_Wall_1', '计算域边界')).toBe('Room_Wall_1')
  })

  it('leaves look-alikes that are not a domain side under OTHER', () => {
    const names = ['domain_xmin', 'domain_wMax', 'my_domain_xMin', 'domain_xMin_extra']
    const g = groupPatchesByModelKind(names.map((n) => patch(n)), [])
    expect(g.wall).toBeUndefined()
    expect(g._other?.['Other']?.map((p) => p.name)).toEqual(names)
  })

  it('lets a model really named domain_xMin keep its patch', () => {
    const g = groupPatchesByModelKind([patch('domain_xMin')], [model('domain_xMin', 'box')])
    expect(g.box?.['domain_xMin']).toHaveLength(1)
    expect(g.wall).toBeUndefined()
  })

  it('keeps a room wall under its own model beside the domain group', () => {
    const g = groupPatchesByModelKind(
      [patch('Room_Wall_1'), patch('domain_yMax')],
      [model('Room_Wall_1', 'wall')],
    )
    expect(g.wall?.['Room_Wall_1']).toHaveLength(1)
    expect(g.wall?.[DOMAIN_BOUNDARY_GROUP]).toHaveLength(1)
    expect(kindPatchCount(g, 'wall')).toBe(2)
  })
})

const CASE_DIR = resolve(process.cwd(), '../../../case')

/** A case's models as the mesh preview panel snapshots them. */
function caseModels(dir: string, file: string): ModelLike[] {
  const project = reviveKinds(
    JSON.parse(readFileSync(resolve(CASE_DIR, dir, file), 'utf8')) as CfdProject,
  )
  return stateV4().allModels(project) as ModelLike[]
}

const room = (name: string, kind: string, isRoomBoundary = true): ModelLike => ({
  kind,
  meta: { name },
  isRoomBoundary,
})

describe('missingEnclosurePatches', () => {
  // Patches of the dcCart mesh of Base604 with 3.5 m walls under its 4.4 m
  // ceiling (2026-09-10), minus the 4000 rack rows: no room wall, floor or
  // ceiling patch, and the six domain sides holding the outer faces.
  const LEAKY_BASE604 = [
    'DataHall604_ACU_01_Front',
    'DataHall604_ACU_01_Top_patch',
    'Room_Raising_Floor_patch_1_master',
    'Room_Raising_Floor_patch_1_slave',
    'Room_Raising_Floor_part1_master',
    'Room_Raising_Floor_part1_slave',
    'merged_perforatedBlankingPanel_1U_master',
    'merged_wall_baffle_master',
    'merged_wall_baffle_slave',
  ]
  const ENCLOSURE_BASE604 = [
    'Room_Ceiling',
    'Room_Floor',
    'Room_Wall_1',
    'Room_Wall_2',
    'Room_Wall_3',
    'Room_Wall_4',
  ]

  it('names every room wall, floor and ceiling a leaked Base604 mesh lost', () => {
    const models = caseModels('Base604', 'Base604.cfdproj')
    const found = missingEnclosurePatches(
      [...LEAKY_BASE604, ...DOMAIN_SIDES].map((n) => patch(n)),
      models,
    )
    // Room_Raising_Floor is not a room boundary and is never reported.
    expect([...found].sort()).toEqual(ENCLOSURE_BASE604)
  })

  it('reports nothing once the re-meshed Base604 has its enclosure back', () => {
    const models = caseModels('Base604', 'Base604.cfdproj')
    const names = [...LEAKY_BASE604, ...ENCLOSURE_BASE604, 'domain_xMin']
    expect(missingEnclosurePatches(names.map((n) => patch(n)), models)).toEqual([])
  })

  it('finds DAML complete, its ceiling split into patches', () => {
    // Ceiling_patch_1..3 and Ceiling_part1 ARE the ceiling (the name_ prefix
    // rule). A domain side with faces is added so the check actually runs.
    const models = caseModels('DAML-Testbed', 'DMAL_Testbed-patched.cfdproj')
    const names = [
      'Wall_1',
      'Wall_2',
      'Wall_3',
      'Wall_4',
      'Floor',
      'Ceiling_patch_1',
      'Ceiling_patch_2',
      'Ceiling_patch_3',
      'Ceiling_part1',
      'domain_yMax',
    ]
    expect(missingEnclosurePatches(names.map((n) => patch(n)), models)).toEqual([])
  })

  it('stays quiet without a leak, even with an enclosure patch missing', () => {
    // A missing name alone is not evidence: a snappy mesh has no domain box,
    // and the project may have been edited since the mesh was built.
    const models = [room('Room_Wall_1', 'wall')]
    expect(missingEnclosurePatches([patch('Rack_1_inlet')], models)).toEqual([])
    expect(missingEnclosurePatches([patch('domain_xMin', 0)], models)).toEqual([])
  })

  it('counts a createBaffles pair and a sanitised name as present', () => {
    const models = [room('Room Ceiling', 'floor'), room('Room_Wall_1', 'wall')]
    const names = ['Room_Ceiling_master', 'Room_Ceiling_slave', 'domain_zMax']
    expect(missingEnclosurePatches(names.map((n) => patch(n)), models)).toEqual(['Room_Wall_1'])
  })

  it('reports a missing model by its sanitised name, the name its patch would have', () => {
    expect(
      missingEnclosurePatches([patch('domain_xMin')], [room('Room Wall 1', 'wall')]),
    ).toEqual(['Room_Wall_1'])
  })

  it('never reports an interior wall, a raised floor, an unflagged wall or another kind', () => {
    const models: ModelLike[] = [
      room('Partition', 'wall', false),
      room('Raised', 'floor', false),
      room('Rack_1', 'rack'),
      { kind: 'wall', meta: { name: 'NoFlag' } },
    ]
    expect(missingEnclosurePatches([patch('domain_xMin')], models)).toEqual([])
  })
})

describe('serverSubgroups', () => {
  const p = (name: string) => ({ name, nTriangles: 1 })
  it('files a rack\'s server patches under the server, the rack\'s own patches first', () => {
    const groups = serverSubgroups('SR1', [
      p('SR1_SR1_40_inlet_patch'),
      p('SR1_frame'),
      p('SR1_SR1_5_outlet_p0'),
      p('SR1_SR1_40_top'),
    ])
    expect(groups.map((g) => g.server)).toEqual([null, 'SR1_40', 'SR1_5'])
    expect(groups[0]!.patches.map((x) => x.name)).toEqual(['SR1_frame'])
    expect(groups[1]!.patches.map((x) => x.name)).toEqual(['SR1_SR1_40_inlet_patch', 'SR1_SR1_40_top'])
  })
  it('does not mistake a longer rack name for a server of a shorter one', () => {
    // SR10_SR10_3_top belongs to SR10; against SR1 it must not parse as server "SR1_0"
    const groups = serverSubgroups('SR1', [p('SR10_SR10_3_top')])
    expect(groups).toEqual([{ server: null, patches: [p('SR10_SR10_3_top')] }])
  })
  it('escapes regex characters in the model name', () => {
    expect(serverSubgroups('R(1)', [p('R(1)_R(1)_2_top')])[0]!.server).toBe('R(1)_2')
  })
})
