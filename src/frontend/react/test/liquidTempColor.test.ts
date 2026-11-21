import { describe, expect, it } from 'vitest'
import {
  chipColorTargets,
  colorableRacks,
  lastStepValues,
  tempToColor,
  valueRange,
  mappedPipeTemperatures,
} from '../src/liquid/liquidTempColor'
import { colormapData } from '../src/postprocess/ppColormaps'

const LUT = colormapData('coolwarm')

describe('physical pipe-cell mapping',()=>{
  it('joins cells by compiled edge identity independent of mesh and cell order',()=>{
    const raw={cell_edge_ids:['b','a','b'],cell_temp_K:[[300,320],[310,330],[302,322]],object_bindings:{
      pipeA:{edge_ids:['a']},pipeB:{edge_ids:['b']},empty:{edge_ids:['missing']},
    }}
    expect([...mappedPipeTemperatures(raw,0)]).toEqual([['pipeA',310],['pipeB',301]])
    expect([...mappedPipeTemperatures(raw,1)]).toEqual([['pipeA',330],['pipeB',321]])
  })
  it('never invents a temperature for unmapped or missing cells',()=>{
    expect([...mappedPipeTemperatures({cell_temp_K:[[300]],object_bindings:{p:{edge_ids:['a']}}},0)]).toEqual([])
  })
})

/** Entry `i` of the map, packed the way `tempToColor` packs it. */
function entry(i: number): number {
  return ((LUT[i * 4] as number) << 16) | ((LUT[i * 4 + 1] as number) << 8) | (LUT[i * 4 + 2] as number)
}

describe('lastStepValues', () => {
  it('takes the last sample of each row', () => {
    expect(lastStepValues([[300, 301, 302], [310, 311, 312]])).toEqual([302, 312])
  })

  it('accepts a scalar row, which a pre-transient result writes', () => {
    expect(lastStepValues([300, [310, 311]])).toEqual([300, 311])
  })

  it('is empty for anything that is not a matrix', () => {
    expect(lastStepValues(undefined)).toEqual([])
    expect(lastStepValues(null)).toEqual([])
  })
})

describe('valueRange', () => {
  it('reports min and max', () => {
    expect(valueRange([305, 300, 310])).toEqual({ min: 300, max: 310 })
  })

  it('is null when there is nothing to scale', () => {
    expect(valueRange([])).toBeNull()
  })
})

describe('tempToColor', () => {
  it('maps the ends of the range to the ends of the map', () => {
    expect(tempToColor(300, 300, 320, LUT)).toBe(entry(0))
    // Entry 254, not 255: the legacy's `+1e-6` guard keeps the normalised value
    // just under 1, so the top of the range lands one entry short. Ported as-is
    // — the two apps have to pick the same byte triple for the same kelvin.
    expect(tempToColor(320, 300, 320, LUT)).toBe(entry(254))
  })

  it('clamps outside the range instead of wrapping', () => {
    expect(tempToColor(290, 300, 320, LUT)).toBe(entry(0))
    expect(tempToColor(400, 300, 320, LUT)).toBe(entry(255))
  })

  it('does not divide by zero on a uniform field', () => {
    expect(Number.isFinite(tempToColor(300, 300, 300, LUT))).toBe(true)
  })
})

describe('colorableRacks', () => {
  it('reads V4 servers out of constructions, keyed by uid', () => {
    const models = [
      {
        uid: 'm1',
        kind: 'rack',
        meta: { name: 'Rack-A' },
        constructions: { servers: { s1: { meta: { name: 'srv-1' } } } },
      },
      { uid: 'm2', kind: 'acu', meta: { name: 'ACU' } },
    ]
    expect(colorableRacks(models)).toEqual([
      { uid: 'm1', name: 'Rack-A', servers: [{ uid: 's1', name: 'srv-1' }] },
    ])
  })
})

describe('chipColorTargets', () => {
  const racks = [
    {
      uid: 'rack-uid',
      name: 'Default_Rack_1',
      servers: [
        { uid: 'srv-uid-a', name: 'liquid-server_1' },
        { uid: 'srv-uid-b', name: 'liquid-server_2' },
      ],
    },
  ]
  const summaries = [
    {
      rack_id: 'Default_Rack_1',
      servers: [{ server_id: 'liquid-server_1' }, { server_id: 'liquid-server_2' }],
    },
  ]

  it('pairs the flat chip list with the meshes in walk order', () => {
    const targets = chipColorTargets(summaries, [300, 320], racks, LUT)
    expect(targets.map((t) => [t.modelUid, t.equipmentUid])).toEqual([
      ['rack-uid', 'srv-uid-a'],
      ['rack-uid', 'srv-uid-b'],
    ])
    // Auto-scaled across the pair, so the cold one is the map's first colour.
    expect(targets[0]!.color).toBe(entry(0))
    expect(targets[1]!.color).toBe(entry(254))
  })

  it('matches on the model uid, not an `id` a V4 construction does not have', () => {
    expect(chipColorTargets(summaries, [300, 320], racks, LUT)[0]!.modelUid).toBe('rack-uid')
  })

  it('skips a server the project no longer has rather than colouring the wrong mesh', () => {
    const renamed = [{ ...racks[0]!, servers: [racks[0]!.servers[1]!] }]
    const targets = chipColorTargets(summaries, [300, 320], renamed, LUT)
    expect(targets).toHaveLength(1)
    expect(targets[0]!.equipmentUid).toBe('srv-uid-b')
  })

  it('paints nothing when the result carries no chip temperatures', () => {
    expect(chipColorTargets(summaries, [], racks, LUT)).toEqual([])
  })

  it('binds dynamic UID summaries after rack and server display names change', () => {
    const uidSummaries = [{rack_id:'rack-uid',servers:[{server_id:'srv-uid-a'},{server_id:'srv-uid-b'}]}]
    expect(chipColorTargets(uidSummaries,[300,320],racks,LUT).map(t=>t.equipmentUid)).toEqual(['srv-uid-a','srv-uid-b'])
  })

  it('consumes missing objects and every plate before coloring the next server', () => {
    const multi = [{rack_id:'Default_Rack_1',servers:[
      {server_id:'deleted',cp_edge_ids:['gone']},
      {server_id:'liquid-server_1',cp_edge_ids:['a','b']},
      {server_id:'liquid-server_2',cp_edge_ids:['c']},
    ]}]
    const targets=chipColorTargets(multi,[290,300,310,330],racks,LUT)
    expect(targets.map(t=>t.color)).toEqual([tempToColor(310,290,330,LUT),tempToColor(330,290,330,LUT)])
  })
})
