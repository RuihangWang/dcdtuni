from pathlib import Path
import asyncio,copy,json,runpy,sys,importlib.util
T=Path(__file__).resolve().parents[1];base=runpy.run_path(str(T/'harness/cpu_usd_grid.py'));g,r,p,view,stage,api,Layout=[base[k]for k in('g','r','p','view','stage','api','ControlledOwnedLayout')];rows=[]
expected_red='--expect-red'in sys.argv
if expected_red:
 q=T/'before-floor/src/frontend/omniverse/cfd_demo/kit/extensions/dc.cfd_bridge/dc/cfd_bridge/grid_raster.py';spec=importlib.util.spec_from_file_location('controlled_bridge.grid_floor_reference',q);g=importlib.util.module_from_spec(spec);sys.modules[spec.name]=g;spec.loader.exec_module(g)
async def check():
 for next_view,next_output in((2,1),(3,2),(3,3),(4,2),(4,3)):
  s=stage();a=api(s,texture=(1600,638),canvas=(1600,638));frame=[0];ctx={'projectUid':'CONTROLLED_PROJECT','projectContentSha256':'a'*64,'revision':1};c=g.GridRasterCoordinator(lambda _:dict(ctx),lambda _:p.sample_grid_viewport(a,s,r.CAMERA,output_layout=Layout()),lambda:frame[0])
  try:
   c.offer(s,view());frame[0]+=1;c.notify();await c._task;literal=s.GetRootLayer().ExportToString();wrong=view(3);wrong['backgroundGridRaster']['output']['revision']=2;wrong['backgroundGridRaster']['output']['css'][0]=0
   try:c.offer(s,wrong)
   except ValueError:pass
   else:raise AssertionError('invalid3/output2 accepted')
   assert c.carrier_contract(s)is None;floors=[c._last_view,c._output_revision];n=view(next_view);n['backgroundGridRaster']['output']['revision']=next_output;accepted=True
   try:c.offer(s,n)
   except (ValueError,g.GridRefused):accepted=False
   if accepted:frame[0]+=1;c.notify();await c._task
   stale=next_view<=3 or next_output<=2
   assert accepted==(True if expected_red else not stale),(next_view,next_output,accepted,floors)
   if not expected_red:
    assert floors==[3,2]
    if stale:assert c.carrier_contract(s)is None and s.GetRootLayer().ExportToString()==literal
   rows.append({'afterInvalidView3Output2':[next_view,next_output],'accepted':accepted,'stale':stale,'retirementFloors':floors,'oldUSDHeld':s.GetRootLayer().ExportToString()==literal,'invalidMeasurementNeverStored':c._output_observation is None if not accepted else True})
  finally:c.close()
async def same_revision_boundaries():
 if expected_red:return
 for kind in('same-output-invalid','valid-output-invalid-camera'):
  s=stage();a=api(s,texture=(1600,638),canvas=(1600,638));frame=[0];ctx={'projectUid':'CONTROLLED_PROJECT','projectContentSha256':'a'*64,'revision':1};c=g.GridRasterCoordinator(lambda _:dict(ctx),lambda _:p.sample_grid_viewport(a,s,r.CAMERA,output_layout=Layout()),lambda:frame[0])
  try:
   c.offer(s,view());frame[0]+=1;c.notify();await c._task;wrong=view(3)
   if kind=='same-output-invalid':wrong['backgroundGridRaster']['output']['css'][0]=0
   else:wrong['camera']['near']=wrong['camera']['far']
   try:c.offer(s,wrong)
   except ValueError:pass
   else:raise AssertionError('invalid view accepted')
   assert c._last_view==3 and c._output_revision==1 and c.carrier_contract(s)is None
   payload_held=c._output_observation is not None
   next_=view(4);accepted=True
   try:c.offer(s,next_)
   except (ValueError,g.GridRefused):accepted=False
   assert accepted==(kind=='valid-output-invalid-camera')
   if not accepted:
    next_=view(5);next_['backgroundGridRaster']['output']['revision']=2;c.offer(s,next_)
   frame[0]+=1;c.notify();await c._task;assert c.receipt['phase']=='rendered'
   rows.append({'case':kind,'previousValidOutputPayloadHeld':payload_held,'sameOutputRevisionAccepted':accepted,'newerValidRecovery':True})
  finally:c.close()
asyncio.run(same_revision_boundaries())
asyncio.run(check());print(json.dumps({'expectedRED':expected_red,'rows':rows,'scope':'actual CPUUSD/coordinator, controlled source/media observations; no native pixels'},indent=2));(T/'logs'/('retirement-floor-original-red.json'if expected_red else'retirement-floor-green.json')).write_text(json.dumps(rows,indent=2)+'\n')
