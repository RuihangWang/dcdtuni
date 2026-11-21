"""Strict primitive safe ordinal withdrawal on actual CPU USD, no pixels."""
from pathlib import Path
import asyncio,copy,json,runpy,sys,importlib.util
T=Path(__file__).resolve().parents[1];base=runpy.run_path(str(T/'harness/cpu_usd_grid.py'));g,r,p,view,stage,api,Layout=[base[k]for k in('g','r','p','view','stage','api','ControlledOwnedLayout')];rows=[];MAX=9007199254740991
reference='--reference'in sys.argv
if reference:
 q=T/'before-ordinal/src/frontend/omniverse/cfd_demo/kit/extensions/dc.cfd_bridge/dc/cfd_bridge/grid_raster.py';spec=importlib.util.spec_from_file_location('controlled_bridge.grid_ordinal_reference',q);g=importlib.util.module_from_spec(spec);sys.modules[spec.name]=g;spec.loader.exec_module(g)
def put(v,key,value):
 q=v['backgroundGridRaster'];q['output'if key=='outputRevision'else'binding']['revision'if key=='outputRevision'else key]=value
async def run():
 cases=[('overflow-view','viewRevision',MAX+1),('overflow-output','outputRevision',MAX+1)]
 if not reference:
  for key in('viewRevision','outputRevision','sceneRevision'):
   for label,value in [('negative',-1),('huge',MAX*2+1),('float',3.0),('string','3'),('NaN',float('nan')),('infinite',float('inf')),('minus-infinite',float('-inf')),('bool-true',True),('bool-false',False),('null',None),('array',[3]),('object',{'value':3})]:cases.append((key+'-'+label,key,value))
 for label,key,value in cases:
  s=stage();a=api(s,texture=(1600,638),canvas=(1600,638));frame=[0];ctx={'projectUid':'CONTROLLED_PROJECT','projectContentSha256':'a'*64,'revision':1};c=g.GridRasterCoordinator(lambda _:dict(ctx),lambda _:p.sample_grid_viewport(a,s,r.CAMERA,output_layout=Layout()),lambda:frame[0])
  try:
   c.offer(s,view());frame[0]+=1;c.notify();await c._task;contract=c.carrier_contract(s);literal=s.GetRootLayer().ExportToString();before=(c._last_view,c._output_revision,c._serial,copy.deepcopy(c._output_observation));wrong=view(3);wrong['backgroundGridRaster']['output']['revision']=2;wrong['backgroundGridRaster']['output']['css'][0]=0;put(wrong,key,value)
   try:c.offer(s,wrong)
   except (ValueError,TypeError,g.GridRefused)as e:refusal=str(e)
   else:raise AssertionError('malformed ordinal accepted')
   after=(c._last_view,c._output_revision,c._serial,copy.deepcopy(c._output_observation));held=before==after and c.carrier_contract(s)is contract;assert s.GetRootLayer().ExportToString()==literal
   if reference:assert not held and c.carrier_contract(s)is None
   else:assert held,(label,before,after)
   recovery=view(4);recovery['backgroundGridRaster']['output']['revision']=3;recovered=True
   try:c.offer(s,recovery)
   except (ValueError,TypeError,g.GridRefused)as e:recovered=False;recovery_error=str(e)
   if recovered:frame[0]+=1;c.notify();await c._task;assert c.receipt['phase']=='rendered'
   assert recovered is(not reference),(label,recovered)
   rows.append({'case':label,'schemaRefusal':refusal,'beforeFloors':list(before[:3]),'afterFloors':list(after[:3]),'existingPermissionHeld':held,'boundedValid4Output3Recovered':recovered,'noClamping':True})
  finally:c.close()
 # Original legitimate bounded malformed CSS must still retire and recover.
 for label,vr,orr,scene_revision,initial_view,initial_output in [('ordinary',3,2,1,1,1),('zero-owned-scene-initial-ordinals',1,1,0,0,0),('max-view-inclusive',MAX,2,1,1,1),('max-output-inclusive',3,MAX,1,1,1)]:
  s=stage();a=api(s,texture=(1600,638),canvas=(1600,638));frame=[0];ctx={'projectUid':'CONTROLLED_PROJECT','projectContentSha256':'a'*64,'revision':1};c=g.GridRasterCoordinator(lambda _:dict(ctx),lambda _:p.sample_grid_viewport(a,s,r.CAMERA,output_layout=Layout()),lambda:frame[0])
  try:
   ctx['revision']=scene_revision;first=view(initial_view);first['backgroundGridRaster']['binding']['sceneRevision']=scene_revision;first['backgroundGridRaster']['output']['revision']=initial_output;c.offer(s,first);frame[0]+=1;c.notify();await c._task;wrong=view(vr);wrong['backgroundGridRaster']['binding']['sceneRevision']=scene_revision;wrong['backgroundGridRaster']['output']['revision']=orr;wrong['backgroundGridRaster']['output']['css'][0]=0
   try:c.offer(s,wrong)
   except ValueError:pass
   else:raise AssertionError('invalid CSS accepted')
   assert (c._last_view,c._output_revision,c._serial)==(vr,orr,2)and c._output_observation is None and c.carrier_contract(s)is None
   ordinary=label in('ordinary','zero-owned-scene-initial-ordinals')
   if ordinary:
    recovery=view(vr+1);recovery['backgroundGridRaster']['binding']['sceneRevision']=scene_revision;recovery['backgroundGridRaster']['output']['revision']=orr+1;c.offer(s,recovery);frame[0]+=1;c.notify();await c._task;assert c.receipt['phase']=='rendered'
   rows.append({'case':label,'boundedOrdinalsRetire':True,'invalidPayloadNotRetained':True,'maxInclusiveNaturalSequenceExhaustion':not ordinary,'ordinaryRecovery':ordinary})
  finally:c.close()
asyncio.run(run());out={'reference':reference,'rows':rows,'count':len(rows),'scope':'actual schema/coordinator/CPUUSD with controlled scene/media measurements; no runtime pixels'};(T/'logs'/('safe-ordinal-F2-red.json'if reference else'safe-ordinal-green.json')).write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out,indent=2))
