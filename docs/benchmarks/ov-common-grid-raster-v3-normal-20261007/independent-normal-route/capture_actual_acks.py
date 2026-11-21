from pathlib import Path
import sys,runpy,json,copy
R=Path(__file__).resolve().parent;target=str(R/'harness-packet/harness/actual_extension_grid_route.py');events=[]
def observe(frame,event,arg):
 if frame.f_code.co_filename!=target:return None
 if event=='return' and frame.f_code.co_name=='<lambda>' and 'rid'in frame.f_locals and 'kw'in frame.f_locals and 'acks'in frame.f_locals:
  events.append(copy.deepcopy(frame.f_locals['acks'][-1]))
 return observe
sys.argv=[str(R/'run_normal.py'),'actual_extension_grid_route.py']
sys.settrace(observe)
try:runpy.run_path(str(R/'run_normal.py'),run_name='__main__')
finally:sys.settrace(None)
assert [x['requestId']for x in events]==['update','plain','probe']
assert all(x['ok']for x in events)and events[1]['view']==events[2]['view']
assert'diagnostics'not in events[1]and events[2]['diagnostics']['pixelVerified']is False
result={'scope':'Readonly trace of existing controlled emitter callback return; original precise-path-bound harness/assertions unchanged, actual normal product methods. Not live WebRTC/Kit/pixels.','acks':events,'ordinaryBoundedViewEqual':True}
(R/'actual-normal-emitted-acks.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({'capturedActualEmitterIDs':[x['requestId']for x in events],'ordinaryBoundedViewEqual':True}))
