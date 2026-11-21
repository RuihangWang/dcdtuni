"""Partition-independent signature of an OpenFOAM mesh snapshot.

cells: every cell's centre (rounded to 1e-9 m) and volume, sorted -- equal
cell sets give equal arrays whatever the rank count; patches: the face count
of every physical (non-processor) patch summed over ranks.
"""
import json, re, sys
from pathlib import Path
import numpy as np

def internal(path, vector=False):
    t = Path(path).read_text().split('internalField', 1)[1].split('boundaryField', 1)[0]
    if vector:
        body = re.search(r'(\d+)\s*\((.*)\)', t, re.S).group(2)
        return np.array(re.findall(r'\(([^()]*)\)', body)[:], dtype=object)
    return np.array(re.search(r'(\d+)\s*\(\s*([^()]*)\)', t, re.S).group(2).split(), float)

snap = Path(sys.argv[1])
roots = sorted(snap.glob('processor*')) or [snap]
cells, patches = [], {}
for r in roots:
    times = [d for d in r.iterdir() if re.fullmatch(r'[0-9.]+', d.name) and (d / 'C').exists()]
    d = max(times, key=lambda x: float(x.name)) if times else r / 'constant'
    C = np.array([s.split() for s in internal(d / 'C', vector=True)], float)
    V = internal(d / 'V')
    cells.append(np.column_stack([np.round(C, 9), V]))
    bnd = (r / 'constant/polyMesh/boundary').read_text()
    for name, body in re.findall(r'\n\s*(\w[\w.-]*)\s*\n\s*\{([^}]*)\}', bnd):
        kind = re.search(r'\btype\s+(\w+);', body)
        if kind is None or 'nFaces' not in body or kind.group(1) == 'processor':
            continue
        patches[name] = patches.get(name, 0) + int(re.search(r'nFaces\s+(\d+);', body).group(1))
allc = np.concatenate(cells)
order = np.lexsort(allc[:, :3].T[::-1])
allc = allc[order]
np.save(sys.argv[2], allc)
json.dump({'cells': int(len(allc)), 'volume': float(allc[:, 3].sum()), 'patches': patches}, open(sys.argv[2] + '.json', 'w'), indent=1, sort_keys=True)
print(len(allc), allc[:, 3].sum())
