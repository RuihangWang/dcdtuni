from pathlib import Path,PurePosixPath
import hashlib,json,tarfile
base=Path(__file__).resolve().parent
sha=lambda b:hashlib.sha256(b).hexdigest()
m=json.loads((base/'MANIFEST.json').read_text())
for e in m['payload']:
 p=base/e['path'];assert not p.is_symlink();b=p.read_bytes();assert len(b)==e['bytes'] and sha(b)==e['sha256'],e['path']
counts=[]
for entry in m['archiveIndexes']:
 idx=json.loads((base/entry).read_text());expected={e['archiveMember']:e for e in idx['members']};seen=set()
 with tarfile.open(base/idx['archivePath'],'r:gz') as tar:
  for t in tar:
   p=PurePosixPath(t.name);assert not p.is_absolute() and '..' not in p.parts and '.' not in p.parts and chr(92) not in t.name
   assert t.isfile() and t.name not in seen and t.name in expected;seen.add(t.name)
   b=tar.extractfile(t).read();e=expected[t.name];assert len(b)==e['bytes'] and sha(b)==e['sha256'],t.name
 assert seen==set(expected);counts.append(len(seen))
print(json.dumps({'payloadFilesVerified':len(m['payload']),'archiveMembersVerified':counts,'nativeExecuted':False,'checkerExecuted':False,'extractionPerformed':False}))
