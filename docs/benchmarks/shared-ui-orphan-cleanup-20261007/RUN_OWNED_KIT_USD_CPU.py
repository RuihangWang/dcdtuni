import sys;sys.dont_write_bytecode=True
from pathlib import Path
import ast,hashlib,importlib.util,json,os,resource
resource.setrlimit(resource.RLIMIT_CORE,(0,0));os.sched_setaffinity(0,{36,37})
D=Path(sys.argv[1]).resolve() if len(sys.argv)>1 else next(p for p in Path(__file__).resolve().parents if (p/'src/frontend/react/package.json').is_file())
T=D/'src/backend/tests/test_constant_common_lut.py'
A=D/'src/frontend/omniverse/cfd_demo/kit/extensions/dc.cfd_bridge/dc/cfd_bridge/result_layers_usd.py'
from pxr import Gf
assert Path(sys.executable).resolve().is_relative_to(D),sys.executable
assert Path(Gf.__file__).resolve().is_relative_to(D),Gf.__file__
spec=importlib.util.spec_from_file_location('private_owned_result_layers_usd',A);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
# Execute the two original test bodies unchanged (only pytest decorators omitted),
# using official Kit's matching CPython/USD ABI; no backend-env site-packages borrowed.
names=['test_actual_usd_flat_normalization_is_finite_and_matches_common_palette_endpoint','test_collapsed_display_domain_and_outliers_sample_only_palette_start_without_rewriting_field']
tree=ast.parse(T.read_text());functions=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names]
assert len(functions)==2
for function in functions:function.decorator_list=[]
namespace={'adapter':lambda:mod}
exec(compile(ast.Module(body=functions,type_ignores=[]),str(T),'exec'),namespace)
for name in names:
 for palette,endpoint in [('jet',(0,0,128)),('coolwarm',(59,76,192)),('viridis',(68,1,84))]:namespace[name](palette,endpoint)
loaded=[]
for line in Path('/proc/self/maps').read_text().splitlines():
 path=line.split()[-1]
 if '/libusd_' in path or '/pxr/' in path:
  p=Path(path);assert p.resolve().is_relative_to(D),path;loaded.append(str(p.resolve()))
print(json.dumps({'controls':6,'status':'PASS','python':sys.version,'sysExecutable':sys.executable,'sysPrefix':sys.prefix,'GfOrigin':Gf.__file__,'testSourceSHA256':hashlib.sha256(T.read_bytes()).hexdigest(),'adapterSHA256':hashlib.sha256(A.read_bytes()).hexdigest(),'CPU_affinity':sorted(os.sched_getaffinity(0)),'CUDA_VISIBLE_DEVICES':os.environ.get('CUDA_VISIBLE_DEVICES'),'ownedUsdLibraries':sorted(set(loaded))},indent=2))
