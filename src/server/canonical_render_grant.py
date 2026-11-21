"""Canonical accepted numeric display artifact, signed with the existing launch key."""
from __future__ import annotations
import hashlib,json,os,time,uuid
from pathlib import Path
if __package__:
 from .verified_render_source import verify_render_source
 from .canonical_render_packet import build_canonical_packet,_keys,_integer
 from .ov_result_dataset import _grant_module
else:
 from verified_render_source import verify_render_source
 from canonical_render_packet import build_canonical_packet,_keys,_integer
 from ov_result_dataset import _grant_module
from backend.aircooling.runner.numeric_product_receipts import canonical_bytes

MAX_PACKET_BYTES=256*1024*1024

def _owned_directory(run):
 path=run/'.ov-display/canonical-result'
 current=run
 for component in ['.ov-display','canonical-result']:
  current=current/component
  if current.is_symlink():raise ValueError('canonical result directory is linked')
  current.mkdir(exist_ok=True)
 if path.resolve().parent.parent!=run:raise ValueError('canonical artifact is outside selected run')
 return path

def _publish_owned_bytes(directory,name,payload):
 target=directory/name
 if target.is_symlink():raise ValueError('canonical artifact is linked')
 if target.exists():
  if target.read_bytes()!=payload:raise ValueError('canonical artifact bytes differ from its numeric identity')
 else:
  temporary=directory/('.'+uuid.uuid4().hex+'.tmp')
  try:
   with temporary.open('xb')as stream:stream.write(payload);stream.flush();os.fsync(stream.fileno())
   os.link(temporary,target)
  except FileExistsError:
   if target.is_symlink()or target.read_bytes()!=payload:raise ValueError('canonical artifact was replaced during publication')
  finally:temporary.unlink(missing_ok=True)
 return target

def issue_canonical_render_grant(*,cpp,case_dir,session,request,key,now=None,current_case=None):
 """Caller pins CPP revision/case; repeat every source check before signing.

 Scene/generation here are presentation coordinates supplied by the common
 controller. The signature binds them; Kit must compare its CURRENT acknowledged
 scene and generation again after loading and immediately before USD commit.
 A browser cannot establish physical/source acceptance by declaring these values.
 """
 _keys(request,['schema','scene','displayGeneration','intent'],'canonical display request')
 if request['schema']!='dcdtuni.canonical-render-request/v1':raise ValueError('unsupported canonical display request')
 scene=_keys(request['scene'],['projectUid','projectContentSha256','revision'],'acknowledged scene identity')
 revision=_integer(scene['revision'],'acknowledged scene revision')
 generation=_integer(request['displayGeneration'],'common display generation')
 before=verify_render_source(cpp=cpp,case_dir=case_dir,session=session)
 if scene['projectUid']!=before['source']['projectUid']or scene['projectContentSha256']!=before['source']['projectContentSha256']:
  raise ValueError('acknowledged scene differs from the accepted source project')
 if current_case is not None and not current_case():raise ValueError('selected case changed before canonical author')
 store=getattr(cpp,'_numeric_product_store',None)
 if store is None:raise ValueError('common numeric response products have not been captured')
 assets={}
 packet=build_canonical_packet(request['intent'],descriptor=before,store=store,volume_assets=assets)
 payload=canonical_bytes(packet);digest=hashlib.sha256(payload).hexdigest()
 if len(payload)>MAX_PACKET_BYTES:raise ValueError('canonical numeric display exceeds artifact capacity')
 directory=_owned_directory(Path(cpp.run_dir).resolve())
 for name,blob in assets.items():_publish_owned_bytes(directory,name,blob)
 target=_publish_owned_bytes(directory,digest+'.json',payload)
 after=verify_render_source(cpp=cpp,case_dir=case_dir,session=session)
 # Re-decoding owned products also rechecks expiry/eviction/current generation.
 # No additional numeric producer or physical calculation is run here.
 current_assets={}
 current_packet=build_canonical_packet(request['intent'],descriptor=after,store=store,volume_assets=current_assets)
 if after!=before or canonical_bytes(current_packet)!=payload or current_assets!=assets:
  raise ValueError('accepted source/products changed during canonical publication')
 if current_case is not None and not current_case():raise ValueError('selected case changed before grant signature')
 if target.is_symlink()or target.read_bytes()!=payload:raise ValueError('canonical artifact changed before grant signature')
 for name,blob in current_assets.items():
  asset=directory/name
  if asset.is_symlink()or asset.read_bytes()!=blob:raise ValueError('original common grid artifact changed before grant signature')
 stamp=time.time()if now is None else now
 grant=_grant_module().sign({'kind':'rendererResult','path':str(target),'runId':session['runId'],
  'packetSha256':digest,'nativeInputSha256':after['nativeInputSha256'],'postprocessRevision':session['revision'],
  'timeSeconds':session['timeValue'],'sceneRevision':revision,'displayGeneration':generation,'source':after['source'],'runConvergence':after['runConvergence'],
  'issuedAt':stamp,'expiresAt':stamp+600,'nonce':uuid.uuid4().hex},key)
 return {'grant':grant,'packetReceipt':{'sha256':digest,'revision':packet['revision'],'source':packet['source'],
  'timeSeconds':packet['timeSeconds'],'postprocess':session,'sceneRevision':revision,'displayGeneration':generation,'runConvergence':after['runConvergence']}}
