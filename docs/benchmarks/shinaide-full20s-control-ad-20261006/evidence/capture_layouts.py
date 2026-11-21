from pathlib import Path
import json,sys,hashlib
import torch,numpy as np
R=Path(__file__).resolve().parent;I=Path('/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/integration');sys.path.insert(0,str(I/'src'));from backend.liquidcooling.dae_runtime import admit_dae_runtime_input
torch.set_num_threads(2);d=admit_dae_runtime_input(json.loads((R/'actual/input.liquidrun').read_text()));a=admit_dae_runtime_input(json.loads((R/'actual/executed.liquidrun').read_text()));eq=d.equation_graph;params=np.load(R/'parameters.npz');baseline=params['baseline_AD'];checks=[]
for i in range(11):
 expected=a.parameters(i).numpy();got=baseline[:,i];same=bool(np.array_equal(got.view(np.uint64),expected.view(np.uint64)));checks.append({'seq':i,'all_parameters_bit_exact':same,'max_abs':float(np.max(np.abs(got-expected)))});assert same
layout={'state_layout':{k:[s.start,s.stop,s.step] for k,s in eq.state_layout.items()},'parameter_layout':{k:[s.start,s.stop,s.step] for k,s in eq.parameter_layout.items()},'state_scale':eq.state_scale.numpy().tolist(),'residual_scale':eq.residual_scale.numpy().tolist(),'schema':'admitted-static-layout/no-physics-solve','accepted_manifest_vs_original_plus_true_commands':checks};(R/'layout-metadata.json').write_text(json.dumps(layout,indent=2));print(json.dumps({'count':len(checks),'all_params_bit_exact':True,'states':eq.size}))
