from pathlib import Path
import hashlib,json
F=Path(__file__).resolve().parent
D=json.loads((F/'dependencies.json').read_text());bad=[]
for r in D['files']:
 p=Path(r['source'])
 if not p.is_file() or p.stat().st_size!=r['bytes'] or hashlib.sha256(p.read_bytes()).hexdigest()!=r['sha256']:bad.append(r['source'])
for r in D['buildResolver']:
 if str(Path(r['buildLink']).resolve())!=r['resolved']:bad.append(r['buildLink'])
print(json.dumps({'sourceFileCount':len(D['files']),'bad':bad,'scope':'file/resolver identity only, no loaded process/API/layout/encoded-frame proof'}))
raise SystemExit(bool(bad))
