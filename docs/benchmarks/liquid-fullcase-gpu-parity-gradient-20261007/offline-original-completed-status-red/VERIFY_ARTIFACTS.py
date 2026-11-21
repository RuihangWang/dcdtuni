from pathlib import Path
import gzip,hashlib,io,json
import numpy as np
B=Path(__file__).resolve().parent
manifest=json.loads((B/'MANIFEST.json').read_text())
for e in manifest['payloads']:
 p=B/e['path'];assert p.resolve().is_relative_to(B)
 raw=p.read_bytes();assert len(raw)==e['stored_bytes'] and hashlib.sha256(raw).hexdigest()==e['stored_sha256']
 source=gzip.decompress(raw) if e['encoding']=='gzip' else raw
 assert len(source)==e['original_bytes'] and hashlib.sha256(source).hexdigest()==e['original_sha256']
def data(path):
 p=B/path
 return json.loads(gzip.decompress(p.read_bytes())) if p.suffix=='.gz' else json.loads(p.read_text())
plan=data('plan.json')
for label,*_ in plan['cohorts']:
 assert data('cohorts/'+label+'/status.json')['rc']==0
 assert data('cohorts/'+label+'/result.json')['status']=='PASS'
def arr(label,key):
 with np.load(B/'cohorts'/label/'trajectory.npz',allow_pickle=False)as z:return z[key].copy()
cpu=arr('cpu-full','states');gpu=arr('gpu-full','states')
assert cpu.shape==gpu.shape==(11,1693)
np.testing.assert_allclose(cpu,gpu,rtol=1e-10,atol=1e-7)
maxdiff=float(np.abs(cpu-gpu).max());claim=data('forward-parity-root.json')
assert maxdiff==claim['cpu_gpu_max_absolute_state_difference']
gui_reply=data('input/frames.json.gz');assert gui_reply['status']=='converged' and gui_reply['error'] is None
gui=gui_reply['frames']
fields=list(claim['CPU_GPU_fields'])
for label in ['cpu-full','gpu-full']:
 frames=data('cohorts/'+label+'/frames.json.gz');assert len(frames)==len(gui)==11
 for field in fields:
  actual=np.asarray([f[field]for f in frames]);original=np.asarray([f[field]for f in gui])
  np.testing.assert_allclose(actual,original,rtol=1e-10,atol=1e-7)
grads=np.array(data('cohorts/cpu-adjoint2/result.json')['gradient'])
cuda=np.array(data('cohorts/gpu-adjoint2/result.json')['gradient'])
np.testing.assert_allclose(grads,cuda,rtol=1e-12,atol=1e-10)
relative=[]
for idx,eps in [(1,.001),(2,.0005)]:
 fd=(arr('cpu-fd-plus'+str(idx),'objectives')-arr('cpu-fd-minus'+str(idx),'objectives'))/(2*eps)
 err=np.abs(fd-grads)/np.abs(grads);assert np.all(err<1e-8);relative.append(err.tolist())
 assert np.array_equal(fd,np.asarray(data('gradient-fd-root.json')['finite_differences'][idx-1]['finite_difference']))
print(json.dumps({'status':'PASS','manifest_payloads':len(manifest['payloads']),'forward_shape':list(cpu.shape),'cpu_gpu_max_abs_state_difference':maxdiff,'heat_gradient_4s':grads.tolist(),'finite_difference_relative_errors':relative,'scope':'offline byte and arithmetic replay, no new solver/GPU execution'},indent=2))
