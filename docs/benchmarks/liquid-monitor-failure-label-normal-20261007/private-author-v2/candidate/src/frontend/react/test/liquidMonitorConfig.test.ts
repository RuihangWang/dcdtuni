import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { beforeEach, describe, expect, it } from 'vitest'
import type { CfdProject } from '../src/schema/cfdproj'
import { reviveKinds } from '../src/legacy/cfdprojModules'
import { buildLiquidGraph } from '../src/legacy/liquidGraphgen'
import {
  buildMonitoringConfig,
  CDU_METRICS,
  cpCatalogFromGraph,
  cpCatalogFromProject,
  DEFAULT_LIQUID_MONITOR_SELECTION,
  getLiquidMonitorSelection,
  monitorCpIds,
  resetLiquidMonitorSelection,
  type CpCuMonitorEntry,
} from '../src/liquid/liquidMonitorConfig'

/**
 * `monitor_cps` decides whether the run reports anything at all, and both
 * failure modes are silent: an empty list gives an empty chart, and a
 * well-formed but WRONG id is dropped by the runner without a word. So the
 * catalogue is tested against the real compiler's output, not against itself.
 */

const LIQUID_PROJECT = resolve(process.cwd(), '../../../case/Liquid/Liquid.cfdproj')

function catalog(ids: string[]): CpCuMonitorEntry[] {
  return ids.map((id) => ({ id, label: id, qW: 350 }))
}

function projectWith(overrides: Record<string, unknown> = {}): CfdProject {
  return {
    constructions: {
      racks: {
        r1: {
          uid: 'r1',
          meta: { name: 'Rack_1', enabled: true },
          cooling: { type: 'liquid' },
          constructions: {
            servers: {
              e1: {
                uid: 'e1',
                meta: { name: 'srv_1' },
                liquidCooling: { cpCuPairs: [{ name: 'CP-CU_1' }, { Q_W: 700 }] },
              },
            },
          },
          ...overrides,
        },
      },
    },
  } as unknown as CfdProject
}

describe('cpCatalogFromProject', () => {
  it('names each plate the way the compiler names its edge', () => {
    // liquid_graphgen.js:526 — `<rack>_<server>_cp<i>`, zero-based.
    expect(cpCatalogFromProject(projectWith()).map((c) => c.id)).toEqual([
      'Rack_1_srv_1_cp0',
      'Rack_1_srv_1_cp1',
    ])
  })

  it('labels an unnamed pair `cp<i>`, as lsmLoadCPList does', () => {
    const entries = cpCatalogFromProject(projectWith())
    expect(entries[0]?.label).toBe('Rack_1/srv_1/CP-CU_1')
    expect(entries[1]?.label).toBe('Rack_1/srv_1/cp1')
    expect(entries[1]?.qW).toBe(700)
  })

  it('skips an air-cooled rack', () => {
    // The compiler only walks liquid racks, so an air rack's servers produce
    // no cold_plate edge for the solver to report on.
    expect(cpCatalogFromProject(projectWith({ cooling: { type: 'air' } }))).toEqual([])
  })

  it('skips a disabled rack', () => {
    expect(
      cpCatalogFromProject(projectWith({ meta: { name: 'Rack_1', enabled: false } })),
    ).toEqual([])
  })

  it('is empty for no project', () => {
    expect(cpCatalogFromProject(null)).toEqual([])
  })
})

describe('cpCatalogFromGraph', () => {
  it('reads the compiled graph the way lsmLoadCPList does', () => {
    const graph = {
      racks: [
        {
          id: 'Rack_1',
          servers: [
            {
              id: 'srv_1',
              cp_cu_pairs: [{ id: 'CP-CU_1', edge_id: 'Rack_1_srv_1_cp0', Q_W: 350 }],
            },
          ],
        },
      ],
    }
    expect(cpCatalogFromGraph(graph)).toEqual([
      { id: 'Rack_1_srv_1_cp0', label: 'Rack_1/srv_1/CP-CU_1', qW: 350 },
    ])
  })

  it('ignores a pair with no edge id — there is nothing to monitor', () => {
    expect(
      cpCatalogFromGraph({ racks: [{ id: 'r', servers: [{ id: 's', cp_cu_pairs: [{ id: 'x' }] }] }] }),
    ).toEqual([])
  })

  it('is empty for a graph that failed to compile', () => {
    expect(cpCatalogFromGraph(null)).toEqual([])
  })
})

describe('the project scan agrees with the real compiler', () => {
  it('produces the same ids and labels for case/Liquid', () => {
    const project = reviveKinds(JSON.parse(readFileSync(LIQUID_PROJECT, 'utf8')) as CfdProject)
    const graph = buildLiquidGraph(project, {
      fluid: { rho: 997, cp: 4182, mu: 0.00089, k: 0.606, supplyTemp: 35 },
      solver: {},
      enabled: true,
    })
    // A null graph means the fixture stopped validating, not that the ids
    // match — fail loudly rather than comparing two empty lists.
    expect(graph).not.toBeNull()
    const fromGraph = cpCatalogFromGraph(graph)
    expect(fromGraph.length).toBeGreaterThan(0)
    expect(cpCatalogFromProject(project)).toEqual(fromGraph)
  })
})

describe('buildMonitoringConfig', () => {
  it('monitors every plate by default', () => {
    const cfg = buildMonitoringConfig(catalog(['a', 'b']), DEFAULT_LIQUID_MONITOR_SELECTION)
    expect(cfg.cpList.map((c) => c.id)).toEqual(['a', 'b'])
    expect(cfg.loss).toBe(true)
    expect(cfg.cduMetrics).toEqual([...CDU_METRICS])
  })

  it('drops the deselected ones', () => {
    const cfg = buildMonitoringConfig(catalog(['a', 'b', 'c']), {
      ...DEFAULT_LIQUID_MONITOR_SELECTION,
      deselectedCps: ['b'],
    })
    expect(cfg.cpList.map((c) => c.id)).toEqual(['a', 'c'])
  })

  it('keeps a plate the selection has never heard of', () => {
    // The selection stores what was UNCHECKED, so a plate added after the
    // panel was last open is monitored — the legacy rebuilds its list checked
    // on every run, and losing a new plate silently would be worse than
    // monitoring one the user did not ask about.
    expect(
      monitorCpIds(catalog(['a', 'new']), {
        ...DEFAULT_LIQUID_MONITOR_SELECTION,
        deselectedCps: ['a'],
      }),
    ).toEqual(['new'])
  })

  it('reports the CDU metrics in a fixed order, not in click order', () => {
    const cfg = buildMonitoringConfig([], {
      ...DEFAULT_LIQUID_MONITOR_SELECTION,
      cduMetrics: ['total_flow', 'return_P'],
    })
    expect(cfg.cduMetrics).toEqual(['return_P', 'total_flow'])
  })
})

describe('the shared selection', () => {
  beforeEach(() => resetLiquidMonitorSelection())

  it('starts with everything on', () => {
    expect(getLiquidMonitorSelection()).toEqual(DEFAULT_LIQUID_MONITOR_SELECTION)
  })

})
