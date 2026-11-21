from pathlib import Path
import asyncio, json, runpy
T=Path(__file__).resolve().parents[1]
base=runpy.run_path(str(T/'harness/cpu_usd_grid.py'))
g,r,p,view,stage,api,Layout=[base[k]for k in ('g','r','p','view','stage','api','ControlledOwnedLayout')]
rows=[]
async def run():
 for kind in ('oversized-view','oversized-output','ordinary-valid-recovery-control'):
  s=stage();a=api(s,texture=(1600,638),canvas=(1600,638));frame=[0]
  ctx={'projectUid':'CONTROLLED_PROJECT','projectContentSha256':'a'*64,'revision':1}
  c=g.GridRasterCoordinator(lambda _:dict(ctx),lambda _:p.sample_grid_viewport(a,s,r.CAMERA,output_layout=Layout()),lambda:frame[0])
  try:
   c.offer(s,view());frame[0]+=1;c.notify();await c._task
   assert c.receipt['phase']=='rendered'
   before={'view':c._last_view,'output':c._output_revision,'serial':c._serial,'permission':c.carrier_contract(s)is not None}
   wrong=view(3);wrong['backgroundGridRaster']['output']['revision']=2
   if kind=='oversized-view':wrong['backgroundGridRaster']['binding']['viewRevision']=9007199254740992
   elif kind=='oversized-output':wrong['backgroundGridRaster']['output']['revision']=9007199254740992
   else:wrong['backgroundGridRaster']['output']['css'][0]=0
   refused=None
   try:c.offer(s,wrong)
   except (ValueError,TypeError)as e:refused=str(e)
   after={'view':c._last_view,'output':c._output_revision,'serial':c._serial,'permission':c.carrier_contract(s)is not None}
   if kind!='ordinary-valid-recovery-control':assert after==before,(kind,'out-of-schema identity poisoned permission',before,after)
   valid=view(4);valid['backgroundGridRaster']['output']['revision']=3
   recovered=True;error=None
   try:c.offer(s,valid)
   except(ValueError,TypeError)as e:recovered=False;error=str(e)
   if recovered:frame[0]+=1;c.notify();await c._task;recovered=c.receipt['phase']=='rendered'
   assert recovered,(kind,'bounded valid recovery failed',error)
   rows.append({'kind':kind,'schemaRefusal':refused,'before':before,'after':after,'boundedValidView4Output3Recovered':recovered,'recoveryError':error})
  finally:c.close()
asyncio.run(run());print(json.dumps(rows,indent=2));(T/'logs/independent-safe-ordinal-boundary.json').write_text(json.dumps(rows,indent=2))
