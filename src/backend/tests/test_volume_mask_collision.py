"""Actual existing CPP validity collision, on controlled disconnected volume."""
import numpy as np
from backend.aircooling.runner.postprocess import CachedPostProcessor

def actual_disconnected_scalar_product(value=-9999.):
 import pyvista as pv
 first=pv.ImageData(dimensions=(2,2,2),spacing=(1,1,1),origin=(0,0,0)).cast_to_unstructured_grid()
 second=pv.ImageData(dimensions=(2,2,2),spacing=(1,1,1),origin=(2,0,0)).cast_to_unstructured_grid()
 source=first.merge(second)
 source.point_data['p']=np.full(source.n_points,value,dtype=np.float32)
 cpp=CachedPostProcessor();cpp._internal=source
 return cpp.get_volume('p',16)

def test_valid_native_pressure_equal_to_old_sentinel_remains_a_physical_sample():
 raw,metadata=actual_disconnected_scalar_product()
 assert metadata['scalarRange']==[-9999.,-9999.]
 assert metadata['validityMask']=={'encoding':'uint8-01','byteCount':16**3}

import copy,json,struct,hashlib,pytest
from server.common_volume_grid import decode_common_volume,common_volume_intent
from backend.aircooling.runner.numeric_product_receipts import NumericProductStore

def test_actual_original_vtk_mask_preserves_inside_collision_and_outside_hole_separately():
 raw,metadata=actual_disconnected_scalar_product();grid=decode_common_volume(raw,{'field':'p','resolution':16},metadata)
 mask=grid.validity_nodes();scalars=grid.scalar_nodes()
 assert set(mask)=={0,1} and np.all(scalars[mask==1]==-9999)and np.all(scalars[mask==0]==-9999)
 # The gap is real native topology, not fabricated from a scalar threshold.
 lattice=mask.reshape((16,16,16),order='F');assert np.all(lattice[7:9]==0)and np.all(lattice[0]==1)and np.all(lattice[-1]==1)
 desc=grid.descriptor('Pa');assert desc['schema']=='dcdtuni.common-volume-grid/v2'
 assert desc['validityMask']['sha256']==hashlib.sha256(mask.tobytes()).hexdigest()
 assert desc['payloadSha256']==hashlib.sha256(raw).hexdigest()

@pytest.mark.parametrize('mutation',['mask-value','mask-short','mask-long','mask-count','mask-count-bool','mask-encoding','mask-offset-extra','domain-bool','domain-nan','origin-bool','spacing-bool','range-false','no-mask'])
def test_mask_binary_domain_and_envelope_cannot_gain_numeric_authority(mutation):
 raw,meta=actual_disconnected_scalar_product();head_size=struct.unpack_from('<I',raw)[0];data=bytearray(raw[4+head_size:]);meta=copy.deepcopy(meta)
 if mutation=='mask-value':data[16**3*4]=2
 elif mutation=='mask-short':data.pop()
 elif mutation=='mask-long':data.append(1)
 elif mutation=='mask-count':meta['validityMask']['byteCount']-=1
 elif mutation=='mask-count-bool':meta['validityMask']['byteCount']=True
 elif mutation=='mask-encoding':meta['validityMask']['encoding']='scalar-threshold'
 elif mutation=='mask-offset-extra':meta['validityMask']['offset']=0
 elif mutation=='domain-bool':meta['scalarRange'][0]=False
 elif mutation=='domain-nan':meta['scalarRange'][0]=float('nan')
 elif mutation=='origin-bool':meta['origin'][0]=True
 elif mutation=='spacing-bool':meta['spacing'][0]=True
 elif mutation=='range-false':meta['scalarRange']=[0,0]
 else:del meta['validityMask'];data=data[:16**3*4]
 header=json.dumps(meta).encode();header+=b'\0'*((-len(header))%4);payload=struct.pack('<I',len(header))+header+data
 with pytest.raises(ValueError):decode_common_volume(bytes(payload),{'field':'p','resolution':16},meta)

@pytest.mark.parametrize('value',[-9999.,-200.,295.15,0.])
def test_literal_equal_domain_opacity_palette_and_original_mask_survive_common_lowering(value):
 raw,meta=actual_disconnected_scalar_product(value);session={'runId':'a'*64,'timeValue':.02,'revision':'actual-mask-controlled'}
 store=NumericProductStore(session,'b'*64);token=store.capture('volume',{'field':'p','resolution':16},raw,meta,session=session,native_input_sha256='b'*64)['token'];product=store.get(token,session=session,native_input_sha256='b'*64,kind='volume')
 intent={'field':'p','unit':'Pa','token':token,'resolution':16,'lut':{'palette':'jet','range':meta['scalarRange'],'opacity':.31},'visible':True}
 grid,lowered=common_volume_intent(intent,product,{'fieldUnits':{'p':'Pa'}})
 assert lowered['lut']==intent['lut']and lowered['grid']['scalarRange']==meta['scalarRange']
 assert np.all(grid.scalar_nodes()[grid.validity_nodes()==1]==np.float32(value))

@pytest.mark.parametrize('domain',[[True,1],[0,False],[float('nan'),1],[0,float('inf')],[0,1e300],[2,1]])
def test_nonphysical_lut_domains_fail_even_for_masked_products(domain):
 raw,meta=actual_disconnected_scalar_product();session={'runId':'a'*64,'timeValue':.02,'revision':'actual-mask-controlled'}
 store=NumericProductStore(session,'b'*64);token=store.capture('volume',{'field':'p','resolution':16},raw,meta,session=session,native_input_sha256='b'*64)['token'];product=store.get(token,session=session,native_input_sha256='b'*64,kind='volume')
 intent={'field':'p','unit':'Pa','token':token,'resolution':16,'lut':{'palette':'jet','range':domain,'opacity':.31},'visible':True}
 with pytest.raises(ValueError):common_volume_intent(intent,product,{'fieldUnits':{'p':'Pa'}})
