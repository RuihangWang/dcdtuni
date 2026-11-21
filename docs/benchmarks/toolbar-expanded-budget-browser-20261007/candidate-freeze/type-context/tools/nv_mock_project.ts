/** Reproducible project fixture. Only three inventory counts come from NV's point list. */
import { geometryPure } from '../src/frontend/react/src/legacy/geometryBuilder'

type Bag=Record<string,any>
const copy=<T>(value:T):T=>JSON.parse(JSON.stringify(value))
export function buildNvMockProject(template:Bag,basePack:Bag,hashes:{templateSha256:string;pointListSha256:string}):Bag {
  const p=copy(template)
  const rackTemplate=Object.values(template.constructions.racks).find((x:any)=>x.cooling?.type==='liquid') as Bag
  const cduTemplate=Object.values(template.constructions.cdus)[0] as Bag
  const pipeTemplate=Object.values(template.constructions.pipeSegments)[0] as Bag
  if(!rackTemplate||!cduTemplate||!pipeTemplate)throw new Error('Template lacks existing CDU/rack/pipe constructions')
  p.uid='proj_nv_pek02_mock_v1';p.name='NV_PEK02_MOCK'
  p.meta={...p.meta,projectName:'NV_PEK02_MOCK',casePath:'',description:'MOCK / 假设场景。仅2 CDU、34能量阀、68隔离阀数量来自NV参考点表；不是现场拓扑、参数或校准结果。'}
  p.meta.provenance={
    fixture_revision:'nv-pek02-mock-1',source_project:'case/Shinaide_Liquid/Shinaide_hybrid_cpu.cfdproj',template_sha256:hashes.templateSha256,
    asset_count_sources:[['CDU',2,'C3'],['energy_valve',34,'C31'],['isolation_valve',68,'C40']].map(([asset,count,cell])=>({asset,count,cell,sheet:'Liquid Cooling',file:'Pek02_BMS_PointList_Interface_V3.0_20260916_CQB.xlsx',sha256:hashes.pointListSha256,meaning:'planned asset count only; no topology, installed state or authorization inferred'})),
    topology:{source:'mock',reference:'tools/nv_mock_project.ts',description:'Two independent CDU zones, 17 racks each, no cross-tie/standby. One equivalent pump and two-fluid-volume HX per CDU; not a model of the actual internal three-pump arrangement. One energy valve + supply isolation + return isolation per rack; isolation is a zero-volume series element co-located with its branch fitting.'},
    geometry:{source:'mock',description:'22 x 14 x 4 m assumed room; generic construction shapes cloned from own Shinaide fixture; 0.85 m assumed rack pitch. All line lengths come from the common geometry compiler, not NV CAD.'},
    thermal:{source:'mock',rack_load_W:10000,total_load_W:340000,step_time_s:5,step_factor:1.2,description:'One equivalent liquid load/thermal mass per rack; not an individual GPU/cold-plate identification. Uniform 10 kW/rack chosen only for solver tests.'},
    missing:['actual CDU model','as-built P&ID','rack/CDU allocation','equipment curves','primary-side dynamics','synchronized telemetry','controller logic','electrical-to-liquid heat split','independent CDU/pump electricity measurement'],
  }
  p.geometry={...p.geometry,plane:[[0,0],[22,0],[22,14],[0,14]],height:4}
  for(const key of Object.keys(p.constructions))p.constructions[key]=['raisedFloor','falseCeiling'].includes(key)?null:{}
  p.instrumentation={probes:{},measurements:{}}
  const pack=copy(basePack)
  pack.id='nv-pek02-solver-mock';pack.revision='mock-1-dae';pack.source='mock'
  pack.description='Explicit synthetic 2-CDU/34-rack solver fixture. No NVIDIA/Vertiv curves or telemetry. HX is two mixed fluid volumes; only inventory counts are source-backed.'
  Object.assign(pack.profiles['pump-demo'].params,{H0_m:12,a_coef:300000,rpm_ratio:1})
  Object.assign(pack.profiles['valve-demo'].params,{D_m:.025,Kv_max:12,opening:1,characteristic:'linear'})
  Object.assign(pack.profiles['hx-demo'].params,{dp_a:120,dp_b:1000,UA_W_per_K:45000})
  delete pack.profiles['hx-demo'].params.T_secondary_K;delete pack.profiles['hx-demo'].units.T_secondary_K
  p.models.materialModels.fluids={mock_water:{type:'custom',rho:997,cp:4182,mu:.00089,k:.606}}
  p.models.coolingModels.pipes={nv_pipe_50mm:{name:'MOCK 50 mm header/spoke',diameter:.05,roughness:1.5e-6}}
  p.runDefaults.liquid={enabled:true,inheritResults:false,solver:{...p.runDefaults.liquid.solver,device:'cpu',dt_s:1,N_t:11},experiment:{
    duration_s:10,dt_s:1,settings:{mode:'manual',pumpId:'',measurementId:'',setpoint:45,kp:.03,ki:.002,bias:.8,loadStep:true,stepTime:5,loadFactor:1.2},parameterPack:pack,
    dae:{equation_system:'incompressible_thermal_dae',analysis:{mode:'transient',initialization:'steady'},newton:{max_iterations:40,atol:1e-10,rtol:1e-10,max_backtracks:18},primary_circuits:[]},
  }}
  // The air settings remain in the project but this fixture never invokes CFD.
  p.runDefaults.meshConfig={...p.runDefaults.meshConfig,meshProcess:1}
  p.runDefaults.simulationConfig={...p.runDefaults.simulationConfig,solveProcess:1}
  for(let zone=1;zone<=2;zone++) {
    const zid=String(zone).padStart(2,'0'),z=zone===1?2:7
    const cdu=copy(cduTemplate)
    cdu.uid=`nv_cdu_${zid}`;cdu.meta={...cdu.meta,name:`MOCK CDU ${zid}`,racks:[],description:'Generic assumed liquid-liquid CDU with one equivalent pump; actual NV model/internal topology unknown.'}
    cdu.geometry.location={x:1,y:0,z};cdu.geometry.quaternion={x:0,y:0,z:0,w:1};cdu.geometry.scale={x:1,y:1,z:1}
    cdu.cooling={...cdu.cooling,advancedMode:false,supplyTemperatureC:30,supplyPressureKpa:250}
    delete cdu.cooling.facilitySupplyTemperatureC
    p.constructions.cdus[cdu.uid]=cdu
    p.runDefaults.liquid.experiment.dae.primary_circuits.push({edge_id:`${cdu.uid}_hx`,flow_kg_s:5,inlet_K:293.15,cp_J_kgK:4182,rho_kg_m3:997,primary_volume_m3:.05,secondary_volume_m3:.05,source:'mock',source_reference:'tools/nv_mock_project.ts: assumed constant 5 kg/s at 20 C; 50 L mixed primary and secondary volumes. Not vendor data, NV telemetry or calibration.'})
    for(const role of ['supply','return']) {
      const pipe=copy(pipeTemplate)
      pipe.uid=`nv_${role}_${zid}`
      const frame=(geometryPure() as any).liquidEquipmentPortFrame({...cdu,kind:'cdu'},role,'top')
      if(!frame)throw new Error('The shared equipment geometry did not resolve CDU ports')
      const a={x:frame.position.x,y:frame.position.y,z:frame.position.z},b={x:18,y:a.y,z:a.z}
      pipe.meta={...pipe.meta,name:`MOCK ${role} header ${zid}`,enabled:true,description:'Assumed 50 mm straight header. Branch spoke diameters also 50 mm under the current shared compiler; rack internal manifold diameter 25 mm.'}
      pipe.geometry={...pipe.geometry,location:{x:0,y:0,z:0},quaternion:{x:0,y:0,z:0,w:1},scale:{x:1,y:1,z:1},waypoints:[a,b],diameterM:.05}
      pipe.cooling={loopRole:role,connection:{startPortId:`${cdu.uid}.${role}`,endPortId:'',blindEnds:{start:false,end:true},junctions:[]},valves:[]}
      p.constructions.pipeSegments[pipe.uid]=pipe
    }
    for(let j=0;j<17;j++) {
      const n=(zone-1)*17+j+1,id=String(n).padStart(2,'0'),rack=copy(rackTemplate)
      rack.uid=`nv_rack_${id}`;rack.meta={...rack.meta,name:`MOCK rack ${id}`,description:'Single equivalent 10 kW liquid thermal load; not an identified GPU/cold-plate network.',display:{opacity:1,showAnchorMarker:false}}
      rack.geometry.location={x:3+j*.85,y:0,z:z+2.2};rack.geometry.quaternion={x:0,y:0,z:0,w:1};rack.geometry.scale={x:1,y:1,z:1}
      const server=copy(Object.values(rackTemplate.constructions.servers)[0]) as Bag
      server.uid=`nv_equivalent_${id}`;server.meta={...server.meta,name:`MOCK equivalent rack load ${id}`};server.geometry={slotPosition:3,slotOccupation:4}
      server.cooling={...server.cooling,power:10000}
      const cp={uid:`nv_heat_${id}`,name:`MOCK equivalent ${id}`,Q_W:10000,R_th:.0004,dp_rated_kPa:20,Q_rated_LPM:18,C_solid:50000,V_fluid_mL:2000}
      server.constructions={chips:{},coldPlates:{[cp.uid]:cp}};server.liquidCooling={cpCuPairs:[copy(cp)]}
      rack.constructions={...rack.constructions,servers:{[server.uid]:server},rdhx:null,serverConflicts:[]}
      p.constructions.racks[rack.uid]=rack;cdu.meta.racks.push(rack.meta.name)
      for(const role of ['supply','return']) {
        const pipe=p.constructions.pipeSegments[`nv_${role}_${zid}`]
        const a=pipe.geometry.waypoints[0],position={x:rack.geometry.location.x+.3,y:a.y,z:a.z}
        const fittingId=role==='supply'?`nv_energy_${id}`:`nv_return_tee_${id}`
        pipe.cooling.valves.push({id:fittingId,type:role==='supply'?'flow_control':'tee_junction',name:role==='supply'?`MOCK energy valve ${id}`:`MOCK return tee ${id}`,side:'above',position,opening:1,connectedRackId:rack.uid,connectedManifoldPortId:`${rack.uid}.manifold_${role}_top`,meta:{assetKind:role==='supply'?'energy_valve':'assumed_tee',source:'mock',source_reference:role==='supply'?'planned count: Liquid Cooling!C31; device assignment and properties are assumed':'assumed topology, not a counted NV valve'}})
        pipe.cooling.valves.push({id:`nv_iso_${role}_${id}`,type:'isolation',name:`MOCK ${role} isolation ${id}`,side:'above',position:copy(position),opening:1,seriesWithValveId:fittingId,meta:{assetKind:'isolation_valve',source:'mock',source_reference:'planned total count: Liquid Cooling!C40; placement/series assignment assumed; fixed open for this test'}})
      }
    }
  }
  p.constructions.liquidFlowNetworks={nv_mock_network:{uid:'nv_mock_network',geometry:{},meta:{name:'MOCK independent zones',enabled:true},autoGenerated:true,mainPipeSpec:'nv_pipe_50mm',branchPipeSpec:null}}
  return p
}
