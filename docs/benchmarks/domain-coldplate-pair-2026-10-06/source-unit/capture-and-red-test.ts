import { createHash } from 'node:crypto'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { DomainProject } from '../src/domain/DomainProject'
import { ColdPlatePairEntity } from '../src/domain/BaseEntity'
import { compileDynamicLiquidGraph } from '../src/liquid/dynamicLiquidGraph'
import { compileSavedExperiment } from '../src/liquid/liquidExperiment'
import { readProjectFileText } from '../src/io/projectFiles'

const root = resolve(process.cwd(), '../../..')
const evidence = process.env.DCDTUNI_CP_EVIDENCE
const sha = (s: string | Buffer) => createHash('sha256').update(s).digest('hex')
const inputPaths = [
  'case/LiquidSingle/LiquidSingle.cfdproj',
  'case/Shinaide_Liquid/Shinaide_liquid.cfdproj',
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
describe('ColdPlatePair original producer baseline', () => {
  it('captures original graph/source and six parameter arithmetic before producer changes', () => {
    if (!evidence) return
    mkdirSync(evidence,{recursive:true})
    const source=readFileSync(resolve(root,'src/frontend/shared/js/liquid_graphgen.js'),'utf8')
    const start=source.indexOf('var cpQ_W')
    const end=source.indexOf('edges.push({',start)
    const paramsStart=source.indexOf('params: {',end)+'params: '.length
    const paramsEnd=source.indexOf('\n                        }',paramsStart)+26
    const body=source.slice(start,end)+'return '+source.slice(paramsStart,paramsEnd)+';'
    const producer=new Function('cp','fluid',body)
    writeFileSync(resolve(evidence,'original-six-parameter-body.js'),body)
    writeFileSync(resolve(evidence,'original-six-parameter-results.json'),JSON.stringify(parameterCases.map(c=>({...tagged(c) as object,result:tagged(producer(c.pair,c.fluid))})),null,2)+'\n')
    const identities=[]
    for (let i=0;i<inputPaths.length;i++) {
      const path=inputPaths[i]!
      const p=project(path)
      const before=JSON.stringify(p.project)
      const graph=compileDynamicLiquidGraph(p.project,{duration_s:10,dt_s:1})
      const saved=compileSavedExperiment(p.project)
      expect(JSON.stringify(p.project)).toBe(before)
      const graphRaw=JSON.stringify(graph)
      const sourceRaw=JSON.stringify(saved)
      writeFileSync(resolve(evidence,'original-'+i+'-graph.json'),graphRaw+'\n')
      writeFileSync(resolve(evidence,'original-'+i+'-source.json'),sourceRaw+'\n')
      identities.push({path,inputSHA256:sha(p.raw),graphSHA256:sha(graphRaw),sourceSHA256:sha(sourceRaw),plates:graph.edges.filter(e=>e.type==='cold_plate').length})
    }
    writeFileSync(resolve(evidence,'original-produced-identity.json'),JSON.stringify({producerSHA256:sha(source),inputs:identities},null,2)+'\n')
  })
  it('hydrates the physical ColdPlatePair method at the authoritative path', () => {
    const p=project(inputPaths[0]!).project
    const entity=new DomainProject(p).allEntities().find(e=>e.type==='coldPlatePair')
    expect(entity).toBeInstanceOf(ColdPlatePairEntity)
    expect(typeof (entity as unknown as {physicalParameters?:unknown}).physicalParameters).toBe('function')
  })
})
