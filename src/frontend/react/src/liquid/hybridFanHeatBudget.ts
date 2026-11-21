import type { CfdProject, Construction, HybridHeatBudgetV2 } from '../schema/cfdproj'
import { DomainProject } from '../domain/DomainProject'
import { projectAtStart } from '../transient/model'
import { cfdrunExport } from '../legacy/cfdrunBuild'

type Server = {uid?:string;type?:string;cooling?:{power?:number;volumeFlowRate?:number;powerMode?:string;serverBCMode?:string}}
/** Explicit mock adoption, called by the creation UI; never a load-time migration. */
export function adoptSavedServerPowerForHybrid(project:CfdProject):HybridHeatBudgetV2 {
  const servers:HybridHeatBudgetV2['servers']={}
  for(const entity of new DomainProject(projectAtStart(project)).topLevel(['racks'])) {
    const rack=entity.record as unknown as Construction
    const nested=(rack.constructions as {servers?:Record<string,Server>}|undefined)?.servers??{}
    for(const [uid,server] of Object.entries(nested)) {
      if(server.type!=='liquidServer')continue
      const c=server.cooling??{}
      if(server.uid!==uid||!rack.uid||!(typeof c.power==='number'&&Number.isFinite(c.power)&&c.power>0)||
         !(typeof c.volumeFlowRate==='number'&&Number.isFinite(c.volumeFlowRate)&&c.volumeFlowRate>0)||
         c.powerMode==='unpowered'||(c.serverBCMode??'powerAndFlow')!=='powerAndFlow')
        throw new Error('Hybrid mock adoption needs positive saved power/airflow and powerAndFlow: '+rack.uid+'/'+uid)
      servers[rack.uid+'/'+uid]={total_it_power_W:c.power}
    }
  }
  if(!Object.keys(servers).length)throw new Error('No liquidServer with a saved fan flow')
  return {version:2,source:'mock',
    source_reference:'Explicit mock adoption of saved cooling.power as total IT; project '+project.uid+'; prior content '+cfdrunExport().projectContentHashSync(project),
    load_mapping:'server_power_times_shared_heat_multiplier',air_transfer:'fan_enthalpy',servers}
}

export function hasHybridFanServer(project:CfdProject):boolean {
  for(const entity of new DomainProject(projectAtStart(project)).topLevel(['racks'])) {
    const nested=(entity.record.constructions as {servers?:Record<string,Server>}|undefined)?.servers??{}
    if(Object.values(nested).some(s=>s.type==='liquidServer'&&s.cooling?.powerMode!=='unpowered'&&
       typeof s.cooling?.volumeFlowRate==='number'&&s.cooling.volumeFlowRate>0))return true
  }
  return false
}
