from pathlib import Path
import asyncio,json,runpy
T=Path(__file__).resolve().parents[1];base=runpy.run_path(str(T/'harness/cpu_usd_grid.py'));g,r,p,view,stage,api,Layout=[base[k]for k in('g','r','p','view','stage','api','ControlledOwnedLayout')];MAX=9007199254740991;rows=[]
async def run():
 for label,scene,initial_view,initial_output,next_view,next_output in [('ordinary',1,1,1,3,2),('zero',0,0,0,1,1),('max-view',1,1,1,MAX,2),('max-output',1,1,1,3,MAX),('max-scene',MAX,1,1,3,2)]:
  s=stage();a=api(s,texture=(1600,638),canvas=(1600,638));frame=[0];ctx={'projectUid':'CONTROLLED_PROJECT','projectContentSha256':'a'*64,'revision':scene,'displayGeneration':0};c=g.GridRasterCoordinator(lambda _:dict(ctx),lambda _:p.sample_grid_viewport(a,s,r.CAMERA,output_layout=Layout()),lambda:frame[0])
  def make(v,o):
   x=view(v);x['backgroundGridRaster']['binding']['sceneRevision']=scene;x['backgroundGridRaster']['output']['revision']=o;return x
  try:
   c.offer(s,make(initial_view,initial_output));frame[0]+=1;c.notify();await c._task;assert c.receipt['phase']=='rendered'
   literal=s.GetRootLayer().ExportToString();wrong=make(next_view,next_output);wrong['backgroundGridRaster']['output']['css'][0]=0
   try:c.offer(s,wrong)
   except ValueError:pass
   else:raise AssertionError('bad measurement accepted')
   assert(c._last_view,c._output_revision,c._serial)==(next_view,next_output,2)and c._output_observation is None and c.carrier_contract(s)is None
   assert s.GetRootLayer().ExportToString()==literal
   recovered=None
   if label not in('max-view','max-output'):
    c.offer(s,make(next_view+1,next_output+1));frame[0]+=1;c.notify();await c._task;assert c.receipt['phase']=='rendered';recovered=True
   else:
    retry=make(next_view,next_output);refused=False
    try:c.offer(s,retry)
    except ValueError:refused=True
    assert refused and c.carrier_contract(s)is None
   rows.append({'case':label,'inclusiveBoundsAccepted':True,'invalidDataNeverAuthority':True,'oldUSDHeldOnRetirement':True,'boundedRecovery':recovered,'naturalSafeOrdinalExhaustion':label in('max-view','max-output')})
  finally:c.close()
asyncio.run(run());print(json.dumps(rows,indent=2));(T/'logs/independent-inclusive-boundaries.json').write_text(json.dumps(rows,indent=2))
