import pandas as pd
import matplotlib.pyplot as plt
import matplotlib
import numpy as np
from matplotlib.lines import Line2D

matplotlib.rcParams.update({
    'font.family': 'serif',
    'font.serif': ['DejaVu Serif'],
    'font.size': 11,
    'axes.labelsize': 12,
    'legend.fontsize': 11,
    'figure.dpi': 150,
})

colors = {
    'Temperature': '#d62728',
    'Ux':          '#1f77b4',
    'Uy':          '#2ca02c',
    'Uz':          '#9467bd',
    'Pressure':    '#ff7f0e',
    'k':           '#8c564b',
    'epsilon':     '#e377c2',
}

dctwin = pd.read_csv("DCTwin/residuals.csv")
cadence = pd.read_csv("Caence/residuals_for_plot.csv")

var_dctwin = [
    ('T',      'Temperature', colors['Temperature']),
    ('Ux',     'Ux',          colors['Ux']),
    ('Uy',     'Uy',          colors['Uy']),
    ('Uz',     'Uz',          colors['Uz']),
    ('p_rgh',  'Pressure',    colors['Pressure']),
    ('k',      'k',           colors['k']),
    ('epsilon','epsilon',      colors['epsilon']),
]

var_cadence = [
    ('Temperature', 'Temperature', colors['Temperature']),
    ('X Velocity',  'Ux',          colors['Ux']),
    ('Y Velocity',  'Uy',          colors['Uy']),
    ('Z Velocity',  'Uz',          colors['Uz']),
    ('Pressure',    'Pressure',    colors['Pressure']),
    ('KE',          'k',           colors['k']),
    ('EP',          'epsilon',      colors['epsilon']),
]

def normalize(vals):
    v = vals.astype(float)
    base = v[0] if v[0] != 0 else v[v != 0][0] if (v != 0).any() else 1.0
    return v / base

fig, ax = plt.subplots(figsize=(7, 5.5))

for col, label, color in var_dctwin:
    ax.semilogy(dctwin["iteration"], normalize(dctwin[col].values),
                '-', color=color, linewidth=2.0)

for col, label, color in var_cadence:
    normed = normalize(cadence[col].values)
    mask = normed > 0
    ax.semilogy(cadence["Iteration"].values[mask], normed[mask],
                '--', color=color, linewidth=2.0)

handles = []
for _, label, color in var_dctwin:
    handles.append(Line2D([0], [0], color=color, linewidth=3.0, label=label))
handles.append(Line2D([0], [0], color='gray', linestyle='-', linewidth=3.0, label='DCTwin'))
handles.append(Line2D([0], [0], color='gray', linestyle='--', linewidth=3.0, label='Commercial Soft.'))

ax.legend(handles=handles, loc='upper right', ncol=1, framealpha=0.9)
ax.set_xlabel("Iteration")
ax.set_ylabel("Normalized Residual")
ax.set_xlim(0, max(dctwin["iteration"].max(), cadence["Iteration"].max()))
ax.grid(True, alpha=0.3, which='both')

fig.tight_layout()
fig.savefig("case02_residual.png", dpi=500, bbox_inches='tight')
plt.close()
print("Saved case02_residual.png")
