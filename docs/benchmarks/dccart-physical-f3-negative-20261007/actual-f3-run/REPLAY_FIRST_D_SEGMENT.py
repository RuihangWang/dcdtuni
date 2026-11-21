from pathlib import Path
import json,struct,math,hashlib
F=Path(__file__).resolve().parent
m=json.loads((F/'first-determinant-map.json').read_text(),parse_int=int)
bindings=json.loads((F/'source/bindings.json').read_text())
b=(F/'source/pointHard-D-segment.txt').read_bytes()
assert hashlib.sha256(b).hexdigest()==bindings['sourceFragment']['sha256']
assert m['complete']==1 and m['oldRefusal']==m['nextRefusal']==0
assert m['oldCells']==m['nextCells'] and m['oldMetrics']==m['nextMetrics']
assert m['oldDeterminants']==m['nextDeterminants']==m['pairCount']==len(m['pairs'])
oldD=0;newD=0
for p in m['pairs']:
 assert p['oldPresent'] and p['nextPresent']
 a=struct.unpack('=d',struct.pack('=Q',p['oldBits']))[0]
 b=struct.unpack('=d',struct.pack('=Q',p['nextBits']))[0]
 assert math.isfinite(a) and math.isfinite(b) and a>=0 and b>=0
 assert not(a>=.001 and b<.001),'Earlier H38 failure'
 oldD+=a<.001;newD+=b<.001
assert newD>=oldD
print(json.dumps({'source':'actual frozen H30-42 D segment only','firstFailure':'H42 first operand newD>=oldD','oldD':oldD,'newD':newD,'passingDRegression':False,'faceLoopNotReached':True}))
