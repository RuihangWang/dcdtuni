from pathlib import Path
import hashlib,json,sys
F=Path(sys.argv[1]).resolve();m=json.loads((F/'manifest.json').read_text());bad=[]
for row in m['payload']:
 p=F/row['path']
 if not p.is_file() or p.stat().st_size!=row['bytes'] or hashlib.sha256(p.read_bytes()).hexdigest()!=row['sha256']:bad.append(row['path'])
print(json.dumps({'payloadCount':len(m['payload']),'payloadBytes':sum(x['bytes'] for x in m['payload']),'drift':bad}));sys.exit(bool(bad))
