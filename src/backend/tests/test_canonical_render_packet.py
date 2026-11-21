"""Controlled actual binary-format lowering, not physical acceptance."""
import copy,json,struct
import numpy as np
import pytest
from backend.aircooling.runner.numeric_product_receipts import NumericProductStore,canonical_bytes
from server.canonical_render_packet import build_canonical_packet

SESSION={'runId':'a'*64,'timeValue':.02,'revision':'controlled-revision'}
SOURCE={'projectUid':'controlled-project','projectContentSha256':'b'*64,'cfdrunSha256':'c'*64,'meshReceiptSha256':'d'*64,'meshQuality':'accepted','run':{'uid':'controlled-run','receiptSha256':'e'*64}}
PROOF={'schema':'dcdtuni.native-boundary-provenance/v1','patchRanges':[{'bindingKind':'meshBoundaryOnly','rank':0,'patchIndex':0,'nativePatchName':'wall','startFace':4,'nFaces':1,'boundarySha256':'f'*64,'sourceRegions':[]}]}
DESC={'schema':'dcdtuni.verified-air-result-source/v2','source':SOURCE,'postprocess':SESSION,'nativeInputSha256':'1'*64,'boundaryProvenanceByPatch':{'wall':PROOF},'fieldUnits':{'T':'K','speed':'m/s'},'runConvergence':{'verdict':'NOT_CONVERGED','receiptSha256':'2'*64}}
LUT={'palette':'viridis','range':[290,310],'opacity':.6}

def envelope(header,data):
 raw=json.dumps(header).encode();raw+=b'\0'*((-len(raw))%4)
 return struct.pack('<I',len(raw))+raw+data

def fixture():
 store=NumericProductStore(SESSION,'1'*64)
 def product(kind,request,payload,metadata):return store.capture(kind,request,payload,metadata,session=SESSION,native_input_sha256='1'*64)['token']
 mesh=np.asarray([0,0,0,1,0,0,0,1,0],dtype='<f4').tobytes()+np.asarray([0,1,2,0,1,1,2,2,0],dtype='<u4').tobytes()
 mesh_token=product('mesh',{},mesh,[{'name':'wall','nPoints':3,'nTriangles':1,'nEdges':3}])
 field=struct.pack('<ffII',300,302,1,3)+np.asarray([300,301,302],dtype='<f4').tobytes()
 field_token=product('field',{'field':'T'},field,{'nPoints':3})
 header={'nPoints':3,'nTriangles':1,'scalarRange':[280,282],'field':'T'}
 binary=np.asarray([0,0,0,1,0,0,0,1,0],dtype='<f4').tobytes()+np.asarray([0,1,2],dtype='<u4').tobytes()+np.asarray([280,281,282],dtype='<f4').tobytes()
 slice_token=product('slice',{'normal':[0,0,1],'origin':[0,0,0],'field':'T'},envelope(header,binary),header)
 header={'streamlines':[{'nPoints':2}],'scalarRange':[300,301],'field':'T'}
 stream_token=product('streamline',{'center':[.5,.5,.5],'radius':.2,'nSeeds':1,'field':'T'},envelope(header,np.asarray([1,2,3,300,4,5,6,301],dtype='<f4').tobytes()),header)
 intent={'schema':'dcdtuni.common-render-intent/v1','revision':4,'postprocess':copy.deepcopy(SESSION),'source':copy.deepcopy(SOURCE),'meshToken':mesh_token,
  'surface':{'fieldToken':field_token,'field':'T','lut':copy.deepcopy(LUT),'hidden':[],'visible':True},
  'slices':[{'setId':3,'token':slice_token,'sourceUid':SESSION['runId']+'/time-0.02/slice-3','lut':copy.deepcopy(LUT),'visible':True}],
  'streamlines':[{'setId':7,'token':stream_token,'sourceUid':SESSION['runId']+'/time-0.02/seed-7','lut':copy.deepcopy(LUT),'visible':True,'tubeRadiusMeters':.02}], 'volume':None}
 return store,intent

def build(store,intent,descriptor=DESC):return build_canonical_packet(intent,descriptor=descriptor,store=store)

def test_preserves_actual_binary_values_original_edges_units_and_public_set_identities():
 store,intent=fixture();packet=build(store,intent)
 assert packet['schema']=='dcdtuni.render-result-layers/v2' and packet['timeSeconds']==.02
 edges,surface,cut,stream=packet['layers']
 assert edges['indices']==[0,1,1,2,2,0] and edges['visible']is False
 assert surface['values']==[300,301,302] and surface['field']=={'name':'T','unit':'K'}
 assert cut['values']==[280,281,282]and cut['originMeters']==[0,0,0]
 assert stream['lines']==[{'positions':[1,2,3,4,5,6],'values':[300,301]}]
 assert '/slice-3'in cut['layerId'] and '/seed-7/'in stream['layerId']
 assert surface['boundaryProvenance']==PROOF and 'sourceFaceUids'not in surface
 assert all(layer.get('lut',LUT)==LUT for layer in packet['layers'])

@pytest.mark.parametrize('change',['source','frame','revision','field','hidden_unknown','hidden_duplicate','wrong_mesh_token','wrong_field_token','wrong_slice_token','wrong_stream_token','set_time','set_run','duplicate_slice_id','duplicate_stream_id','palette','domain','opacity','normal_extra','volume_unimplemented'])
def test_independent_input_identity_or_presentation_corruption_is_rejected(change):
 store,intent=fixture()
 if change=='source':intent['source']['cfdrunSha256']='0'*64
 elif change=='frame':intent['postprocess']['timeValue']=.01
 elif change=='revision':intent['postprocess']['revision']='old'
 elif change=='field':intent['surface']['field']='speed'
 elif change=='hidden_unknown':intent['surface']['hidden']=['other']
 elif change=='hidden_duplicate':intent['surface']['hidden']=['wall','wall']
 elif change=='wrong_mesh_token':intent['meshToken']=intent['surface']['fieldToken']
 elif change=='wrong_field_token':intent['surface']['fieldToken']=intent['slices'][0]['token']
 elif change=='wrong_slice_token':intent['slices'][0]['token']=intent['streamlines'][0]['token']
 elif change=='wrong_stream_token':intent['streamlines'][0]['token']=intent['slices'][0]['token']
 elif change=='set_time':intent['slices'][0]['sourceUid']=intent['slices'][0]['sourceUid'].replace('0.02','0.01')
 elif change=='set_run':intent['streamlines'][0]['sourceUid']='other/time-0.02/seed-7'
 elif change=='duplicate_slice_id':intent['slices'].append(copy.deepcopy(intent['slices'][0]))
 elif change=='duplicate_stream_id':intent['streamlines'].append(copy.deepcopy(intent['streamlines'][0]))
 elif change=='palette':intent['surface']['lut']['palette']='not-common'
 elif change=='domain':intent['surface']['lut']['range']=[301,300]
 elif change=='opacity':intent['surface']['lut']['opacity']=2
 elif change=='normal_extra':intent['slices'][0]['normal']=[0,1,0]
 else:intent['volume']={'visible':True}
 with pytest.raises((ValueError,KeyError)):build(store,intent)


def test_multiple_public_sets_same_numeric_product_keep_individual_identity_visibility():
 store,intent=fixture();another=copy.deepcopy(intent['slices'][0]);another['setId']=9;another['sourceUid']=another['sourceUid'].replace('slice-3','slice-9');intent['slices'].append(another)
 another=copy.deepcopy(intent['streamlines'][0]);another['setId']=11;another['sourceUid']=another['sourceUid'].replace('seed-7','seed-11');intent['streamlines'].append(another)
 packet=build(store,intent);assert len(packet['layers'])==6
 assert len({p['layerId']for p in packet['layers']})==6
 intent['slices'][0]['visible']=False;intent['streamlines'][1]['visible']=False
 packet=build(store,intent);assert len(packet['layers'])==4
 assert any('/slice-9'in p['layerId']for p in packet['layers'])
 assert any('/seed-7/'in p['layerId']for p in packet['layers'])


def test_hidden_patch_still_requires_full_provenance_and_current_numeric_token():
 store,intent=fixture();intent['surface']['hidden']=['wall'];intent['slices']=[];intent['streamlines']=[]
 assert build(store,intent)['layers']==[]
 stale=copy.deepcopy(DESC);stale['boundaryProvenanceByPatch']={}
 with pytest.raises(ValueError):build(store,intent,stale)
 intent['surface']['fieldToken']='missing'
 with pytest.raises(ValueError):build(store,intent)

@pytest.mark.parametrize('change',['nonfinite','indices','field_dimension','trailing_bytes'])
def test_actual_numeric_byte_corruption_is_rejected_even_when_product_token_exists(change):
 store,intent=fixture()
 if change=='field_dimension':
  binary=struct.pack('<ffII',300,302,1,2)+np.asarray([300,301],dtype='<f4').tobytes();kind='field';request={'field':'T'};meta={'nPoints':2}
 elif change=='trailing_bytes':
  original=store.get(intent['meshToken'],session=SESSION,native_input_sha256='1'*64);binary=original.payload+b'\0';kind='mesh';request={};meta=json.loads(original.metadata_json)
 else:
  original=store.get(intent['meshToken'],session=SESSION,native_input_sha256='1'*64);binary=bytearray(original.payload);kind='mesh';request={};meta=json.loads(original.metadata_json)
  if change=='nonfinite':struct.pack_into('<f',binary,0,float('nan'))
  else:struct.pack_into('<I',binary,36,99)
 token=store.capture(kind,request,binary,meta,session=SESSION,native_input_sha256='1'*64)['token']
 if kind=='field':intent['surface']['fieldToken']=token
 else:intent['meshToken']=token
 with pytest.raises(ValueError):build(store,intent)

def test_genuine_empty_numeric_cuts_and_traces_keep_token_identity_without_invalid_usd_layers():
 store,intent=fixture()
 for kind,rows,header,request in [
  ('slice',intent['slices'],{'nPoints':0,'nTriangles':0,'scalarRange':[0,0],'field':'T'},{'normal':[0,0,1],'origin':[0,0,0],'field':'T'}),
  ('streamline',intent['streamlines'],{'streamlines':[],'scalarRange':[0,0],'field':'T'},{'center':[0,0,0],'radius':.2,'nSeeds':1,'field':'T'})]:
  rows[0]['token']=store.capture(kind,request,envelope(header,b''),header,session=SESSION,native_input_sha256=DESC['nativeInputSha256'])['token']
 assert [layer['kind']for layer in build(store,intent)['layers']]==['meshEdges','surfaceField']
 intent['slices'][0]['token']='0'*64
 with pytest.raises(ValueError):build(store,intent)
