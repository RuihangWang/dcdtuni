#!/usr/bin/env python3
"""Read-only verification of this relocatable documentation evidence manifest."""
from pathlib import Path
import hashlib, json
root=Path(__file__).resolve().parents[1]
manifest=json.loads((root/'manifest.json').read_text())
errors=[]
for row in manifest['payloads']:
    p=root/row['path']
    if not p.is_file() or p.is_symlink():
        errors.append([row['path'],'missing or symlink']); continue
    if p.stat().st_size!=row['bytes'] or hashlib.sha256(p.read_bytes()).hexdigest()!=row['sha256']:
        errors.append([row['path'],'size/SHA mismatch'])
expected={r['path'] for r in manifest['payloads']}|{'manifest.json','verification.json'}
observed={str(p.relative_to(root)) for p in root.rglob('*') if p.is_file()}
if observed!=expected: errors.append(['inventory', sorted(observed^expected)])
verification=json.loads((root/'verification.json').read_text())
if hashlib.sha256((root/'manifest.json').read_bytes()).hexdigest()!=verification['manifest_sha256']:
    errors.append(['manifest.json','SHA differs from verification.json'])
print(json.dumps(dict(payload_count=len(manifest['payloads']),payload_bytes=sum(r['bytes'] for r in manifest['payloads']),manifest_SHA=verification['manifest_sha256'],all_SHA_and_inventory_match=not errors,errors=errors),indent=2))
raise SystemExit(bool(errors))
