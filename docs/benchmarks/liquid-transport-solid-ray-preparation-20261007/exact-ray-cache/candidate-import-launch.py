"""Private consumer import routing; execute the candidate under its public module name."""
import importlib.util,os,runpy,sys
from pathlib import Path
repo=Path(os.environ['DCDTUNI_RAY_REPO']).resolve();candidate=Path(os.environ['DCDTUNI_RAY_CANDIDATE']).resolve()
sys.path.insert(0,str(repo/'src'))
import backend.aircooling.runner as package
name='backend.aircooling.runner.stlgen_runner'
spec=importlib.util.spec_from_file_location(name,candidate);module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module);package.stlgen_runner=module
if sys.argv[1]=='pytest':
 import pytest
 raise SystemExit(pytest.main(sys.argv[2:]))
else:
 target=sys.argv[1];sys.argv=sys.argv[1:];runpy.run_path(target,run_name='__main__')
