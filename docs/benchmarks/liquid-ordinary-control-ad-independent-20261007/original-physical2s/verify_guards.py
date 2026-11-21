from pathlib import Path
import json,hashlib
R=Path(__file__).resolve().parent
items=json.loads((R/'normal-before-guards.json').read_text())+json.loads((R/'imported-source-before.json').read_text())+json.loads((R/'dynamic-initializer-dependency.json').read_text())
for item in items:
 data=Path(item['path']).read_bytes()
 if len(data)!=item['bytes'] or hashlib.sha256(data).hexdigest()!=item['sha256']:raise RuntimeError('immutable normal prerequisite changed: '+item['path'])
print('Normal prerequisite guards match:',len(items),'records',len(set(x['path']for x in items)),'unique paths')
