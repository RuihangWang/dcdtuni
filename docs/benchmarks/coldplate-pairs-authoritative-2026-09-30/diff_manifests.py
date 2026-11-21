"""Diff two COLDPLATE_DUMP_DIR trees written by
src/frontend/react/test/coldPlateManifestDump.test.ts, field by field.

    python3 diff_manifests.py BEFORE_DIR AFTER_DIR [--json OUT.json]

Every file is compared whole (nodes, edges and their params, racks and their
cp_cu_pairs, object_bindings, events, time, source_project, ...); a compile
that threw is stored as {"error": ...} and compared like any other value.
"""
import hashlib, json, os, sys


def walk(a, b, path, out):
    if type(a) is not type(b):
        out.append([path, a, b]); return
    if isinstance(a, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                out.append([f'{path}.{k}', a.get(k, '<absent>'), b.get(k, '<absent>')])
            else:
                walk(a[k], b[k], f'{path}.{k}', out)
    elif isinstance(a, list):
        if len(a) != len(b):
            out.append([f'{path}[len]', len(a), len(b)])
        for i, (x, y) in enumerate(zip(a, b)):
            walk(x, y, f'{path}[{i}]', out)
    elif a != b:
        out.append([path, a, b])


def digest(doc):
    return hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def summary(doc):
    if isinstance(doc, dict) and 'error' in doc:
        return {'error': doc['error']}
    edges = doc.get('edges', [])
    return {'schemaVersion': doc.get('schemaVersion'), 'nodes': len(doc.get('nodes', [])), 'edges': len(edges),
            'cold_plate_edges': sum(e.get('type') == 'cold_plate' for e in edges),
            'source_sha256': (doc.get('source_project') or {}).get('sha256')}


def main(before, after, out=None):
    rows = {}
    for name in sorted(os.listdir(before)):
        if name == 'index.json' or not name.endswith('.json'):
            continue
        a = json.load(open(os.path.join(before, name)))
        b = json.load(open(os.path.join(after, name)))
        diffs = []
        walk(a, b, '', diffs)
        rows[name] = {'before': {'digest': digest(a), **summary(a)}, 'after': {'digest': digest(b), **summary(b)},
                      'identical': not diffs, 'diffs': diffs}
        print(f"{name:66s} {'IDENTICAL' if not diffs else f'{len(diffs)} DIFF'}")
        for p, x, y in diffs[:6]:
            print(f'      {p} | {str(x)[:70]} -> {str(y)[:70]}')
    if out:
        json.dump(rows, open(out, 'w'), indent=1)


if __name__ == '__main__':
    args = sys.argv[1:]
    out = args[args.index('--json') + 1] if '--json' in args else None
    main(args[0], args[1], out)
