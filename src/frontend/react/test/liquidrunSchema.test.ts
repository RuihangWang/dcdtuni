import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { compileDynamicLiquidGraph } from '../src/liquid/dynamicLiquidGraph'
import { makeLiquidrunV2 } from '../src/schema/liquidrun'
import type { CfdProject } from '../src/schema/cfdproj'

function source() {
  const project = JSON.parse(readFileSync(resolve(process.cwd(), '../../../case/verification/shinaide-legacy-projects/Shinaide_liquid.json'), 'utf8')) as CfdProject
  if (!project.uid) throw new Error('Shinaide fixture requires its stable project UID')
  const graph = compileDynamicLiquidGraph(project, {dt_s:1,duration_s:10})
  return {graph, options:{projectUid:project.uid,projectName:'Shinaide',controllers:[]}}
}

describe('liquidrun distributable schema before export', () => {
  it('rejects a contradictory legacy solver device field with its JSON path', () => {
    const {graph, options} = source()
    expect(makeLiquidrunV2(graph, options).execution.device).toBe('cpu')
    graph.solver.device = 'cuda'
    expect(() => makeLiquidrunV2(graph, options)).toThrow(/\/solver\/device/)
  })

  it('rejects an unknown physical parameter instead of exporting it', () => {
    const {graph, options} = source()
    graph.edges[0]!.params.misspelled_diameter = 0.025
    expect(() => makeLiquidrunV2(graph, options)).toThrow(/\/edges\/0\/params\/misspelled_diameter/)
  })
})
