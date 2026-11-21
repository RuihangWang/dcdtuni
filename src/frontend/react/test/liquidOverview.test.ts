import { t } from '../src/i18n'
import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import type { CfdProject } from '../src/schema/cfdproj'
import { CP_WATER, cduOverview, serverOutletC } from '../src/liquid/liquidOverview'
import * as overview from '../src/liquid/liquidOverview'
import { parseLiquidResults } from '../src/liquid/liquidResults'

function project(): CfdProject {
  return {
    name: 'T',
    constructions: {
      cdus: {
        m_1: { uid: 'm_1', meta: { name: 'CDU_1', enabled: true, racks: ['Rack_1', 'Rack_2'] } },
        m_2: { uid: 'm_2', meta: { name: 'CDU_2', enabled: true } },
        m_3: { uid: 'm_3', meta: { name: 'CDU_off', enabled: false, racks: ['Rack_9'] } },
      },
    },
  } as unknown as CfdProject
}

describe('cduOverview', () => {
  it('lists each enabled CDU with the racks it serves', () => {
    expect(cduOverview(project())).toEqual([
      { uid: 'm_1', name: 'CDU_1', served: 'Rack_1, Rack_2' },
      { uid: 'm_2', name: 'CDU_2', served: t('lq_ux.cdu_served_none') },
    ])
  })

  it('leaves a disabled CDU out — it took no part in the run', () => {
    expect(cduOverview(project()).map((r) => r.uid)).not.toContain('m_3')
  })

  it('is empty without a project', () => {
    expect(cduOverview(null)).toEqual([])
  })

  it('resolves all 14 actual Shinaide racks from CDU ports and pipe-valve UIDs despite empty legacy metadata', () => {
    const real = JSON.parse(readFileSync(resolve(process.cwd(), '../../../case/verification/shinaide-legacy-projects/Shinaide_liquid.json'), 'utf8')) as CfdProject
    const names = Object.values(real.constructions!.racks!).filter(rack => rack.meta.enabled !== false).map(rack => rack.meta.name)
    const row = cduOverview(real).find(cdu => cdu.uid === 'm_30')!
    expect(row.served.split(', ').sort()).toEqual(names.sort())
    expect(row.served.split(', ')).toHaveLength(14)
    // Disable one connected rack: topology references must not resurrect it.
    real.constructions!.racks!.m_1!.meta.enabled = false
    expect(cduOverview(real).find(cdu => cdu.uid === 'm_30')!.served.split(', ')).toHaveLength(13)
  })
})

describe('serverOutletC', () => {
  it('adds the temperature rise Q / (m cp) to the rack supply', () => {
    // 4182 W through 1 kg/s of water is exactly 1 K.
    expect(serverOutletC(30, 1, CP_WATER)).toBeCloseTo(31, 9)
    expect(serverOutletC(30, 0.02, 350)).toBeCloseTo(30 + 350 / (0.02 * CP_WATER), 9)
  })

  it('reports the supply temperature for a server with no flow', () => {
    // Dividing by a zero flow would print Infinity in the table; the legacy
    // reports "no rise" instead.
    expect(serverOutletC(30, 0, 350)).toBe(30)
    expect(serverOutletC(30, 1e-9, 350)).toBe(30)
  })

  it('uses the selected fluid heat capacity and absolute flow for a legacy estimate', () => {
    expect(serverOutletC(30, -0.5, 2000, 2000)).toBe(32)
  })
})

describe('result fluid and solved server outlets', () => {
  const input = {
    time_s:[0,1], edge_ids:['cp-first','cp-last'], edge_flow_kg_s:[[.5,-.5],[.5,-.5]],
    rack_summaries:[{rack_id:'rack',total_flow_kg_s:[.5,.5],supply_temp_K:[300,300],return_temp_K:[320,320],
      servers:[{server_id:'server',flow_kg_s:[.5,.5],max_chip_temp_K:[340,340],heat_removed_W:[2000,2000],cp_edge_ids:['cp-first','cp-last']}]}],
    cp_details:[{edge_id:'cp-first',water_outlet_temp_K:[310,321]},{edge_id:'cp-last',water_outlet_temp_K:[315,311]}],
  }

  it('converts LPM using result density and selects the last physical plate in each flow direction', () => {
    expect(overview).toHaveProperty('resultFluid')
    const fluid = overview.resultFluid({fluid:{rho_kg_m3:800,cp_J_kgK:2000}})
    expect(overview.flowLPM(.4,fluid.rho_kg_m3)).toBe(30)
    const results = parseLiquidResults(input)!
    const server = results.rack_summaries[0]!.servers[0]!
    const boundary = {supplyC:30,returnC:40,cpJkgK:fluid.cp_J_kgK}
    expect(overview.serverResultOutlet(results,server,0,boundary)).toMatchObject({temperatureC:315-273.15,source:'solved',edgeId:'cp-last'})
    expect(overview.serverResultOutlet(results,server,1,boundary)).toMatchObject({temperatureC:321-273.15,source:'solved',edgeId:'cp-first'})
  })

  it('labels the old no-detail energy-balance fallback and never substitutes it for a missing current outlet', () => {
    expect(overview).toHaveProperty('serverResultOutlet')
    const old = parseLiquidResults({...input,cp_details:[]})!
    const server = old.rack_summaries[0]!.servers[0]!
    const boundary = {supplyC:30,returnC:40,cpJkgK:2000}
    expect(overview.serverResultOutlet(old,server,0,boundary)).toMatchObject({temperatureC:32,source:'legacy-estimate'})
    expect(overview.serverResultOutlet(old,server,1,boundary)).toMatchObject({temperatureC:42,source:'legacy-estimate'})
    const incomplete = parseLiquidResults({...input,cp_details:[input.cp_details[0]]})!
    const missing = overview.serverResultOutlet(incomplete,server,0,boundary)
    expect(missing.source).toBe('unavailable')
    expect(Number.isNaN(missing.temperatureC)).toBe(true)
    expect(overview.resultFluid({})).toMatchObject({rho_kg_m3:997,cp_J_kgK:4182,legacyDefaults:true})
  })
})
