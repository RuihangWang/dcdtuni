"""Literal public SDK bodies with controlled UI; no native/UI/GPU execution."""
from pathlib import Path
import ast,hashlib,importlib.util,unittest
W=Path(__file__).resolve().parents[1]
p=W/'candidate/src/frontend/omniverse/cfd_demo/tests/test_owned_output_layout.py'
spec=importlib.util.spec_from_file_location('portable_owned_tests',p);t=importlib.util.module_from_spec(spec);spec.loader.exec_module(t)
SDK=Path('/mnt/lv/home/litianyu/.local/share/ov/data/exts/v2/omni.kit.widget.viewport-e33603573147cc56/omni/kit/widget/viewport')
for name,expected in [('display_delegate.py','47ebdf1077aa854f366e9c2d7208e5ee1b96dd6cb75384aeae7aa4a6f18449ba'),('widget.py','05b000e92fec9e624844196260c7404de6a3e5589a9db524291ac8b5814d2170')]:assert hashlib.sha256((SDK/name).read_bytes()).hexdigest()==expected
node=next(x for x in ast.parse((SDK/'display_delegate.py').read_text()).body if isinstance(x,ast.ClassDef) and x.name=='ViewportDisplayDelegate');scope={'ui':t.UI};exec(compile(ast.Module(body=[node],type_ignores=[]),str(SDK/'display_delegate.py'),'exec'),scope);t.Base=scope['ViewportDisplayDelegate']
cl=next(x for x in ast.parse((SDK/'widget.py').read_text()).body if isinstance(x,ast.ClassDef) and x.name=='ViewportWidget');fns=[x for x in cl.body if isinstance(x,ast.FunctionDef) and x.name=='display_delegate'];module=ast.Module(body=[ast.ClassDef(name='ViewportWidget',bases=[],keywords=[],body=fns,decorator_list=[])],type_ignores=[]);ast.fix_missing_locations(module);scope={'ViewportDisplayDelegate':t.Base};exec(compile(module,str(SDK/'widget.py'),'exec'),scope);t.Widget=scope['ViewportWidget']
r=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(t));raise SystemExit(0 if r.wasSuccessful() else 1)
