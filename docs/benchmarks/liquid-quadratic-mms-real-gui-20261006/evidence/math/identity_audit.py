from pathlib import Path
import json,hashlib,copy,os
import numpy as np
F=Path('/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/full-mms-quadratic-product-private-20261006/FROZEN-v1')
O=Path('/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/quadratic-mms-root-review-private-20261006')
def digest(x):return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()
receipts=json.loads((F/'results/react-compiler-receipt.json').read_text());coarse=json.loads((F/'results/coarse-product-final.json').read_text())
identity=[]
for tag,receipt,run in zip(['2p0','1p0','0p5','0p25'],receipts,coarse['runs']):
 rawpath=F/f'results/compiled-dt-{tag}.liquidrun';raw=json.loads(rawpath.read_text());cp=json.loads((F/f'results/checkpoint-{tag}.json').read_text());accepted=json.loads((F/f'results/accepted-final-{tag}.liquidrun').read_text())
 bench=copy.deepcopy(raw['manufactured_benchmark']);expected=bench.pop('source_sha256')
 bench['times_s']=[float(x) for x in bench['times_s']]
 for section in ['sources','initial_state','initial_temperature_rate']:
  for block in bench[section].values():block['values']=np.asarray(block['values'],dtype=np.float64).tolist()
 bench['flow_signs']['values']=[float(x) for x in bench['flow_signs']['values']]
 assert digest(bench)==expected
 assert hashlib.sha256(rawpath.read_bytes()).hexdigest()==receipt['input_raw_sha256']
 assert hashlib.sha256((F/f'results/normalized-dt-{tag}.cfdproj').read_bytes()).hexdigest()==receipt['normalized_project_raw_sha256']
 assert cp['version']==3 and cp['seq']==raw['time']['N_t']-1
 assert digest({'liquidrun':cp['liquidrun'],'controllers':cp['controllers']})==cp['config_hash']==run['config_hash']
 assert cp['liquidrun']['manufactured_benchmark']==raw['manufactured_benchmark']==accepted['manufactured_benchmark']
 assert cp['liquidrun']['source_project']==raw['source_project']==accepted['source_project']
 assert len(cp['heat_multipliers'])==raw['time']['N_t'] and set(cp['heat_multipliers'])=={1.}
 identity.append({'tag':tag,'typed_benchmark_sha_independently_verified':expected,'raw_input_sha_verified':receipt['input_raw_sha256'],'normalized_project_raw_sha_verified':receipt['normalized_project_raw_sha256'],'actual_source_project_sha':raw['source_project']['sha256'],'checkpoint_v3_config_hash_independently_verified':cp['config_hash'],'checkpoint_original_full_benchmark_and_source_project_preserved':True,'accepted_final_full_benchmark_preserved':True,'N_t':raw['time']['N_t']})
review=json.loads((O/'independent-review.json').read_text())
scaled_init_max=max(r['continuous_initial_literal_residual']/1e4 for r in review['runs'])
assert scaled_init_max<1e-10
result={'identity_checks':identity,'original_atol':1e-10,'independent_assembled_initial_scaled_thermal_norm_max':scaled_init_max,'scope_limit':'No expensive new product checkpoint replay or negative constructor execution; those actual pinned logs/scripts were independently source-reviewed. Numeric own audit imported only NumPy/SciPy and the frozen JSON/data.','affinity':sorted(os.sched_getaffinity(0)),'CUDA_VISIBLE_DEVICES':os.environ.get('CUDA_VISIBLE_DEVICES')}
(O/'identity-review.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result,indent=2))
