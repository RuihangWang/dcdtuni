"""Global identity of the solved T equation (bounded Gauss upwind):
   sum_b phi_b T_b  (advective export through every non-coupled patch)
 = sum_i T_i * sum_f phi_f   (the bounded scheme's  -Sp(div phi) T  term)
 + diffusive terms.
phi = the flux TEqn used (written at the previous time), T = the solved field."""
import re, sys
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).parent))
case = Path(sys.argv[1]); t_old, t_new = sys.argv[2], sys.argv[3]
RHO_CP = 1.19 * 1005.0
def body(p): return p.read_text().split('FoamFile', 1)[-1].split('}', 1)[1]
def slist(text):
    m = re.search(r'(\d+)\s*\(\s*([^()]*)\)', text, re.S)
    if m is None:
        m = re.search(r'(\d+)\s*\{\s*([^{}]*)\}', text, re.S); return np.full(int(m.group(1)), float(m.group(2)))
    return np.array(m.group(2).split(), float)
def field(text):
    inner = text.split('internalField', 1)[1].split('boundaryField', 1)[0]
    bf = text.split('boundaryField', 1)[1]
    return inner, bf
def patch_block(bf, name):
    m = re.search(r'\n\s*' + re.escape(name) + r'\s*\n\s*\{', bf)
    if not m: return None
    d, i = 1, m.end()
    while d: d += {'{': 1, '}': -1}.get(bf[i], 0); i += 1
    return bf[m.end():i - 1]
def pvals(bf, name, n, default=None):
    blk = patch_block(bf, name)
    if blk is None: return default
    m = re.search(r'\bvalue\s+(nonuniform|uniform)\s+(.*?);', blk, re.S)
    if not m: return default
    return np.full(n, float(m.group(2))) if m.group(1) == 'uniform' else slist(m.group(2))
tot = dict(export=0., sp=0., sp_mean=0., mass=0., ncells=0)
per_patch = {}
for proc in sorted(case.glob('processor*')):
    mesh = proc / 'constant/polyMesh'
    owner = slist(body(mesh / 'owner')).astype(int); nb = slist(body(mesh / 'neighbour')).astype(int)
    nint = len(nb)
    Ttxt = (proc / t_new / 'T').read_text(); Pt = (proc / t_old / 'phi').read_text()
    Tin, Tbf = field(Ttxt); Pin, Pbf = field(Pt)
    T = slist(Tin); phi_int = slist(Pin)
    nc = len(T); div = np.zeros(nc)
    np.add.at(div, owner[:nint], phi_int); np.add.at(div, nb, -phi_int)
    bnd = (mesh / 'boundary').read_text()
    for name, b in re.findall(r'\n\s*(\w[\w.-]*)\s*\n\s*\{([^}]*)\}', bnd):
        if 'nFaces' not in b: continue
        kind = re.search(r'\btype\s+(\w+);', b).group(1)
        n = int(re.search(r'nFaces\s+(\d+);', b).group(1)); s = int(re.search(r'startFace\s+(\d+);', b).group(1))
        if n == 0: continue
        ph = pvals(Pbf, name, n, np.zeros(n))
        np.add.at(div, owner[s:s + n], ph)
        if kind in ('processor', 'processorCyclic', 'cyclic'):
            continue
        Tb = pvals(Tbf, name, n, T[owner[s:s + n]])
        e = float((ph * Tb).sum()); per_patch[name] = per_patch.get(name, 0.) + e
        tot['export'] += e; tot['mass'] += float(ph.sum())
    tot['sp'] += float((T * div).sum()); tot['ncells'] += nc
    tot.setdefault('div_abs', 0.); tot['div_abs'] += float(np.abs(div).sum())
    tot.setdefault('cells', []).append((T, div))
Tall = np.concatenate([c[0] for c in tot['cells']]); dall = np.concatenate([c[1] for c in tot['cells']])
print('cells', tot['ncells'], ' sum|div phi| (m3/s):', round(tot['div_abs'], 6), ' sum div phi:', dall.sum())
print('advective export through patches (W):', round(RHO_CP * tot['export'], 2), '  net mass (m3/s):', tot['mass'])
print('bounded Sp term  sum T_i*divphi_i (W):', round(RHO_CP * tot['sp'], 2))
print('   of which (T_i - Tmean)*divphi_i (W):', round(RHO_CP * float(((Tall - Tall.mean()) * dall).sum()), 2))
order = np.argsort(-np.abs(dall * (Tall - Tall.mean())))
cum = np.cumsum((dall * (Tall - Tall.mean()))[order]) * RHO_CP
for k in (10, 100, 1000, 10000):
    print(f'  top {k:6d} cells carry {cum[k-1]:9.2f} W')
