import {describe,it,expect,vi,afterEach} from 'vitest'
import {readFileSync} from 'node:fs'
import {resolve} from 'node:path'
import {adoptSavedServerPowerForHybrid,hasHybridFanServer} from '../../src/liquid/hybridFanHeatBudget'
import '../../src/legacy/geometryBuilder'
import {createCfdrunBuild,type CfdrunContext} from '../../src/legacy/cfdrunBuild'
import {allModels} from '../../src/schema/cfdproj'
import {reviveKinds,schema} from '../../src/legacy/cfdprojModules'
import type {CfdProject} from '../../src/schema/cfdproj'

describe('real Shinaide saved hybrid inputs',()=>{
  afterEach(()=>vi.unstubAllGlobals())
  const raw=JSON.parse(readFileSync(resolve(process.cwd(),'../../../case/verification/shinaide-legacy-projects/Shinaide_liquid.json'),'utf8')) as CfdProject
  it('adopts saved power explicitly while retaining each UID, liquid pair and original flow',()=>{
    const p=reviveKinds(structuredClone(raw))
    const before=JSON.stringify(p)
    const b=adoptSavedServerPowerForHybrid(p)
    expect(hasHybridFanServer(p)).toBe(true)
    expect(JSON.stringify(p)).toBe(before)
    expect(b.source).toBe('mock')
    expect(b.air_transfer).toBe('fan_enthalpy')
    expect(Object.keys(b.servers)).toHaveLength(56)
    expect(Object.values(b.servers).reduce((sum,row)=>sum+row.total_it_power_W,0)).toBe(84000)
    expect(schema().validateHybridHeatBudget(b,p)).toEqual([])
  })
  it('both actual shared translations retain all original per-UID air intents and liquid configs',()=>{
    const p=reviveKinds(structuredClone(raw))
    const models=allModels(p).map(item=>item.model)
    vi.stubGlobal('allModels',()=>models)
    const api=createCfdrunBuild({state:p,globalGroup:{children:[]},
      LENGTH_UNITS:{m:{scale:1,label:'m',step:.0001,precision:4}},
      _MESH_MODES:['bodyFitted','cartesianLocalized','structured']} as unknown as CfdrunContext)
    type Carrier={type:string;sourceType:string;sourceUid:string;sourceRackUid:string;
      liquidCooling:unknown;physics:{power:number;volumeFlowRate:number};
      faces:Record<string,{solid:string|null;wallSolid:string|null;patches:{solid:string;patchId:string}[]}>}
    const racks=(api.buildCfdrunJson().racks as Record<string,{servers:Carrier[]}>)
    const carriers=Object.values(racks).flatMap(r=>r.servers)
    expect(carriers).toHaveLength(56)
    const bySource:Record<string,{patchName:string;solidName:string}>={}
    for(const c of carriers) {
      expect(c.type).toBe('fanServer');expect(c.sourceType).toBe('liquidServer')
      const rack=p.constructions?.racks?.[c.sourceRackUid]
      const srv=((rack?.constructions as {servers:Record<string,{cooling:{power:number;volumeFlowRate:number};liquidCooling:unknown}>}).servers)[c.sourceUid]!
      expect(c.liquidCooling).toEqual(srv.liquidCooling)
      expect(c.physics.power).toBe(srv.cooling.power)
      expect(c.physics.volumeFlowRate).toBe(srv.cooling.volumeFlowRate)
      for(const [face,d] of Object.entries(c.faces)) {
        if(d.solid)bySource[c.sourceUid+'|'+face+'|__face__']={patchName:d.solid,solidName:d.solid}
        for(const patch of d.patches)bySource[c.sourceUid+'|'+face+'|'+patch.patchId]={patchName:patch.solid,solidName:patch.solid}
        if(d.wallSolid)bySource[c.sourceUid+'|'+face+'|__wall__']={patchName:d.wallSolid,solidName:d.wallSolid}
      }
    }
    type Binding={bcType:string;objectPath:string;parameters:{flowRateM3s:number;powerW:number}}
    const mod=(window as unknown as {cfdrunExport:{translateRackServers(p:unknown,c:unknown):{boundaryBindings:Record<string,Binding>}}}).cfdrunExport
    const bound=Object.values(mod.translateRackServers(p,{bySource}).boundaryBindings)
    for(const c of carriers)for(const bc of ['fan_inlet','fan_outlet']) {
      const faces=bound.filter(b=>b.bcType===bc&&b.objectPath==='constructions.racks.'+c.sourceRackUid+'.servers.'+c.sourceUid)
      expect(faces.length).toBeGreaterThan(0)
      expect(faces.reduce((sum,b)=>sum+b.parameters.flowRateM3s,0)).toBeCloseTo(c.physics.volumeFlowRate,12)
      expect(faces.reduce((sum,b)=>sum+b.parameters.powerW,0)).toBeCloseTo(c.physics.power,9)
    }
    expect(bound.some(b=>b.bcType==='nonfan_fixed_temp')).toBe(false)
  })

})
