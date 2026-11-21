"""Lower ACTUAL common response products into a canonical renderer packet.

This decodes existing binary formats. It contains no triangulation, slicing,
streamline tracing, interpolation or resampling algorithm. Browser inputs may
choose presentation and public set identity; native numeric values/source come
only from the server-owned pinned product store and verified source descriptor.
"""
from __future__ import annotations
import json,math,struct
import numpy as np
from backend.aircooling.runner.numeric_product_receipts import canonical_bytes

PALETTES={'coolwarm','jet','viridis'}

def _keys(value,keys,label):
 if not isinstance(value,dict)or set(value)!=set(keys):raise ValueError(label+' has missing/extra fields')
 return value

def _number(value,label,*,low=None,high=None):
 if type(value)not in(int,float)or not math.isfinite(value)or(low is not None and value<low)or(high is not None and value>high):raise ValueError('invalid '+label)
 return value

def _integer(value,label,low=0):
 if type(value)is not int or not low<=value<=9007199254740991:raise ValueError('invalid '+label)
 return value

def _bool(value,label):
 if type(value)is not bool:raise ValueError('invalid '+label)
 return value

def _vec(value,label,nonzero=False):
 if not isinstance(value,list)or len(value)!=3:raise ValueError('invalid '+label)
 out=[_number(v,label)for v in value]
 if nonzero and not math.hypot(*out)>0:raise ValueError('zero '+label)
 return out

def _lut(value,allow_flat=False):
 _keys(value,['palette','range','opacity'],'common LUT')
 if value['palette']not in PALETTES:raise ValueError('unsupported common palette')
 domain=value['range']
 if not isinstance(domain,list)or len(domain)!=2:raise ValueError('invalid scalar domain')
 domain=[_number(v,'scalar domain')for v in domain]
 if allow_flat and any(abs(v)>3.4028234663852886e38 for v in domain):raise ValueError('volume domain exceeds finite shader scalar capacity')
 if domain[0]>domain[1]:raise ValueError('scalar domain must not descend')
 return {'palette':value['palette'],'range':domain,'opacity':_number(value['opacity'],'opacity',low=0,high=1)}

def _field(name,descriptor):
 unit=descriptor['fieldUnits'].get(name)
 if not isinstance(unit,str)or not unit.strip():raise ValueError('numeric product scalar field has no verified raw unit')
 if name=='T'and unit!='K':raise ValueError('raw OpenFOAM T does not use verified K')
 return {'name':name,'unit':unit}

def _array(payload,offset,count,dtype,label):
 if type(count)is not int or count<0:raise ValueError('invalid '+label+' count')
 end=offset+count*4
 if offset<0 or end>len(payload):raise ValueError(label+' exceeds numeric bytes')
 values=np.frombuffer(payload,dtype=dtype,count=count,offset=offset)
 if not np.all(np.isfinite(values)):raise ValueError('nonfinite '+label)
 return values,end

def _envelope(payload):
 if len(payload)<4:raise ValueError('short numeric envelope')
 length=struct.unpack_from('<I',payload)[0]
 if length>1024*1024 or length+4>len(payload):raise ValueError('invalid numeric header length')
 header=json.loads(payload[4:4+length].rstrip(b'\0'))
 if not isinstance(header,dict):raise ValueError('numeric header must be an object')
 return header,4+length

def _spans(mesh_product,descriptor):
 patches=json.loads(mesh_product.metadata_json)
 if not isinstance(patches,list):raise ValueError('mesh product has no patch table')
 names=set();spans=[];npoints=ntris=nedges=0
 for p in patches:
  name=p.get('name')
  if not isinstance(name,str)or not name or name in names:raise ValueError('duplicate/missing mesh patch')
  names.add(name)
  counts=[_integer(p[k],k)for k in ['nPoints','nTriangles','nEdges']]
  spans.append((p,npoints,ntris,nedges));npoints+=counts[0];ntris+=counts[1];nedges+=counts[2]
 if set(descriptor['boundaryProvenanceByPatch'])!=names:raise ValueError('actual mesh patch set differs from native provenance')
 positions,offset=_array(mesh_product.payload,0,npoints*3,'<f4','mesh positions')
 triangles,offset=_array(mesh_product.payload,offset,ntris*3,'<u4','mesh triangles')
 edges,offset=_array(mesh_product.payload,offset,nedges*2,'<u4','original polygon edges')
 if offset!=len(mesh_product.payload):raise ValueError('extra actual mesh bytes')
 for p,p0,t0,e0 in spans:
  for indices in [triangles[t0*3:(t0+p['nTriangles'])*3],edges[e0*2:(e0+p['nEdges'])*2]]:
   if np.any(indices<p0)or np.any(indices>=p0+p['nPoints']):raise ValueError('actual patch indices escape their point span')
 return spans,positions,triangles,edges,npoints

def _source_set_uid(raw,set_id,session,label):
 if not isinstance(raw,str):raise ValueError('public set has no identity')
 prefix=session['runId']+'/time-';suffix='/'+label+'-'+str(set_id)
 if not raw.startswith(prefix)or not raw.endswith(suffix):raise ValueError('public set belongs to another run/id')
 try:time=float(raw[len(prefix):-len(suffix)])
 except ValueError:raise ValueError('public set has no exact time')from None
 if time!=session['timeValue']:raise ValueError('public set belongs to another exact frame')
 return raw

def build_canonical_packet(intent,*,descriptor,store,volume_assets=None):
 _keys(intent,['schema','revision','postprocess','source','meshToken','surface','slices','streamlines','volume'],'common render intent')
 if intent['schema']!='dcdtuni.common-render-intent/v1':raise ValueError('unsupported common render intent')
 revision=_integer(intent['revision'],'display layer revision',1)
 session=descriptor['postprocess'];native=descriptor['nativeInputSha256']
 if intent['postprocess']!=session or intent['source']!=descriptor['source']:raise ValueError('common intent belongs to another accepted frame/source')
 def product(token,kind):return store.get(token,session=session,native_input_sha256=native,kind=kind)
 mesh=product(intent['meshToken'],'mesh');spans,positions,triangles,edges,npoints=_spans(mesh,descriptor)
 surface=_keys(intent['surface'],['fieldToken','field','lut','hidden','visible'],'common surface intent')
 scalar=product(surface['fieldToken'],'field')
 if json.loads(scalar.request_json)!={'field':surface['field']}:raise ValueError('actual scalar product belongs to another field')
 if len(scalar.payload)<16:raise ValueError('short actual scalar payload')
 _,_,components,count=struct.unpack_from('<ffII',scalar.payload)
 values,end=_array(scalar.payload,16,count*components,'<f4','surface field')
 if components!=1 or count!=npoints or end!=len(scalar.payload):raise ValueError('surface field/actual mesh dimensions differ')
 field=_field(surface['field'],descriptor);lut=_lut(surface['lut']);visible=_bool(surface['visible'],'surface visibility')
 hidden=surface['hidden']
 if not isinstance(hidden,list)or any(not isinstance(v,str)for v in hidden)or hidden!=sorted(set(hidden))or not set(hidden)<=set(descriptor['boundaryProvenanceByPatch']):raise ValueError('hidden patch list differs from actual mesh')
 layers=[]
 # Preserve common producer order: all original edges, then all raw surfaces.
 for kind in ['meshEdges','surfaceField']:
  for p,p0,t0,e0 in spans:
   name=p['name'];count=p['nEdges']if kind=='meshEdges'else p['nTriangles']
   if name in hidden or count==0:continue
   layer={'kind':kind,'layerId':('meshEdges/'if kind=='meshEdges'else'surfaceField/'+field['name']+'/')+name,
    'visible':False if kind=='meshEdges'else visible,'meshPatchName':name,
    'boundaryProvenance':descriptor['boundaryProvenanceByPatch'][name],
    'positions':positions[p0*3:(p0+p['nPoints'])*3].tolist()}
   indices=edges[e0*2:(e0+count)*2]if kind=='meshEdges'else triangles[t0*3:(t0+count)*3]
   layer['indices']=(indices.astype(np.int64)-p0).tolist()
   if kind=='meshEdges':layer['opacity']=.85
   else:layer.update({'values':values[p0:p0+p['nPoints']].tolist(),'field':field,'lut':lut})
   layers.append(layer)
 for kind,rows,label in [('slice',intent['slices'],'slice'),('streamline',intent['streamlines'],'seed')]:
  if not isinstance(rows,list):raise ValueError('public '+kind+' list is not a list')
  seen=set()
  for row in rows:
   required=['setId','token','sourceUid','lut','visible']+(['tubeRadiusMeters']if kind=='streamline'else[])
   _keys(row,required,'public '+kind+' intent');set_id=_integer(row['setId'],'public set id')
   if set_id in seen:raise ValueError('duplicate public '+kind+' set id')
   seen.add(set_id);uid=_source_set_uid(row['sourceUid'],set_id,session,label);shown=_bool(row['visible'],'public '+kind+' visibility')
   # Hidden products still retain identity; lack of visibility cannot validate
   # a stale or another-source token by omission.
   item=product(row['token'],kind);header,offset=_envelope(item.payload);request=json.loads(item.request_json)
   raw_field=header['field'];sf=_field(raw_field,descriptor);slut=_lut(row['lut'])
   if request.get('field')!=raw_field:raise ValueError('actual cut/stream field differs from stored request')
   if kind=='slice':
    origin=_vec(request['origin'],'actual slice origin');normal=_vec(request['normal'],'actual slice normal',True)
    npnt=_integer(header['nPoints'],'slice point count');ntri=_integer(header['nTriangles'],'slice triangle count')
    pos,offset=_array(item.payload,offset,npnt*3,'<f4','slice positions');ind,offset=_array(item.payload,offset,ntri*3,'<u4','slice triangles');val,offset=_array(item.payload,offset,npnt,'<f4','slice values')
    if np.any(ind>=npnt):raise ValueError('actual slice index escapes points')
    layer={'kind':kind,'layerId':'slice/'+uid,'visible':True,'sourceSliceUid':uid,'originMeters':origin,'normal':normal,'positions':pos.tolist(),'indices':ind.tolist(),'values':val.tolist(),'field':sf,'lut':slut}
   else:
    radius=_number(row['tubeRadiusMeters'],'streamline tube radius',low=0)
    if radius<=0:raise ValueError('streamline tube radius must be positive')
    lines=[]
    for line in header['streamlines']:
     count=_integer(line['nPoints'],'streamline point count',2);quad,offset=_array(item.payload,offset,count*4,'<f4','streamline point/scalars');quad=quad.reshape((count,4));lines.append({'positions':quad[:,:3].ravel().tolist(),'values':quad[:,3].tolist()})
    layer={'kind':kind,'layerId':'streamline/'+uid+'/'+raw_field,'visible':True,'sourceSeedUid':uid,'lines':lines,'field':sf,'lut':slut,'tubeRadiusMeters':radius}
   if offset!=len(item.payload):raise ValueError('extra numeric product bytes')
   if shown and ((kind=='slice'and npnt>0 and ntri>0)or(kind=='streamline'and lines)):layers.append(layer)
 volume=None
 if intent['volume']is not None:
  if __package__:
   from .common_volume_grid import common_volume_intent
  else:
   from common_volume_grid import common_volume_intent
  volume_intent=_keys(intent['volume'],['field','unit','token','resolution','lut','visible'],'common volume intent')
  grid,volume=common_volume_intent(volume_intent,product(volume_intent['token'],'volume'),descriptor)
  name=grid.payload_sha256+'.volume'
  volume['artifactName']=name
  volume['field']=volume['grid']['field']
  if volume_assets is not None:volume_assets[name]=grid.payload
 packet={'schema':'dcdtuni.render-result-layers/v2','revision':revision,'source':descriptor['source'],
  'coordinateSystem':{'upAxis':'Y','metersPerUnit':1,'handedness':'right'},'timeSeconds':session['timeValue'],'layers':layers,'runConvergence':descriptor['runConvergence']}
 if volume is not None:
  packet['schema']='dcdtuni.render-result-layers/v3';packet['volume']=volume
 canonical_bytes(packet) # finite canonical JSON; no shader/scene implementation state.
 return packet
