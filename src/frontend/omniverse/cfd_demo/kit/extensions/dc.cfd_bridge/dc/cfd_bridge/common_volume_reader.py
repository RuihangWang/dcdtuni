"""Read the signed original CPP grid, not a browser supplied array/path."""
from functools import lru_cache
import hashlib,importlib.util,json,os,re,sys
from pathlib import Path

@lru_cache(maxsize=1)
def shared_grid_module():
 path=Path(__file__).resolve().parents[7]/'shared/python/common_volume_grid.py'
 name='_dcdtuni_shared_common_volume_grid'
 spec=importlib.util.spec_from_file_location(name,path)
 module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module)
 return module

def read_granted_volume(granted,packet):
 if packet.get('schema')!='dcdtuni.render-result-layers/v3':return None
 volume=packet['volume'];descriptor=volume['grid'];name=volume['artifactName']
 if not isinstance(name,str)or not re.fullmatch(r'[a-f0-9]{64}\.volume',name)or name!=descriptor['payloadSha256']+'.volume':
  raise ValueError('signed volume artifact name differs from payload identity')
 directory=Path(granted['path']).parent
 path=directory/name
 if path.is_symlink()or path.resolve().parent!=directory.resolve():raise ValueError('signed common volume artifact escapes its owned directory')
 before=path.stat();capacity=shared_grid_module().MAX_GRID_BYTES
 if not 4<before.st_size<=capacity or before.st_size!=descriptor['payloadBytes']:raise ValueError('signed common volume byte count differs')
 with path.open('rb')as stream:raw=stream.read(capacity+1)
 after=path.stat()
 if (before.st_size,before.st_mtime_ns,before.st_ino)!=(after.st_size,after.st_mtime_ns,after.st_ino)or hashlib.sha256(raw).hexdigest()!=descriptor['payloadSha256']:
  raise ValueError('signed common volume bytes changed while loading')
 size=int.from_bytes(raw[:4],'little')
 metadata=json.loads(raw[4:4+size].rstrip(b'\0'))
 grid=shared_grid_module().decode_common_volume(raw,{'field':descriptor['field']['name'],'resolution':descriptor['dimensions'][0]},metadata)
 if grid.descriptor(descriptor['field']['unit'])!=descriptor or volume['field']!=descriptor['field']:
  raise ValueError('signed common volume grid coordinates/field differ from actual binary')
 return grid
