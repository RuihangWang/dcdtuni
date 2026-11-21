"""Owned signed renderer artifact and a final-commit context gate.

Pure module. Real Kit and CPU race tests share this controller. Only a VERIFIED
rendererResult grant can establish context; legacy browser packet declarations
never enter this path. No numeric producer is implemented here.
"""
from __future__ import annotations
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib,json,math,os,threading,time

MAX_PACKET_BYTES=256*1024*1024

def _canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode('ascii')

def _scene_matches(granted,scene):
 return scene=={'projectUid':granted['source']['projectUid'],'projectContentSha256':granted['source']['projectContentSha256'],'revision':granted['sceneRevision']}

def _packet_matches_grant(packet,granted):
 if not isinstance(packet,dict)or packet.get('source')!=granted['source']or packet.get('timeSeconds')!=granted['timeSeconds']:return False
 if granted['kind']=='rendererResult':
  return packet.get('schema')in('dcdtuni.render-result-layers/v2','dcdtuni.render-result-layers/v3') and packet.get('runConvergence')==granted['runConvergence']
 if granted['kind']=='rendererMesh':
  return (packet.get('schema')=='dcdtuni.render-result-layers/v4' and packet.get('source',{}).get('run')is None and packet.get('timeSeconds')is None and
   'runConvergence'not in packet and 'volume'not in packet and all(layer.get('kind')in {'meshEdges','meshFaces'}for layer in packet.get('layers',[]))and
   packet.get('meshPreview')=={name:granted[name]for name in ['meshSnapshotKey','meshPayloadSha256','meshMetadataSha256']})
 return False

def read_granted_packet(granted):
 """Run only after display_grant.verify(kind=rendererResult), off the UI thread."""
 path=granted['path'];before=os.stat(path,follow_symlinks=False)
 if os.path.islink(path)or not 0<before.st_size<=MAX_PACKET_BYTES:raise ValueError('signed renderer artifact is linked/empty or exceeds capacity')
 with open(path,'rb')as stream:raw=stream.read(MAX_PACKET_BYTES+1)
 after=os.stat(path,follow_symlinks=False)
 if (before.st_size,before.st_mtime_ns,before.st_ino)!=(after.st_size,after.st_mtime_ns,after.st_ino):raise ValueError('renderer artifact changed while loading')
 if len(raw)>MAX_PACKET_BYTES or hashlib.sha256(raw).hexdigest()!=granted['packetSha256']:raise ValueError('renderer artifact hash differs from server signature')
 packet=json.loads(raw,parse_constant=lambda v:(_ for _ in ()).throw(ValueError('nonfinite renderer packet')))
 if not _packet_matches_grant(packet,granted):
  raise ValueError('renderer artifact source/frame differs from server signature')
 if _canonical(packet)!=raw:raise ValueError('renderer artifact is not canonical owned JSON')
 return packet

@dataclass(frozen=True)
class ResultTicket:
 serial:int
 stage_generation:int
 granted_json:bytes
 packet_sha256:str
 def granted(self):return json.loads(self.granted_json)

class VerifiedResultController:
 """One Kit receiver, current signed display context and final mutation lock.

 Entry installs a serial BEFORE an async artifact read. A newer signed entry,
 clear or scene change invalidates that work. The final commit context manager
 checks source/frame/scene/generation/expiry/hash again and holds the SAME lock
 throughout atomic USD subtree replacement, so a late task cannot overwrite it.
 """
 def __init__(self,clock=time.time):
  self._lock=threading.RLock();self._serial=0;self._current=None;self._scope=None;self._minimum_generation=-1;self._clock=clock
 def accept(self,granted,scene,stage_generation):
  with self._lock:
   if granted.get('kind')not in {'rendererResult','rendererMesh'} or not _scene_matches(granted,scene):raise ValueError('signed result differs from acknowledged scene')
   scope=(scene['projectUid'],scene['projectContentSha256'],scene['revision'])
   if scope!=self._scope:self._scope=scope;self._minimum_generation=-1
   generation=granted['displayGeneration']
   if type(generation)is not int or generation<self._minimum_generation:raise ValueError('signed result belongs to an older common display generation')
   if self._clock()>granted['expiresAt']:raise ValueError('signed result expired before load')
   self._minimum_generation=generation;self._serial+=1
   ticket=ResultTicket(self._serial,stage_generation,_canonical(granted),granted['packetSha256']);self._current=ticket
   return ticket
 def invalidate(self,*,advance_generation=False,display_generation=None):
  with self._lock:
   if display_generation is not None and(type(display_generation)is not int or not 0<=display_generation<=9007199254740991):raise ValueError('invalid common clear generation')
   self._serial+=1;self._current=None
   if display_generation is not None:
    self._minimum_generation=max(self._minimum_generation,display_generation)
   elif advance_generation:self._minimum_generation+=1
   return self._serial
 @contextmanager
 def clear_commit(self,serial,stage_generation,current_stage_generation):
  with self._lock:
   if self._serial!=serial or self._current is not None or stage_generation!=current_stage_generation():
    raise ValueError('result clear was superseded by a newer scene/display intent')
   yield
 def _require(self,ticket,packet,scene,stage_generation):
  granted=ticket.granted()
  if ticket!=self._current or ticket.serial!=self._serial or stage_generation!=ticket.stage_generation:
   raise ValueError('signed result context changed while loading/authoring')
  if not _scene_matches(granted,scene):raise ValueError('acknowledged scene changed while loading/authoring')
  if granted['displayGeneration']<self._minimum_generation:raise ValueError('common display generation changed while loading/authoring')
  now=self._clock()
  if not math.isfinite(now)or now>granted['expiresAt']:raise ValueError('signed result expired before final commit')
  if (not _packet_matches_grant(packet,granted)or
      hashlib.sha256(_canonical(packet)).hexdigest()!=ticket.packet_sha256):
   raise ValueError('owned numeric packet source/frame/bytes changed before commit')
 def require_loaded(self,ticket,packet,scene,stage_generation):
  with self._lock:self._require(ticket,packet,scene,stage_generation)
 @contextmanager
 def commit(self,ticket,packet,current_scene,current_stage_generation):
  with self._lock:
   self._require(ticket,packet,current_scene(),current_stage_generation())
   yield
