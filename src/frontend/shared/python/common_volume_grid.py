"""Decode the ACTUAL CPP regular-grid product; no resampling algorithm.

The owned binary stays the source artifact. This contract describes POINT nodes
at origin + ijk*spacing, x varying fastest as in the existing CPP/VTK payload.
Carrier conversion to NanoVDB may reorganize storage, never sample another grid.
"""
from __future__ import annotations
from dataclasses import dataclass
import hashlib,json,math,re,struct
import numpy as np
MAX_GRID_BYTES=641*1024*1024

@dataclass(frozen=True)
class CommonVolumeGrid:
 dimensions:tuple[int,int,int]
 origin:tuple[float,float,float]
 spacing:tuple[float,float,float]
 sentinel:float
 scalar_range:tuple[float,float]
 field:str
 payload_sha256:str
 scalar_offset:int
 mask_offset:int
 payload:bytes
 def descriptor(self,unit):
  if not isinstance(unit,str)or not unit.strip():raise ValueError('volume field has no actual raw unit')
  return {'schema':'dcdtuni.common-volume-grid/v2','field':{'name':self.field,'unit':unit},
   'dimensions':list(self.dimensions),'originMeters':list(self.origin),'spacingMeters':list(self.spacing),
   'layout':'x-fastest-point-nodes','sentinel':self.sentinel,'scalarRange':list(self.scalar_range),
   'payloadSha256':self.payload_sha256,'payloadBytes':len(self.payload),'scalarOffset':self.scalar_offset,
   'validityMask':{'encoding':'uint8-01','offset':self.mask_offset,'byteCount':math.prod(self.dimensions),'sha256':hashlib.sha256(self.validity_nodes().tobytes()).hexdigest()}}
 def scalar_nodes(self):
  raw=np.frombuffer(self.payload,dtype='<f4',offset=self.scalar_offset,count=math.prod(self.dimensions))
  return raw.copy()
 def validity_nodes(self):
  return np.frombuffer(self.payload,dtype=np.uint8,offset=self.mask_offset,count=math.prod(self.dimensions))
 def array_xyz(self):
  # Existing CPP wire is x-fastest; Warp volume stores the explicit (i,j,k).
  return self.scalar_nodes().reshape(self.dimensions,order='F')
 def bounds(self):
  return self.origin,tuple(a+(n-1)*h for a,n,h in zip(self.origin,self.dimensions,self.spacing))

def _vec(value,name,positive=False):
 if not isinstance(value,list)or len(value)!=3:raise ValueError('invalid actual grid '+name)
 if any(type(v)not in(int,float)or not math.isfinite(v)or(positive and v<=0)for v in value):raise ValueError('invalid actual grid '+name)
 return tuple(float(v)for v in value)

def decode_common_volume(payload,request,metadata):
 if not isinstance(payload,bytes)or not 4<len(payload)<=MAX_GRID_BYTES:raise ValueError('actual grid payload exceeds capacity or is incomplete')
 size=struct.unpack_from('<I',payload)[0]
 if size>1024*1024 or 4+size>len(payload)or size%4:raise ValueError('invalid actual grid envelope')
 header=json.loads(payload[4:4+size].rstrip(b'\0'),parse_constant=lambda value:(_ for _ in ()).throw(ValueError('nonfinite grid metadata')))
 if not isinstance(header,dict)or header!=metadata:raise ValueError('actual grid metadata differs from original response')
 required={'dimensions','origin','spacing','scalarRange','sentinel','field','validityMask'}
 if set(header)!=required or not isinstance(request,dict)or set(request)!={'field','resolution'}:raise ValueError('actual common grid has missing/extra fields')
 dims=header['dimensions'];n=request['resolution']
 if not isinstance(dims,list)or len(dims)!=3 or any(type(value)is not int for value in dims):raise ValueError('actual grid dimensions must be integer triplets')
 # The current common numeric producer supports 16..512. A silently clamped
 # request cannot be a signed claim of a different grid; actual dimensions win.
 if type(n)is not int or not 16<=n<=512 or dims!=[n,n,n]:raise ValueError('actual grid dimensions differ from common resolution intent')
 origin=_vec(header['origin'],'origin');spacing=_vec(header['spacing'],'spacing',True)
 name=header['field']
 if not isinstance(name,str)or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*',name)or request['field']!=name:raise ValueError('actual grid field differs from common numeric request')
 sentinel=header['sentinel'];domain=header['scalarRange']
 if type(sentinel)not in(int,float)or not math.isfinite(sentinel)or sentinel>=0:raise ValueError('actual grid sentinel must be negative and finite')
 if not isinstance(domain,list)or len(domain)!=2 or any(type(v)not in(int,float)or not math.isfinite(v)for v in domain)or domain[0]>domain[1]:raise ValueError('actual grid scalar range is invalid')
 count=n**3;offset=4+size;mask_offset=offset+count*4
 mask_meta=header['validityMask']
 if not isinstance(mask_meta,dict)or set(mask_meta)!={'encoding','byteCount'}or mask_meta['encoding']!='uint8-01'or type(mask_meta['byteCount'])is not int or mask_meta['byteCount']!=count:
  raise ValueError('actual VTK validity descriptor is invalid')
 if mask_offset+count!=len(payload):raise ValueError('actual scalar/mask grid dimensions differ from byte count')
 values=np.frombuffer(payload,dtype='<f4',count=count,offset=offset)
 mask=np.frombuffer(payload,dtype=np.uint8,count=count,offset=mask_offset)
 if np.any((mask!=0)&(mask!=1)):raise ValueError('actual VTK validity mask is not binary')
 physical=values[mask==1]
 if not np.all(np.isfinite(physical)):raise ValueError('nonfinite valid actual scalar grid sample')
 actual=[float(physical.min()),float(physical.max())]if physical.size else[0.,0.]
 if actual!=domain:raise ValueError('actual scalar range differs from valid VTK samples')
 return CommonVolumeGrid(tuple(dims),origin,spacing,float(sentinel),tuple(float(v)for v in domain),name,hashlib.sha256(payload).hexdigest(),offset,mask_offset,payload)
