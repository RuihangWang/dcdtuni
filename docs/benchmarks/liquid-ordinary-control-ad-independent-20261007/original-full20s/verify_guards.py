from pathlib import Path
import json,hashlib
R=Path(__file__).resolve().parent;rows=json.loads((R/'normal-before-guards.json').read_text());errors=[]
for x in rows:
 p=Path(x['path']);q=R/x['copy']
 if not p.is_file() or not q.is_file() or p.stat().st_size!=x['bytes'] or hashlib.sha256(p.read_bytes()).hexdigest()!=x['sha256'] or hashlib.sha256(q.read_bytes()).hexdigest()!=x['sha256']:errors.append(x['path'])
print(json.dumps({'normal_before_records':len(rows),'normal_before_unique':len(set(x['path'] for x in rows)),'errors':errors}));assert not errors
