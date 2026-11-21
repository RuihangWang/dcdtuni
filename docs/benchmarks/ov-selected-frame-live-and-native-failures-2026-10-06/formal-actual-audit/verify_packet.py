from pathlib import Path
import hashlib,json,sys
A=Path(__file__).resolve().parent
M=json.loads((A/'manifest.json').read_text())
errors=[]
for r in M['payload']:
 p=A/r['path']
 if not p.is_file():errors.append({'path':str(p),'error':'missing'});continue
 sha=hashlib.sha256(p.read_bytes()).hexdigest()
 if sha!=r['sha256'] or p.stat().st_size!=r['bytes']:errors.append({'path':str(p),'error':'payload mismatch','actualSHA':sha})
for r in M['references']:
 p=Path(r['path'])
 if not p.is_file():errors.append({'path':str(p),'error':'missing reference'});continue
 sha=hashlib.sha256(p.read_bytes()).hexdigest()
 if sha!=r['sha256'] or p.stat().st_size!=r['bytes']:errors.append({'path':str(p),'error':'reference mismatch','actualSHA':sha})
result={'allPass':not errors,'payloadCount':len(M['payload']),'payloadBytes':sum(x['bytes'] for x in M['payload']),'referenceCount':len(M['references']),'manifestSHA256':hashlib.sha256((A/'manifest.json').read_bytes()).hexdigest(),'errors':errors,'scope':'Read-only hash verification of frozen packet and original references.'}
print(json.dumps(result,indent=2));sys.exit(0 if not errors else 1)
