"""Recheck retained actual run products. No solver, API or browser operation."""
import hashlib, json, struct, sys
from pathlib import Path

def differences(a, b, pointer=''):
    if type(a) is not type(b):
        return [pointer]
    if isinstance(a, dict):
        if list(a) != list(b):
            return [pointer + '/@ordered-keys']
        result = []
        for key in a:
            escaped = key.replace('~', '~0').replace('/', '~1')
            result.extend(differences(a[key], b[key], pointer + '/' + escaped))
        return result
    if isinstance(a, list):
        if len(a) != len(b):
            return [pointer + '/@length']
        return [p for i, (x, y) in enumerate(zip(a, b))
                for p in differences(x, y, pointer + '/' + str(i))]
    if isinstance(a, float):
        return [] if struct.pack('>d', a) == struct.pack('>d', b) else [pointer]
    return [] if a == b else [pointer]

if __name__ == '__main__':
    prior, current = (Path(arg).resolve() for arg in sys.argv[1:3])
    permitted = {'input.json': ['/run_id', '/project_revision'],
                 'input.liquidrun': [], 'executed.liquidrun': [],
                 'result.json': ['/run_id'], 'checkpoint.json': ['/run_id'],
                 'executed-inputs.json': ['/run_id']}
    reports = {}
    for name, allowed in permitted.items():
        left, right = (prior / name).read_bytes(), (current / name).read_bytes()
        found = differences(json.loads(left), json.loads(right))
        assert found == allowed, (name, found)
        if name.endswith('.liquidrun'):
            assert left == right
        reports[name] = {'differencePaths': found, 'bytesEqual': left == right,
                         'priorSHA256': hashlib.sha256(left).hexdigest(),
                         'currentSHA256': hashlib.sha256(right).hexdigest()}
    print(json.dumps({'scope': 'Actual full liquid run compatibility, no numerical tolerances',
                      'signedZeroCompared': True, 'orderedKeysCompared': True,
                      'products': reports}, indent=2))
