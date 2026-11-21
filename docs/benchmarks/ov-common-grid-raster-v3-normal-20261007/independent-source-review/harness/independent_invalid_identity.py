from pathlib import Path
import asyncio, json, runpy, copy
T=Path(__file__).resolve().parents[1]
base=runpy.run_path(str(T/'harness/cpu_usd_grid.py'))
g,r,p,view,stage,api,Layout=[base[k]for k in ('g','r','p','view','stage','api','ControlledOwnedLayout')]
rows=[]
async def run():
 cases=[('view-'+str(i),'binding','viewRevision',v,False)for i,v in enumerate((-1,True,4.0,'4',None,float('nan'),float('inf')))]
 cases += [('output-'+str(i),'output','revision',v,False)for i,v in enumerate((-1,True,2.0,'2',None,float('nan'),float('inf')))]
 cases += [('scene-'+str(i),'binding','sceneRevision',v,False)for i,v in enumerate((-1,True,1.0,'1',None,float('nan'),float('inf'),9007199254740992))]
 cases += [('unsupported-metric','raster','metricVersion',1,True),('missing-metric','raster','metricVersion','DELETE',True)]
 for name,part,key,value,retire in cases:
  s=stage();a=api(s,texture=(1600,638),canvas=(1600,638));frame=[0]
  ctx={'projectUid':'CONTROLLED_PROJECT','projectContentSha256':'a'*64,'revision':1,'displayGeneration':0}
  c=g.GridRasterCoordinator(lambda _:dict(ctx),lambda _:p.sample_grid_viewport(a,s,r.CAMERA,output_layout=Layout()),lambda:frame[0])
  try:
   c.offer(s,view());frame[0]+=1;c.notify();await c._task
   assert c.receipt['phase']=='rendered'
   before=(c._last_view,c._output_revision,c._serial,c._owner,c.carrier_contract(s))
   usd=s.GetRootLayer().ExportToString();wrong=view(3);ras=wrong['backgroundGridRaster'];ras['output']['revision']=2;ras['output']['css'][0]=0
   target=ras if part=='raster'else ras[part]
   if value=='DELETE':del target[key]
   else:target[key]=value
   refused=None
   try:c.offer(s,wrong)
   except(ValueError,TypeError)as e:refused=str(e)
   assert refused is not None,(name,'unexpected accepted')
   after=(c._last_view,c._output_revision,c._serial,c._owner,c.carrier_contract(s))
   assert s.GetRootLayer().ExportToString()==usd
   if retire:
    assert after[:4]==(3,2,before[2]+1,before[3])and after[4]is None,(name,before[:4],after[:4])
    recovered=view(4);recovered['backgroundGridRaster']['output']['revision']=3;c.offer(s,recovered);frame[0]+=1;c.notify();await c._task;assert c.receipt['phase']=='rendered'
   else:assert before==after,(name,before[:4],after[:4])
   rows.append({'case':name,'refused':True,'currentPermissionRetired':retire,'sourceUSDHeld':True,'floors':list(after[:2]),'validRecoveryAfterRetirement':True if retire else None})
  finally:c.close()
asyncio.run(run());print(json.dumps({'checks':len(rows),'rows':rows,'scope':'actual CPUUSD/source-bound coordinator with controlled owner/frame; no layout/pixels'},indent=2));(T/'logs/independent-invalid-identity-boundaries.json').write_text(json.dumps(rows,indent=2))
