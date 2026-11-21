"""Convert the exact signed regular nodes to the existing NanoVDB carrier.

The actual first conversion verifies EVERY loaded integer node and the grid
transform. Cached files retain a literal SHA and source/converter receipt.
No GPU is imported or executed by CPU source/contract validation.
"""
import hashlib,json,os,threading,uuid
from pathlib import Path
import numpy as np
_LOCK=threading.RLock()

def _hash(path):
 digest=hashlib.sha256()
 with path.open('rb')as stream:
  for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
 return digest.hexdigest()

def converter_identity():
 root=Path(__file__).parent
 digest=hashlib.sha256()
 for name in ['common_volume_nvdb.py','common_volume_warp.py']:
  digest.update(name.encode());digest.update((root/name).read_bytes())
 return digest.hexdigest()

def prepare_nvdb(grid,owned_directory):
 # Two carriers reorganize storage only. Both are verified against the SAME
 # signed original binary; mask values are original uint8 0/1 cast to float.
 return {'scalar':_prepare_nodes(grid,owned_directory,'scalar',grid.scalar_nodes(),grid.sentinel),
         'validity':_prepare_nodes(grid,owned_directory,'validity',grid.validity_nodes().astype(np.float32),0.)}

def _prepare_nodes(grid,owned_directory,role,values,background):
 from .owned_paths import owned_path
 directory=Path(owned_path(str(owned_directory),'common volume carrier directory'))
 if directory.is_symlink()or not directory.is_dir():raise ValueError('common grid carrier directory is not owned')
 digest=converter_identity();base=grid.payload_sha256+'-'+digest[:16]+'-'+role
 target=directory/(base+'.nvdb');receipt=directory/(base+'.nvdb-receipt.json')
 expected={'schema':'dcdtuni.common-volume-carrier/v2','role':role,'storedNodesSha256':hashlib.sha256(values.tobytes()).hexdigest(),'sourcePayloadSha256':grid.payload_sha256,'converterSha256':digest,
  'dimensions':list(grid.dimensions),'originMeters':list(grid.origin),'spacingMeters':list(grid.spacing),'allIntegerNodesVerified':True}
 with _LOCK:
  if target.is_symlink()or receipt.is_symlink():raise ValueError('common grid carrier/cache receipt is linked')
  # Respect the Kit launch's visible CUDA device. Kernel cache stays in this
  # selected run's owned display directory; it never creates outside folders.
  import warp as wp
  cache=directory/'warp-kernels'
  if cache.is_symlink():raise ValueError('common grid kernel cache is linked')
  cache.mkdir(exist_ok=True);wp.config.kernel_cache_dir=str(cache)
  from .common_volume_warp import store_nodes,read_nodes
  device=wp.get_device('cuda')
  if not device.is_cuda:raise ValueError('common grid NanoVDB carrier requires the configured Kit CUDA device')
  def verify_nodes(path):
   with path.open('rb')as stream:loaded=wp.Volume.load_from_nvdb(stream,device=device)
   actual=wp.empty(values.size,dtype=wp.float32,device=device)
   wp.launch(read_nodes,dim=values.size,inputs=[loaded.id,actual,grid.dimensions[0],grid.dimensions[1]],device=device)
   if not np.array_equal(actual.numpy().view(np.uint32),values.view(np.uint32)):raise ValueError('NanoVDB carrier does not preserve every original grid node')
   info=loaded.get_grid_info()
   if info.type_str!='float' or info.grid_count!=1 or not np.array_equal(np.array(info.translation,dtype=np.float32),np.array(grid.origin,dtype=np.float32))or not np.array_equal(np.array(info.transform_matrix,dtype=np.float32).reshape(3,3),np.diag(np.array(grid.spacing,dtype=np.float32))):
    raise ValueError('NanoVDB carrier transform differs from original grid coordinates')
  if target.exists()and receipt.exists():
   saved=json.loads(receipt.read_text());file_sha=saved.pop('carrierSha256',None)
   if saved!=expected or _hash(target)!=file_sha:raise ValueError('common grid carrier cache differs from original receipt')
   # The unsigned local cache receipt is diagnostic, never numeric authority:
   # re-read EVERY node/transform against the signed original binary even here.
   verify_nodes(target)
   return str(target)
  axes=[np.arange(0,n,8,dtype=np.int32)for n in grid.dimensions]
  tiles=np.stack(np.meshgrid(*axes,indexing='ij'),axis=-1).reshape(-1,3)
  volume=wp.Volume.allocate_by_tiles(wp.array(tiles,dtype=wp.int32,device=device),voxel_size=list(grid.spacing),translation=grid.origin,bg_value=float(background),device=device)
  array=wp.array(values,dtype=wp.float32,device=device)
  wp.launch(store_nodes,dim=values.size,inputs=[volume.id,array,grid.dimensions[0],grid.dimensions[1]],device=device)
  temporary=directory/('.'+uuid.uuid4().hex+'.nvdb')
  try:
   volume.save_to_nvdb(str(temporary),codec='none')
   # Verify the file, not just the pre-serialization in-memory volume.
   verify_nodes(temporary)
   if target.exists():raise ValueError('common grid carrier appeared during conversion')
   os.link(temporary,target)
   saved={**expected,'carrierSha256':_hash(target)}
   temporary_receipt=directory/('.'+uuid.uuid4().hex+'.json')
   try:
    with temporary_receipt.open('x')as stream:json.dump(saved,stream,sort_keys=True);stream.flush();os.fsync(stream.fileno())
    os.link(temporary_receipt,receipt)
   finally:temporary_receipt.unlink(missing_ok=True)
   return str(target)
  finally:temporary.unlink(missing_ok=True)
