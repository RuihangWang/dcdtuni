from pathlib import Path
import ast,json,types,hashlib
p=Path('/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/integration/src/backend/liquidcooling/run_manager.py')
b=p.read_bytes();assert hashlib.sha256(b).hexdigest()=='b768314ae433a5c476ac2ddff6b5545c4b8ce80cda27a43d2ba7c4d019bedafc'
node=next(n for n in ast.walk(ast.parse(b.decode())) if isinstance(n,ast.FunctionDef) and n.name=='_advance_locked')
ns={'_RunRecord':object};exec(compile(ast.Module(body=[node],type_ignores=[]),str(p),'exec'),ns)
class ControlledStepFailure:
 latest_frame={'seq':1}
 def step(self):raise RuntimeError('independent controlled step exception')
r=types.SimpleNamespace(simulation=ControlledStepFailure(),status='running',error=None)
ns['_advance_locked'](None,r)
assert r.status=='failed' and r.error=={'type':'RuntimeError','message':'independent controlled step exception','last_successful_seq':1}
print(json.dumps({'scope':'actual backend method only with controlled exception carrier; no physical simulation','status':r.status,'error':r.error}))
