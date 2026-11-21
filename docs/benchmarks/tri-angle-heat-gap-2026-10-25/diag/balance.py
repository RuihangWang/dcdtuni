"""Per-iteration enthalpy balance of the Tri_angle continuation (scratch run)."""
import glob, re, sys
import numpy as np
RHO_CP = 1.19 * 1005.0
def series(fo):
    rows = {}
    for f in sorted(glob.glob(f'postProcessing/{fo}/*/*.dat')):
        for line in open(f):
            if line.startswith('#') or not line.strip(): continue
            t, v = line.split()[:2]; rows[int(float(t))] = float(v)
    return rows
names = sorted({re.sub(r'^bal_phiT?_', '', p.split('/')[1]) for p in glob.glob('postProcessing/bal_phi*')})
phi = {n: series(f'bal_phi_{n}') for n in names}
phT = {n: series(f'bal_phiT_{n}') for n in names}
its = sorted(set.intersection(*(set(s) for s in list(phi.values()) + list(phT.values()))))
out = []
for it in its:
    mass = sum(phi[n][it] for n in names)
    acu = sum(phT[n][it] for n in names if n.startswith('ACU'))          # >0 = enthalpy leaving through ACU patches
    srv = -sum(phT[n][it] for n in names if not n.startswith('ACU'))     # >0 = enthalpy added by servers
    out.append((it, mass, RHO_CP * acu, RHO_CP * srv, RHO_CP * (acu - srv)))
a = np.array(out)
print('iterations', int(a[0, 0]), '..', int(a[-1, 0]), 'n =', len(a))
print('max |sum phi| (m3/s):', np.abs(a[:, 1]).max())
for lab, col in (('ACU removes (W)', 2), ('servers add (W)', 3), ('net export (W)', 4)):
    c = a[:, col]; print(f'{lab:18s} mean {c.mean():10.2f}  min {c.min():10.2f}  max {c.max():10.2f}  last {c[-1]:10.2f}')
vt = series('bal_volT'); v = np.array([vt[i] for i in its])
print('volIntegrate(T) drift over the window (m3 K):', v[-1] - v[0], ' -> mean storage rate x rho cp per iteration (W-equiv):', RHO_CP * (v[-1] - v[0]) / max(1, len(v) - 1))
