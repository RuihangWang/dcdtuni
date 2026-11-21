#!/usr/bin/env python3
"""Read-only complete archive verification; no source/tests/services/Git mutations."""
from pathlib import Path
import hashlib,json
D=Path(__file__).resolve().parent
M=D/'MANIFEST.json'
manifest=json.loads(M.read_text())
actual={str(p.relative_to(D)) for p in D.rglob('*') if p.is_file() and p!=M}
expected={e['path'] for e in manifest['files']}
assert len(expected)==len(manifest['files']) and actual==expected,'outer payload set mismatch'
for e in manifest['files']:
 rel=Path(e['path']);assert not rel.is_absolute() and '..' not in rel.parts
 p=D/rel;assert p.is_file() and not p.is_symlink()
 raw=p.read_bytes();assert len(raw)==e['bytes'] and hashlib.sha256(raw).hexdigest()==e['sha256'],e['path']
assert len(actual)==manifest['payloadCount']
assert sum(e['bytes'] for e in manifest['files'])==manifest['payloadBytes']
receipt=json.loads((D/'COPY_RECEIPT.json').read_text())
for name,proof in receipt['authorPackages'].items():
 f=D/'author'/name;m=f/'MANIFEST.json';raw=m.read_bytes()
 assert hashlib.sha256(raw).hexdigest()==proof['manifestSha256'] and len(raw)==proof['manifestBytes'],name
 a=json.loads(raw);entries=a.get('payloads',a.get('files'));files={str(p.relative_to(f)) for p in f.rglob('*') if p.is_file() and p!=m}
 assert files=={e['path'] for e in entries} and len(entries)==proof['payloadCount']
 assert sum(e['bytes'] for e in entries)==proof['payloadBytes']
 for e in entries:
  raw=(f/e['path']).read_bytes();assert len(raw)==e['bytes'] and hashlib.sha256(raw).hexdigest()==e['sha256'],(name,e['path'])
for e in receipt['sourceCopies']:
 raw=(D/e['path']).read_bytes();assert len(raw)==e['bytes'] and hashlib.sha256(raw).hexdigest()==e['sha256'],e['path']
assert len(receipt['sourceCopies'])==receipt['copiedFileCount']
assert sum(e['bytes'] for e in receipt['sourceCopies'])==receipt['copiedBytes']
whitelist=json.loads((D/'ADDITION_WHITELIST.json').read_text())
all_names={str(p.relative_to(D.parent.parent.parent)) for p in D.rglob('*') if p.is_file()}
assert all_names==set(whitelist['newArchivePaths']) and len(all_names)==len(whitelist['newArchivePaths'])
print(json.dumps({'FULLpayloadsVerified':len(actual),'payloadBytes':manifest['payloadBytes'],'actualFileCount':len(actual)+1,'outerManifestSha256':hashlib.sha256(M.read_bytes()).hexdigest(),'authorPackagesVerified':len(receipt['authorPackages']),'sourceCopiesVerified':receipt['copiedFileCount'],'whitelistExact':True},indent=2))
