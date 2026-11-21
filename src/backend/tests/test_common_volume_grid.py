"""Real existing CPP grid bytes and layout checks; CPU fixture, not CFD acceptance."""
import copy,json,struct
import numpy as np
import pytest
from server.common_volume_grid import decode_common_volume,common_volume_intent
from backend.aircooling.runner.numeric_product_receipts import NumericProductStore
from backend.aircooling.runner.postprocess import CachedPostProcessor

SESSION={'runId':'a'*64,'timeValue':.02,'revision':'controlled-grid'}

def fixture():
 pv=pytest.importorskip('pyvista')
 # Exact analytic nodal ramp on a non-unit, anisotropic and translated domain.
 data=pv.ImageData(dimensions=(3,3,3),origin=(1,2,3),spacing=(.5,1,1.5))
 xyz=np.asarray(data.points);data.point_data['T']=(300+xyz[:,0]+2*xyz[:,1]+3*xyz[:,2]).astype(np.float32)
 cpp=CachedPostProcessor();cpp._internal=data.cast_to_unstructured_grid()
 payload,meta=cpp.get_volume('T',16)
 return payload,meta

def store_product(payload,meta,request=None):
 request=request or{'field':'T','resolution':16};store=NumericProductStore(SESSION,'b'*64)
 token=store.capture('volume',request,payload,meta,session=SESSION,native_input_sha256='b'*64)['token']
 return store.get(token,session=SESSION,native_input_sha256='b'*64,kind='volume')

def test_actual_common_cpp_grid_preserves_node_origin_spacing_raw_values_and_x_fastest_order():
 payload,meta=fixture();grid=decode_common_volume(payload,{'field':'T','resolution':16},meta)
 assert grid.dimensions==(16,16,16)and grid.origin==(1,2,3)
 np.testing.assert_allclose(grid.spacing,[1/15,2/15,3/15])
 values=grid.array_xyz();assert values.shape==(16,16,16)
 for i,j,k in [(0,0,0),(15,15,15),(5,8,11),(1,0,0),(0,1,0),(0,0,1)]:
  x,y,z=[a+n*h for a,n,h in zip(grid.origin,[i,j,k],grid.spacing)]
  assert values[i,j,k]==pytest.approx(300+x+2*y+3*z,abs=4e-5)
  assert grid.scalar_nodes()[i+16*j+16*16*k]==values[i,j,k]
 assert grid.bounds()==((1.,2.,3.),(2.,4.,6.))
 assert grid.payload is payload

@pytest.mark.parametrize('change',['field','unit','resolution','kind','token','palette','opacity','extra'])
def test_common_ui_volume_parameters_never_drop_or_cross_numeric_receipt(change):
 payload,meta=fixture();product=store_product(payload,meta)
 intent={'field':'T','unit':'K','token':product.token,'resolution':16,'lut':{'palette':'viridis','range':[315,330],'opacity':.17},'visible':True};descriptor={'fieldUnits':{'T':'K'}}
 if change=='field':intent['field']='speed'
 elif change=='unit':intent['unit']='degC'
 elif change=='resolution':intent['resolution']=128
 elif change=='kind':product=store_product(payload,meta);object.__setattr__(product,'kind','slice')
 elif change=='token':intent['token']='c'*64
 elif change=='palette':intent['lut']['palette']='demo-only'
 elif change=='opacity':intent['lut']['opacity']=2
 else:intent['hiddenScalarOverride']=0
 with pytest.raises(ValueError):common_volume_intent(intent,product,descriptor)

@pytest.mark.parametrize('change',['dimensions','clamped_request','spacing','origin','sentinel','nan_header','trailing','infinite_sample','metadata','field_header'])
def test_corrupt_binary_grid_or_silent_numeric_clamp_cannot_get_a_signed_grid_claim(change):
 payload,meta=fixture();request={'field':'T','resolution':16};header=copy.deepcopy(meta)
 if change=='dimensions':header['dimensions']=[16,16,17]
 elif change=='clamped_request':request['resolution']=2
 elif change=='spacing':header['spacing'][1]=0
 elif change=='origin':header['origin'][0]=float('nan')
 elif change=='sentinel':header['sentinel']=0
 elif change=='nan_header':header['scalarRange'][0]=float('nan')
 elif change=='metadata':meta['scalarRange'][0]+=1
 elif change=='field_header':header['field']='U'
 if change not in ['metadata','trailing','infinite_sample']:
  size=struct.unpack_from('<I',payload)[0];raw=json.dumps(header).encode();raw+=b'\0'*((-len(raw))%4);payload=struct.pack('<I',len(raw))+raw+payload[4+size:];meta=header
 elif change=='trailing':payload+=b'\0'
 elif change=='infinite_sample':
  changed=bytearray(payload);size=struct.unpack_from('<I',payload)[0];struct.pack_into('<f',changed,4+size,float('inf'));payload=bytes(changed)
 with pytest.raises(ValueError):decode_common_volume(payload,request,meta)

def test_nonfinite_valid_node_cannot_be_signed_but_original_masked_nan_bytes_are_preserved():
 payload,meta=fixture();changed=bytearray(payload);size=struct.unpack_from('<I',payload)[0]
 struct.pack_into('<f',changed,4+size,float('nan'))
 with pytest.raises(ValueError,match='nonfinite valid'):decode_common_volume(bytes(changed),{'field':'T','resolution':16},meta)
 # A masked original value is never reconstructed from scalar equality.
 changed[4+size+16**3*4]=0
 remaining=np.frombuffer(changed,dtype='<f4',count=16**3,offset=4+size)[1:]
 meta['scalarRange']=[float(remaining.min()),float(remaining.max())]
 head=json.dumps(meta).encode();head+=b'\0'*((-len(head))%4)
 raw=struct.pack('<I',len(head))+head+changed[4+size:]
 grid=decode_common_volume(raw,{'field':'T','resolution':16},meta)
 assert np.isnan(grid.scalar_nodes()[0])and grid.validity_nodes()[0]==0
 assert grid.payload==raw


def test_full_public_volume_intent_preserves_palette_range_opacity_resolution_and_raw_unit():
 payload,meta=fixture();product=store_product(payload,meta)
 intent={'field':'T','unit':'K','token':product.token,'resolution':16,'lut':{'palette':'jet','range':[318,325],'opacity':.31},'visible':False}
 grid,lowered=common_volume_intent(intent,product,{'fieldUnits':{'T':'K'}})
 assert lowered['lut']==intent['lut']and lowered['visible']is False
 assert lowered['grid']['dimensions']==[16]*3 and lowered['grid']['field']=={'name':'T','unit':'K'}
 assert lowered['grid']['layout']=='x-fastest-point-nodes'
