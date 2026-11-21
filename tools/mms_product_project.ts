/** Reproducible MMS verification fixture: 1 CDU + 1 rack + 1 cold plate.
 *
 *  Purpose: the compiled PRODUCT chain (.cfdproj -> strict .liquidrun v3 ->
 *  graph compile -> PyTorch Newton DAE) must reproduce the closed-form lumped
 *  thermal response to load steps and supply-temperature steps (derivation:
 *  docs/developer/liquid_dae_mms_product_derivation.md). All parameters are
 *  synthetic and chosen for analytic tractability; nothing here is NV, vendor
 *  or site data. The fixed primary supply temperature (290 K) is the scenario
 *  boundary the user specified; the acceptance script varies it by schedule. */
import { geometryPure } from '../src/frontend/react/src/legacy/geometryBuilder'

type Bag=Record<string,any>
const copy=<T>(value:T):T=>JSON.parse(JSON.stringify(value))

/** Analytic targets (W/K, J/K, m3, kg/s, K). The acceptance script reads the
 *  ACTUAL compiled manifest values and instantiates the closed form from them
 *  (sha-locked); these are the design intent, not a second source of truth. */
export const MMS_TARGETS = {
  ua_cp_W_per_K: 100, c_solid_J_per_K: 1000, v_fluid_m3: 0.002,
  ua_hx_W_per_K: 500, q_baseline_W: 100,
  primary_flow_kg_s: 0.1, primary_inlet_K: 290,
  primary_volume_m3: 0.001, secondary_volume_m3: 0.002,
  rho_kg_m3: 997, cp_J_kgK: 4182, dt_s: 1, n_t: 61,
}

export function buildMmsProductProject(template:Bag,basePack:Bag,hashes:{templateSha256:string}):Bag {
  const p=copy(template)
  const rackTemplate=Object.values(template.constructions.racks).find((x:any)=>x.cooling?.type==='liquid') as Bag
  const cduTemplate=Object.values(template.constructions.cdus)[0] as Bag
  const pipeTemplate=Object.values(template.constructions.pipeSegments)[0] as Bag
  if(!rackTemplate||!cduTemplate||!pipeTemplate)throw new Error('Template lacks existing CDU/rack/pipe constructions')
  p.uid='proj_mms_product_v1';p.name='MMS_PRODUCT'
  p.meta={...p.meta,projectName:'MMS_PRODUCT',casePath:'',description:'Analytical verification fixture (MMS). 1 CDU + 1 rack + 1 equivalent cold plate; synthetic parameters chosen so the lumped thermal network admits a closed-form step response. Not NV/vendor/site data; not a model of any real facility.'}
  p.meta.provenance={
    fixture_revision:'mms-product-1',source_project:'case/verification/shinaide-legacy-projects/Shinaide_hybrid_cpu.json',template_sha256:hashes.templateSha256,
    topology:{source:'mock',reference:'tools/mms_product_project.ts',description:'Single CDU zone, single rack with one equivalent server cold plate; one energy valve + one return tee + supply/return isolation valves, all fixed open. One equivalent pump and a two-fluid-volume HX (no vendor internal topology).'},
    geometry:{source:'mock',description:'6 x 4 x 3 m minimal room; generic construction shapes cloned from own Shinaide fixture; line lengths from the common geometry compiler.'},
    thermal:{source:'mock',description:'UA_cp=100 W/K, C_solid=1000 J/K, V_fluid=2 L, UA_hx=500 W/K, baseline load 100 W, primary 0.1 kg/s at 290 K FIXED supply, primary/secondary mixed volumes 1 L / 2 L, water rho 997 / cp 4182. Chosen for analytic tractability (lumped linear thermal ODE with closed-form step response); secondary flow comes from the hydraulic solve and is asserted constant by the acceptance script.'},
    missing:['everything real: this fixture exists only to verify the transient DAE solver against a manufactured closed form'],
  }
  p.geometry={...p.geometry,plane:[[0,0],[6,0],[6,4],[0,4]],height:3}
  for(const key of Object.keys(p.constructions))p.constructions[key]=['raisedFloor','falseCeiling'].includes(key)?null:{}
  p.instrumentation={probes:{},measurements:{}}
  const pack=copy(basePack)
  pack.id='mms-product-analytic';pack.revision='mms-1';pack.source='mock'
  pack.description='Synthetic analytic-tractability parameters for the MMS product case. No vendor or site data. See docs/developer/liquid_dae_mms_product_derivation.md.'
  Object.assign(pack.profiles['pump-demo'].params,{H0_m:12,a_coef:3e8,rpm_ratio:1})
  Object.assign(pack.profiles['valve-demo'].params,{D_m:.025,Kv_max:12,opening:1,characteristic:'linear'})
  Object.assign(pack.profiles['hx-demo'].params,{dp_a:120,dp_b:1000,UA_W_per_K:500})
  delete pack.profiles['hx-demo'].params.T_secondary_K;delete pack.profiles['hx-demo'].units.T_secondary_K
  Object.assign(pack.profiles['rack-equivalent'].params,{UA_W_per_K:100,C_solid_J_per_K:1000,V_fluid_m3:0.002})
  Object.assign(pack.profiles['pipe-25mm'].params,{n_cells:1})
  Object.assign(pack.profiles['water'].params,{rho_kg_m3:997,cp_J_kgK:4182})
  p.models.materialModels.fluids={mms_water:{type:'custom',rho:997,cp:4182,mu:.00089,k:.606}}
  p.models.coolingModels.pipes={mms_pipe_25mm:{name:'MMS 25 mm pipe',diameter:.025,roughness:1.5e-6}}
  p.runDefaults.liquid={enabled:true,inheritResults:false,solver:{...p.runDefaults.liquid.solver,device:'cpu',dt_s:1,N_t:61},experiment:{
    duration_s:60,dt_s:1,settings:{mode:'manual',pumpId:'',measurementId:'',setpoint:45,kp:.03,ki:.002,bias:.8,loadStep:false,stepTime:30,loadFactor:1},parameterPack:pack,
    dae:{equation_system:'incompressible_thermal_dae',analysis:{mode:'transient',initialization:'steady'},newton:{max_iterations:40,atol:1e-10,rtol:1e-10,max_backtracks:18},primary_circuits:[]},
  }}
  p.runDefaults.meshConfig={...p.runDefaults.meshConfig,meshProcess:1}
  p.runDefaults.simulationConfig={...p.runDefaults.simulationConfig,solveProcess:1}
  const cdu=copy(cduTemplate)
  cdu.uid='mms_cdu';cdu.meta={...cdu.meta,name:'MMS CDU',racks:[],description:'Generic single-pump two-volume CDU; synthetic parameters, no vendor model.'}
  cdu.geometry.location={x:1,y:0,z:2};cdu.geometry.quaternion={x:0,y:0,z:0,w:1};cdu.geometry.scale={x:1,y:1,z:1}
  cdu.cooling={...cdu.cooling,advancedMode:false,supplyTemperatureC:30,supplyPressureKpa:250}
  delete cdu.cooling.facilitySupplyTemperatureC
  p.constructions.cdus[cdu.uid]=cdu
  p.runDefaults.liquid.experiment.dae.primary_circuits.push({edge_id:'mms_cdu_hx',flow_kg_s:0.1,inlet_K:290,cp_J_kgK:4182,rho_kg_m3:997,primary_volume_m3:0.001,secondary_volume_m3:0.002,source:'mock',source_reference:'tools/mms_product_project.ts: fixed 0.1 kg/s facility supply at 290 K (the FIXED supply-temperature boundary of the verification scenario); 1 L primary / 2 L secondary mixed volumes. Synthetic, chosen for analytic tractability.'})
  for(const role of ['supply','return']) {
    const pipe=copy(pipeTemplate)
    pipe.uid=`mms_${role}`
    const frame=(geometryPure() as any).liquidEquipmentPortFrame({...cdu,kind:'cdu'},role,'top')
    if(!frame)throw new Error('The shared equipment geometry did not resolve CDU ports')
    const a={x:frame.position.x,y:frame.position.y,z:frame.position.z},b={x:5,y:a.y,z:a.z}
    pipe.meta={...pipe.meta,name:`MMS ${role} header`,enabled:true,description:'Assumed 25 mm straight header, single cell per branch under the MMS pack.'}
    pipe.geometry={...pipe.geometry,location:{x:0,y:0,z:0},quaternion:{x:0,y:0,z:0,w:1},scale:{x:1,y:1,z:1},waypoints:[a,b],diameterM:.025}
    pipe.cooling={loopRole:role,connection:{startPortId:`mms_cdu.${role}`,endPortId:'',blindEnds:{start:false,end:true},junctions:[]},valves:[]}
    p.constructions.pipeSegments[pipe.uid]=pipe
  }
  const rack=copy(rackTemplate)
  rack.uid='mms_rack';rack.meta={...rack.meta,name:'MMS rack',description:'Single equivalent 100 W liquid load for analytic verification; not an identified GPU/cold-plate network.',display:{opacity:1,showAnchorMarker:false}}
  rack.geometry.location={x:3,y:0,z:3.5};rack.geometry.quaternion={x:0,y:0,z:0,w:1};rack.geometry.scale={x:1,y:1,z:1}
  const server=copy(Object.values(rackTemplate.constructions.servers)[0]) as Bag
  server.uid='mms_server';server.meta={...server.meta,name:'MMS equivalent load'};server.geometry={slotPosition:3,slotOccupation:4}
  server.cooling={...server.cooling,power:100}
  const cp={uid:'mms_heat',name:'MMS cold plate',Q_W:100,R_th:0.01,dp_rated_kPa:20,Q_rated_LPM:12,C_solid:1000,V_fluid_mL:2000}
  server.constructions={chips:{},coldPlates:{[cp.uid]:cp}};server.liquidCooling={cpCuPairs:[copy(cp)]}
  rack.constructions={...rack.constructions,servers:{[server.uid]:server},rdhx:null,serverConflicts:[]}
  p.constructions.racks[rack.uid]=rack;cdu.meta.racks.push(rack.meta.name)
  for(const role of ['supply','return']) {
    const pipe=p.constructions.pipeSegments[`mms_${role}`]
    const a=pipe.geometry.waypoints[0],position={x:rack.geometry.location.x+.3,y:a.y,z:a.z}
    const fittingId=role==='supply'?'mms_energy_valve':'mms_return_tee'
    pipe.cooling.valves.push({id:fittingId,type:role==='supply'?'flow_control':'tee_junction',name:`MMS ${role} fitting`,side:'above',position,opening:1,connectedRackId:rack.uid,connectedManifoldPortId:`${rack.uid}.manifold_${role}_top`,meta:{assetKind:role==='supply'?'energy_valve':'assumed_tee',source:'mock',source_reference:'single analytic-verification branch; fixed open'}})
    pipe.cooling.valves.push({id:`mms_iso_${role}`,type:'isolation',name:`MMS ${role} isolation`,side:'above',position:copy(position),opening:1,seriesWithValveId:fittingId,meta:{assetKind:'isolation_valve',source:'mock',source_reference:'fixed open for verification'}})
  }
  p.constructions.liquidFlowNetworks={mms_network:{uid:'mms_network',geometry:{},meta:{name:'MMS single zone',enabled:true},autoGenerated:true,mainPipeSpec:'mms_pipe_25mm',branchPipeSpec:null}}
  return p
}
