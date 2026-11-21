import { createHash } from 'node:crypto'
import vm from 'node:vm'
import { coldPlatePairKernel } from '../src/domain/coldPlatePairKernel'
import { readFileSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { DomainProject } from '../src/domain/DomainProject'
import { ColdPlatePairEntity } from '../src/domain/BaseEntity'
import { compileDynamicLiquidGraph } from '../src/liquid/dynamicLiquidGraph'
import { compileSavedExperiment } from '../src/liquid/liquidExperiment'
import { readProjectFileText } from '../src/io/projectFiles'
import { assertPhysicalManifoldBranches } from './helpers/physicalManifoldBranchOracle'
import type { DynamicLiquidGraph } from '../src/liquid/dynamicLiquidGraph'

const root = resolve(process.cwd(), '../../..')
const evidence = process.env.DCDTUNI_CP_EVIDENCE
const sha = (s: string | Buffer) => createHash('sha256').update(s).digest('hex')
const inputPaths = [
  'case/LiquidSingle/LiquidSingle.cfdproj',
  'case/verification/shinaide-legacy-projects/Shinaide_liquid.json',
  '.codex-tmp/cases/Shinaide_LiquidMixed_Keyframe_CUA/Shinaide_LiquidMixed_Keyframe.cfdproj',
]
function project(path: string) {
  const raw = readFileSync(resolve(root, path), 'utf8')
  const result = readProjectFileText(raw)
  if (!result.ok) throw new Error(JSON.stringify(result))
  return { raw, project: result.project }
}
const parameterCases = [
  {name:'defaults', pair:{}, fluid:{}},
  {name:'heatLoad', pair:{heatLoad:432.1}, fluid:{}},
  {name:'Q precedence', pair:{heatLoad:999,Q_W:123.25}, fluid:{}},
  {name:'null fallback', pair:{Q_W:null,heatLoad:null,R_th:null,dp_rated_kPa:null,Q_rated_LPM:null,C_solid:null,V_fluid_mL:null},fluid:{rho:null}},
  {name:'zeros',pair:{Q_W:0,R_th:0,dp_rated_kPa:0,Q_rated_LPM:0,C_solid:0,V_fluid_mL:0},fluid:{rho:0}},
  {name:'decimal order',pair:{Q_W:832.515,R_th:.01947263,dp_rated_kPa:17.432531,Q_rated_LPM:1.7235167,C_solid:134.5421,V_fluid_mL:8.954328},fluid:{rho:998.35416}},
  {name:'negative flow',pair:{Q_rated_LPM:-1,dp_rated_kPa:-2},fluid:{rho:997}},
  {name:'negative density',pair:{Q_rated_LPM:2},fluid:{rho:-1}},
  {name:'both negative mass factors',pair:{Q_rated_LPM:-2,dp_rated_kPa:-3},fluid:{rho:-997}},
  {name:'signed zero',pair:{Q_W:-0,R_th:-0,dp_rated_kPa:-0,Q_rated_LPM:1,C_solid:-0,V_fluid_mL:-0},fluid:{rho:997}},
  {name:'string arithmetic',pair:{Q_W:'832',R_th:'0.02',dp_rated_kPa:'12.5',Q_rated_LPM:'1.3',C_solid:'120',V_fluid_mL:'7'},fluid:{rho:'998.5'}},
  {name:'false passthrough',pair:{Q_W:false,R_th:false,dp_rated_kPa:false,Q_rated_LPM:false,C_solid:false,V_fluid_mL:false},fluid:{rho:false}},
  {name:'NaN input',pair:{Q_W:NaN,R_th:NaN,dp_rated_kPa:NaN,Q_rated_LPM:NaN,C_solid:NaN,V_fluid_mL:NaN},fluid:{rho:NaN}},
  {name:'Infinity input',pair:{Q_W:Infinity,R_th:Infinity,dp_rated_kPa:Infinity,Q_rated_LPM:Infinity,C_solid:Infinity,V_fluid_mL:Infinity},fluid:{rho:Infinity}},
]
function tagged(value: unknown): unknown {
  if (typeof value === 'number') {
    if (Number.isNaN(value)) return {number:'NaN'}
    if (!Number.isFinite(value)) return {number:String(value)}
    if (Object.is(value,-0)) return {number:'-0'}
    const bytes = Buffer.alloc(8); bytes.writeDoubleLE(value)
    return {number:value,bits:bytes.toString('hex')}
  }
  if (Array.isArray(value)) return value.map(tagged)
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([k,v])=>[k,tagged(v)]))
  return value
}

const originalParameterResults = [{"dp_a":{"number":36216975.902632676,"bits":"7b97387f04458141"},"dp_b":{"number":0,"bits":"0000000000000000"},"UA_W_per_K":{"number":33.333333333333336,"bits":"abaaaaaaaaaa4040"},"C_solid_J_per_K":{"number":100,"bits":"0000000000005940"},"V_fluid_m3":{"number":4.9999999999999996e-06,"bits":"f068e388b5f8d43e"},"Q_W":{"number":350,"bits":"0000000000e07540"}},{"dp_a":{"number":36216975.902632676,"bits":"7b97387f04458141"},"dp_b":{"number":0,"bits":"0000000000000000"},"UA_W_per_K":{"number":33.333333333333336,"bits":"abaaaaaaaaaa4040"},"C_solid_J_per_K":{"number":100,"bits":"0000000000005940"},"V_fluid_m3":{"number":4.9999999999999996e-06,"bits":"f068e388b5f8d43e"},"Q_W":{"number":432.1,"bits":"9a99999999017b40"}},{"dp_a":{"number":36216975.902632676,"bits":"7b97387f04458141"},"dp_b":{"number":0,"bits":"0000000000000000"},"UA_W_per_K":{"number":33.333333333333336,"bits":"abaaaaaaaaaa4040"},"C_solid_J_per_K":{"number":100,"bits":"0000000000005940"},"V_fluid_m3":{"number":4.9999999999999996e-06,"bits":"f068e388b5f8d43e"},"Q_W":{"number":123.25,"bits":"0000000000d05e40"}},{"dp_a":{"number":36216975.902632676,"bits":"7b97387f04458141"},"dp_b":{"number":0,"bits":"0000000000000000"},"UA_W_per_K":{"number":33.333333333333336,"bits":"abaaaaaaaaaa4040"},"C_solid_J_per_K":{"number":100,"bits":"0000000000005940"},"V_fluid_m3":{"number":4.9999999999999996e-06,"bits":"f068e388b5f8d43e"},"Q_W":{"number":350,"bits":"0000000000e07540"}},{"dp_a":{"number":50000,"bits":"00000000006ae840"},"dp_b":{"number":0,"bits":"0000000000000000"},"UA_W_per_K":{"number":"Infinity"},"C_solid_J_per_K":{"number":0,"bits":"0000000000000000"},"V_fluid_m3":{"number":0,"bits":"0000000000000000"},"Q_W":{"number":0,"bits":"0000000000000000"}},{"dp_a":{"number":21196428.544420887,"bits":"adf2b5c8e8367441"},"dp_b":{"number":0,"bits":"0000000000000000"},"UA_W_per_K":{"number":51.3541314141952,"bits":"313a9d2d54ad4940"},"C_solid_J_per_K":{"number":134.5421,"bits":"2c6519e258d16040"},"V_fluid_m3":{"number":8.954328e-06,"bits":"704e287851c7e23e"},"Q_W":{"number":832.515,"bits":"85eb51b81e048a40"}},{"dp_a":{"number":50000,"bits":"00000000006ae840"},"dp_b":{"number":0,"bits":"0000000000000000"},"UA_W_per_K":{"number":33.333333333333336,"bits":"abaaaaaaaaaa4040"},"C_solid_J_per_K":{"number":100,"bits":"0000000000005940"},"V_fluid_m3":{"number":4.9999999999999996e-06,"bits":"f068e388b5f8d43e"},"Q_W":{"number":350,"bits":"0000000000e07540"}},{"dp_a":{"number":50000,"bits":"00000000006ae840"},"dp_b":{"number":0,"bits":"0000000000000000"},"UA_W_per_K":{"number":33.333333333333336,"bits":"abaaaaaaaaaa4040"},"C_solid_J_per_K":{"number":100,"bits":"0000000000005940"},"V_fluid_m3":{"number":4.9999999999999996e-06,"bits":"f068e388b5f8d43e"},"Q_W":{"number":350,"bits":"0000000000e07540"}},{"dp_a":{"number":-2716273.192697451,"bits":"614faa9838b944c1"},"dp_b":{"number":0,"bits":"0000000000000000"},"UA_W_per_K":{"number":33.333333333333336,"bits":"abaaaaaaaaaa4040"},"C_solid_J_per_K":{"number":100,"bits":"0000000000005940"},"V_fluid_m3":{"number":4.9999999999999996e-06,"bits":"f068e388b5f8d43e"},"Q_W":{"number":350,"bits":"0000000000e07540"}},{"dp_a":{"number":"-0"},"dp_b":{"number":0,"bits":"0000000000000000"},"UA_W_per_K":{"number":"-Infinity"},"C_solid_J_per_K":{"number":"-0"},"V_fluid_m3":{"number":"-0"},"Q_W":{"number":"-0"}},{"dp_a":{"number":26707280.68558646,"bits":"8229f80a55787941"},"dp_b":{"number":0,"bits":"0000000000000000"},"UA_W_per_K":{"number":50,"bits":"0000000000004940"},"C_solid_J_per_K":"120","V_fluid_m3":{"number":7e-06,"bits":"b75f3e59315cdd3e"},"Q_W":"832"},{"dp_a":{"number":50000,"bits":"00000000006ae840"},"dp_b":{"number":0,"bits":"0000000000000000"},"UA_W_per_K":{"number":"Infinity"},"C_solid_J_per_K":false,"V_fluid_m3":{"number":0,"bits":"0000000000000000"},"Q_W":false},{"dp_a":{"number":50000,"bits":"00000000006ae840"},"dp_b":{"number":0,"bits":"0000000000000000"},"UA_W_per_K":{"number":"NaN"},"C_solid_J_per_K":{"number":"NaN"},"V_fluid_m3":{"number":"NaN"},"Q_W":{"number":"NaN"}},{"dp_a":{"number":"NaN"},"dp_b":{"number":0,"bits":"0000000000000000"},"UA_W_per_K":{"number":0,"bits":"0000000000000000"},"C_solid_J_per_K":{"number":"Infinity"},"V_fluid_m3":{"number":"Infinity"},"Q_W":{"number":"Infinity"}}]
// Original raw bytes are retained. ca1cee61 admitted physical bottom manifold headers
// and route-length cell counts; current derived identities are independently checked below.
// Historical identities were recovered exactly with the pre-edit shared compiler control.
// 2026-10-21: a CDU without cooling.hxModel compiles with no hxModel field
// (mixed-volumes), so these saved projects keep their pre-hxModel SHAs.
const originalInputs = [
  {
    "path": "case/LiquidSingle/LiquidSingle.cfdproj",
    // c97a9bde0 (10-18) changed only meta.casePath (absolute -> case/LiquidSingle).
    // The pre-c97a9bde0 bytes (6df7ba78...) still reproduce this graphSHA256 and the
    // former sourceSHA256 dc1fa0b1... exactly; the new bytes keep the same graph, and
    // the source changes only through source_project.sha256, the content hash of the
    // whole project, which covers meta.casePath.
    "inputSHA256": "0554e7a2c33ea7f0ca76b0db495cf46bd7959ba1d4103c47e6b759355253c524",
    "historicalGraphSHA256": "924cb1808fe656a6948c2029a8d9d2f9455865e0e0b5c93817d6d2add5d93df9",
    "graphSHA256": "6e5a1cad65c033b481e8b0102cb4fa100d9b4d508f0c8428b619b283ed6591c4",
    "historicalSourceSHA256": "c9ba809050eb084e26c6de7c7f0fba8a98de0f88d7d5a266f021770696219ef3",
    "sourceSHA256": "6e1948a7b2e5086a0902eb5decdc829193eb36d2ca73213febe748e417817316",
    "branches": 2, "bottomHeaders": 0,
    "plates": 1
  },
  {
    "path": "case/verification/shinaide-legacy-projects/Shinaide_liquid.json",
    "inputSHA256": "594558e17f9618bf216fefd1f8788c31f7332b1bca00b6a7cadc25247316e626",
    "historicalGraphSHA256": "04922193bfaef78e375fec162b0e9f80ab347256e125156b69a45bb4ae59c11e",
    "graphSHA256": "46a1295d0ac688b7d723b652602f37b57ee48ada319b6894c0e1951632be8cdf",
    "historicalSourceSHA256": "b01a0c60201facb653777413ac2f7d8c24ce13150969969d234ca0574f6dfdc5",
    "sourceSHA256": "e7ca1c0a46b16807bf795f7b2a0ce84c6f00b15700d8235c019ae6a29012ee27",
    "branches": 28, "bottomHeaders": 28,
    "plates": 56
  }
]
describe('ColdPlatePair pure physical ownership', () => {
  it.each(parameterCases.map((c,i)=>({...c,index:i})))('matches original producer float bits: $name', c => {
    const pairBefore=tagged(c.pair), fluidBefore=tagged(c.fluid)
    const result=coldPlatePairKernel.parameters(c.pair,c.fluid)
    expect(tagged(result)).toEqual(originalParameterResults[c.index])
    expect(Object.keys(result)).toEqual(['dp_a','dp_b','UA_W_per_K','C_solid_J_per_K','V_fluid_m3','Q_W'])
    expect(tagged(c.pair)).toEqual(pairBefore)
    expect(tagged(c.fluid)).toEqual(fluidBefore)
  })
  it('hydrates the real physical pair with unchanged source path and parent identity', () => {
    const p=project(inputPaths[0]!).project
    const before=JSON.stringify(p)
    const entity=new DomainProject(p).allEntities().find(e=>e.type==='coldPlatePair')
    expect(entity).toBeInstanceOf(ColdPlatePairEntity)
    const pair=entity as ColdPlatePairEntity
    expect(pair.identity.path).toBe('/constructions/racks/m_2/constructions/servers/e_2/liquidCooling/cpCuPairs/0')
    expect(pair.identity.parentPath).toBe('/constructions/racks/m_2/constructions/servers/e_2')
    expect(pair.identity.parentUid).toBe('e_2')
    expect(pair.physicalParameters({rho:991.23})).toEqual(coldPlatePairKernel.parameters(pair.record,{rho:991.23}))
    const physics=pair.physics({rho:991.23})
    expect(physics).toMatchObject({type:'coldPlatePair',edgeType:'cold_plate',sourcePath:pair.identity.path,modelUid:pair.uid,parentUid:'e_2'})
    expect(physics.params).toEqual(pair.physicalParameters({rho:991.23}))
    expect(physics.declarations).toEqual({cfd:pair.record.cfd,physics:pair.record.physics,cooling:pair.record.cooling,liquidCooling:pair.record.liquidCooling})
    expect(JSON.stringify(p)).toBe(before)
    expect(JSON.stringify(pair.toRecord())).toBe(JSON.stringify(pair.record))
  })
  it('returns fresh parameter products and permits default fluid without mutating a record', () => {
    const pair={Q_W:350}
    const first=coldPlatePairKernel.parameters(pair)
    const second=coldPlatePairKernel.parameters(pair,{})
    expect(first).toEqual(second)
    expect(first).not.toBe(second)
    first.dp_a=0
    expect(coldPlatePairKernel.parameters(pair).dp_a).toBe(second.dp_a)
    expect(Object.isFrozen(coldPlatePairKernel)).toBe(true)
  })
  it.each(originalInputs)('preserves entire actual graph and source output: $path', input => {
    const p=project(input.path)
    expect(sha(p.raw)).toBe(input.inputSHA256)
    const before=JSON.stringify(p.project)
    const graph=compileDynamicLiquidGraph(p.project,{duration_s:10,dt_s:1})
    const saved=compileSavedExperiment(p.project)
    expect(assertPhysicalManifoldBranches(p.project,graph)).toEqual({branches:input.branches,bottomHeaders:input.bottomHeaders})
    expect(assertPhysicalManifoldBranches(p.project,saved.liquidrun as unknown as DynamicLiquidGraph))
      .toEqual({branches:input.branches,bottomHeaders:input.bottomHeaders})
    // The physical oracle must reject the former fixed-cell shortcut, altered bore,
    // and bypassing/removing the real bottom manifold transport segment.
    const spokeIndex = graph.edges.findIndex(edge => edge.id.includes('_spoke_') && Number(edge.params.n_cells) > 2)
    expect(spokeIndex).toBeGreaterThanOrEqual(0)
    for (const corrupt of [
      (candidate: DynamicLiquidGraph) => { candidate.edges[spokeIndex]!.params.n_cells = 2 },
      (candidate: DynamicLiquidGraph) => { candidate.edges[spokeIndex]!.params.D_m = 0.001 },
    ]) {
      const candidate = structuredClone(graph); corrupt(candidate)
      expect(() => assertPhysicalManifoldBranches(p.project, candidate)).toThrow()
    }
    if (input.bottomHeaders) {
      const bypass = structuredClone(graph)
      bypass.edges[spokeIndex]!.to = bypass.edges[spokeIndex]!.to.replace(/_bottom$/, '')
      expect(() => assertPhysicalManifoldBranches(p.project, bypass)).toThrow()
      const missingHeader = structuredClone(graph)
      missingHeader.edges = missingHeader.edges.filter(edge => !edge.id.endsWith('_bottom_header'))
      expect(() => assertPhysicalManifoldBranches(p.project, missingHeader)).toThrow()
    }
    expect(saved.liquidrun.schemaVersion).toBe(3)
    expect(saved.liquidrun).not.toHaveProperty('manufactured_benchmark')
    if (!('analysis' in saved.liquidrun)) throw new Error('Expected a saved DAE analysis manifest')
    expect(saved.liquidrun.analysis).toEqual({mode:'steady',initialization:'steady'})
    expect(sha(JSON.stringify(graph))).toBe(input.graphSHA256)
    expect(sha(JSON.stringify(saved))).toBe(input.sourceSHA256)
    expect(graph.edges.filter(e=>e.type==='cold_plate')).toHaveLength(input.plates)
    expect(JSON.stringify(p.project)).toBe(before)
  })
  it('keeps classic and CommonJS adapters on the same pure kernel and preserves loader order', () => {
    const source=readFileSync(resolve(root,'src/frontend/shared/js/cold_plate_pair_parameters.js'),'utf8')
    const browser=vm.createContext({window:{}})
    vm.runInContext(source,browser)
    const commonjs=vm.createContext({module:{exports:{}}})
    vm.runInContext(source,commonjs)
    for (let i=0;i<parameterCases.length;i++) {
      const c=parameterCases[i]!
      expect(tagged(browser.window.coldPlatePairParameters.parameters(c.pair,c.fluid))).toEqual(originalParameterResults[i])
      expect(tagged(commonjs.module.exports.parameters(c.pair,c.fluid))).toEqual(originalParameterResults[i])
    }
    const html=readFileSync(resolve(root,'src/frontend/web/STL_generate.html'),'utf8')
    const classicKernel=html.indexOf('src="js/cold_plate_pair_parameters.js"')
    const classicCompiler=html.indexOf('src="js/liquid_graphgen.js"')
    expect(classicKernel).toBeGreaterThanOrEqual(0)
    expect(classicCompiler).toBeGreaterThanOrEqual(0)
    expect(classicKernel).toBeLessThan(classicCompiler)
    const loader=readFileSync(resolve(root,'src/frontend/react/src/legacy/liquidGraphgen.ts'),'utf8')
    const moduleKernel=loader.indexOf("import { coldPlatePairKernel } from '../domain/coldPlatePairKernel'")
    const moduleCompiler=loader.indexOf("import '../../../shared/js/liquid_graphgen.js'")
    expect(moduleKernel).toBeGreaterThanOrEqual(0)
    expect(moduleCompiler).toBeGreaterThanOrEqual(0)
    expect(moduleKernel).toBeLessThan(moduleCompiler)
    expect(source).not.toMatch(/\b(?:THREE|USD|state|document)\b/)
  })
  it('compares saved mixed trajectory byte-for-byte against the pre-edit capture when the owned proof is supplied', () => {
    if (!evidence) return
    const before=JSON.parse(readFileSync(resolve(evidence,'baseline/original-produced-identity.json'),'utf8'))
    const identity=before.inputs[2]
    const p=project(identity.path)
    expect(sha(p.raw)).toBe(identity.inputSHA256)
    const graph=JSON.stringify(compileDynamicLiquidGraph(p.project,{duration_s:10,dt_s:1}))
    const source=JSON.stringify(compileSavedExperiment(p.project))
    expect(graph+'\n').toBe(readFileSync(resolve(evidence,'baseline/original-2-graph.json'),'utf8'))
    expect(source+'\n').toBe(readFileSync(resolve(evidence,'baseline/original-2-source.json'),'utf8'))
    writeFileSync(resolve(evidence,'after-mixed-comparison.json'),JSON.stringify({inputSHA256:sha(p.raw),graphSHA256:sha(graph),sourceSHA256:sha(source),completeGraphAndSourceExact:true},null,2)+'\n')
  })
})
