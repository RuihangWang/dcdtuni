from pathlib import Path
import json,runpy,types
T=Path(__file__).resolve().parents[1]
ns=runpy.run_path(str(T/'tests/native_a.py'))
rows=[]
for kind in ('source-revision','native-time'):
 v=ns['fixture']();s,a,state,setting,calls,app,w,g,run=v
 old=a._ViewportAPI__viewport_texture
 class Texture:
  def __init__(self):self.__dict__.update(old.__dict__);self.data=old.frame_info;self.reads=0
  @property
  def frame_info(self):
   self.reads+=1
   if self.reads==2:
    if kind=='source-revision':state['sceneRevision']=2
    else:a._ViewportAPI__time=ns['ns']['Usd'].TimeCode(1)
   return self.data
 tex=Texture();a._ViewportAPI__viewport_texture=tex;out=run()
 rows.append({'kind':'actual literal ViewportAPI last frame_info delegates controlled texture mutation '+kind,'reads':tex.reads,'status':out['status'],'reason':out['reason'],'rawMetadataNotEncodedSameFrame':True})
 assert out['status']=='unavailable',out
(T/'late-metadata-sideeffect-results.json').write_text(json.dumps({'scope':'actual API getter + real CPU USD, controlled texture side effects; no live/GPU/renderer/authority','rows':rows},indent=2)+'\n')
print(json.dumps(rows,indent=2))
