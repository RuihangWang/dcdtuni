"""Controlled numeric bytes contracts; not physical/backend acceptance."""
import copy,importlib.util,sys
from pathlib import Path
import pytest
MODULE=Path(__file__).resolve().parents[2]/'backend/aircooling/runner/numeric_product_receipts.py'
spec=importlib.util.spec_from_file_location('private_numeric_receipts',MODULE);mod=importlib.util.module_from_spec(spec);sys.modules[spec.name]=mod;spec.loader.exec_module(mod)
SESSION={'runId':'r','timeValue':.02,'revision':'rev'}

def store(**kw):return mod.NumericProductStore(SESSION,'a'*64,**kw)
def capture(s,kind='slice',payload=b'actual response',request=None,metadata=None):return s.capture(kind,request or{'origin':[1,2,3],'normal':[0,1,0],'field':'T'},payload,metadata or{'nPoints':4},session=SESSION,native_input_sha256='a'*64)

def test_owns_numeric_bytes_and_metadata_without_caller_mutation():
 s=store();payload=bytearray(b'original');request={'field':'T','origin':[1,2,3]};meta={'nPoints':4};receipt=capture(s,payload=payload,request=request,metadata=meta)
 payload[:]=b'changed!';request['origin'][0]=99;meta['nPoints']=999;receipt['request']['origin'][0]=88
 product=s.get(receipt['token'],session=SESSION,native_input_sha256='a'*64)
 assert product.payload==b'original';assert product.descriptor()['request']['origin']==[1,2,3];assert product.descriptor()['metadata']['nPoints']==4

@pytest.mark.parametrize('change',['runId','timeValue','revision','input','closed'])
def test_current_frame_source_and_lifetime_are_independent_guards(change):
 s=store();r=capture(s);session=copy.deepcopy(SESSION);digest='a'*64
 if change=='input':digest='b'*64
 elif change=='closed':s.close()
 elif change=='timeValue':session[change]=.01
 else:session[change]='older'
 with pytest.raises(ValueError):s.get(r['token'],session=session,native_input_sha256=digest)

@pytest.mark.parametrize('change',['field','plane','kind'])
def test_matching_bytes_cannot_certify_another_request_or_kind(change):
 s=store();r=capture(s);request=copy.deepcopy(r['request']);kind='slice'
 if change=='kind':kind='streamline'
 elif change=='field':request['field']='U'
 else:request['origin'][0]=9
 with pytest.raises(ValueError):s.get(r['token'],session=SESSION,native_input_sha256='a'*64,kind=kind,request=request)

def test_token_binds_binary_content_and_real_multiple_set_request_identity():
 s=store();a=capture(s,payload=b'a');b=capture(s,payload=b'b');c=capture(s,payload=b'a',request={'field':'T','origin':[9,2,3]})
 assert len({a['token'],b['token'],c['token']})==3
 assert s.get(a['token'],session=SESSION,native_input_sha256='a'*64).payload==b'a'
 assert s.get(b['token'],session=SESSION,native_input_sha256='a'*64).payload==b'b'

def test_expiry_and_eviction_do_not_promote_old_products():
 now=[0];s=store(max_bytes=4,max_entries=1,ttl_seconds=2,clock=lambda:now[0]);a=capture(s,payload=b'1234');b=capture(s,payload=b'abcd')
 with pytest.raises(ValueError,match='evicted'):s.get(a['token'],session=SESSION,native_input_sha256='a'*64)
 now[0]=3
 with pytest.raises(ValueError,match='expired'):s.get(b['token'],session=SESSION,native_input_sha256='a'*64)

@pytest.mark.parametrize('bad',[True,None,0,-1,float('nan')])
def test_no_implicit_latest_or_nonphysical_time(bad):
 session={**SESSION,'timeValue':bad}
 with pytest.raises(ValueError):mod.NumericProductStore(session,'a'*64)

@pytest.mark.parametrize('kind',['mesh','field','slice','streamline','volume'])
def test_same_common_frame_can_retain_all_existing_numeric_products(kind):
 s=store();r=capture(s,kind=kind);assert s.get(r['token'],session=SESSION,native_input_sha256='a'*64,kind=kind).kind==kind
