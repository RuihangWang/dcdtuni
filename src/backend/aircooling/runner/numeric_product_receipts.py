"""Receipts of the actual common postprocess numeric response bytes.

This module performs no slice/streamline/volume algorithm and accepts no browser
provenance. A server route pins its CPP revision, records the binary it actually
returns, and a later canonical author retrieves precisely that owned product.
Physical acceptance remains the independent source/execution/quality verifier.
"""
from __future__ import annotations
from dataclasses import dataclass
from collections import OrderedDict
import hashlib,json,math,threading,time
from pathlib import Path

PRODUCT_KINDS=frozenset({'mesh','field','slice','streamline','volume'})

def canonical_bytes(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode('ascii')

def sha256(data):return hashlib.sha256(data).hexdigest()

def require_session(session):
    if not isinstance(session,dict)or set(session)!={'runId','timeValue','revision'}:
        raise ValueError('numeric product requires exact common postprocess identity')
    if any(not isinstance(session[k],str)or not session[k]for k in ('runId','revision')):
        raise ValueError('numeric product has no common run/revision')
    value=session['timeValue']
    if type(value)not in(int,float)or not math.isfinite(value)or value<=0:
        raise ValueError('numeric product requires one positive exact physical frame')
    return json.loads(canonical_bytes(session))

@dataclass(frozen=True)
class NumericProduct:
    token:str
    identity_json:bytes
    request_json:bytes
    metadata_json:bytes
    kind:str
    payload:bytes
    captured_at:float
    input_sha256:str
    def descriptor(self):
        return {'schema':'dcdtuni.numeric-product-receipt/v1','token':self.token,
                'kind':self.kind,'postprocess':json.loads(self.identity_json),
                'request':json.loads(self.request_json),'metadata':json.loads(self.metadata_json),
                'payloadSha256':sha256(self.payload),'nativeInputSha256':self.input_sha256}

class NumericProductStore:
    """One native loaded frame. Its products cannot cross a CPP generation.

    Binary bytes are immutable and retained by reference when already bytes;
    metadata/request trees are canonical owned copies. Eviction never certifies
    a missing product: a caller must re-fetch through the common numeric route.
    """
    def __init__(self,session,native_input_sha256,*,max_bytes=768*1024*1024,max_entries=128,ttl_seconds=900,clock=time.monotonic):
        self._identity=canonical_bytes(require_session(session))
        if not isinstance(native_input_sha256,str)or len(native_input_sha256)!=64 or any(c not in'0123456789abcdef'for c in native_input_sha256):
            raise ValueError('numeric product requires captured native input SHA')
        if type(max_bytes)is not int or max_bytes<=0 or type(max_entries)is not int or max_entries<=0 or not math.isfinite(ttl_seconds)or ttl_seconds<=0:
            raise ValueError('invalid numeric product capacity/lifetime')
        self.input_sha256=native_input_sha256;self._limit=max_bytes;self._entries=max_entries;self._ttl=ttl_seconds;self._clock=clock
        self._products=OrderedDict();self._bytes=0;self._lock=threading.RLock();self._closed=False
    def _current(self,session,input_sha):
        if self._closed or canonical_bytes(require_session(session))!=self._identity or input_sha!=self.input_sha256:
            raise ValueError('numeric product belongs to an older common frame/input')
    def capture(self,kind,request,payload,metadata,*,session,native_input_sha256):
        with self._lock:
            self._current(session,native_input_sha256)
            if kind not in PRODUCT_KINDS or not isinstance(request,dict)or not isinstance(metadata,(dict,list)):
                raise ValueError('unsupported actual numeric product')
            if not isinstance(payload,(bytes,bytearray,memoryview)):
                raise TypeError('numeric response payload must be bytes')
            owned=bytes(payload)
            if len(owned)>self._limit:raise ValueError('numeric product exceeds retained byte capacity')
            request_json=canonical_bytes(request);metadata_json=canonical_bytes(metadata)
            token=sha256(canonical_bytes({'schema':'dcdtuni.numeric-product-receipt/v1','identity':json.loads(self._identity),'kind':kind,'request':json.loads(request_json),'metadata':json.loads(metadata_json),'payloadSha256':sha256(owned),'nativeInputSha256':self.input_sha256}))
            if token in self._products:self._bytes-=len(self._products.pop(token).payload)
            while self._products and (len(self._products)>=self._entries or self._bytes+len(owned)>self._limit):
                _,expired=self._products.popitem(last=False);self._bytes-=len(expired.payload)
            product=NumericProduct(token,self._identity,request_json,metadata_json,kind,owned,float(self._clock()),self.input_sha256)
            self._products[token]=product;self._bytes+=len(owned)
            return product.descriptor()
    def get(self,token,*,session,native_input_sha256,kind=None,request=None):
        with self._lock:
            self._current(session,native_input_sha256);product=self._products.get(token)
            if product is None:raise ValueError('numeric product is absent or has been evicted')
            now=float(self._clock())
            if not math.isfinite(now)or now<product.captured_at or now-product.captured_at>self._ttl:
                raise ValueError('numeric product receipt expired')
            if kind is not None and product.kind!=kind:raise ValueError('numeric product kind differs')
            if request is not None and product.request_json!=canonical_bytes(request):raise ValueError('numeric product request differs')
            return product
    def close(self):
        with self._lock:self._closed=True;self._products.clear();self._bytes=0

from backend.aircooling.runner.native_numeric_input import capture_native_inputs
