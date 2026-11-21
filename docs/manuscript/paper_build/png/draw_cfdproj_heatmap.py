#!/usr/bin/env python3
"""Generate heatmap figure for multi-dimensional cfdproj evaluation."""

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import json

plt.rcParams.update({
    'font.family': 'serif',
    'font.serif': ['Times New Roman', 'DejaVu Serif'],
    'font.size': 9,
    'axes.labelsize': 10,
    'xtick.labelsize': 8.5,
    'ytick.labelsize': 8,
    'figure.dpi': 300,
    'savefig.dpi': 300,
    'savefig.bbox': 'tight',
    'savefig.pad_inches': 0.05,
})

data = json.load(open('/mnt/lv/litianyu/mycode/dcdtuni/paper_experiment/cfdproj_diff_results.json'))

case_short = {
    'case_00_sketch': '00', 'case_01_testbed': '01', 'case_02_leakage': '02',
    'case_03_cold_aisle': '03', 'case_04_polygon': '04', 'case_05_triangle': '05',
    'case_06_raised_floor': '06', 'case_07_compact': '07', 'case_08_midsize_A': '08',
    'case_09_standard': '09', 'case_11_dense_B': '11', 'case_12_large_A': '12',
    'case_13_large_B': '13',
}
rack_counts = {
    '00': 24, '01': 2, '02': 4, '03': 2, '04': 2, '05': 2,
    '06': 8, '07': 20, '08': 14, '09': 24, '11': 138, '12': 50, '13': 210,
}

skip = {'case_00_sketch', 'case_05_triangle'}

cases = []
dims = []

for r in data:
    if r['case'] in skip:
        continue
    mc = r['model_count']
    geo_pct = round(100 * mc['matched'] / mc['label'], 1) if mc['label'] > 0 else 0
    short = case_short.get(r['case'], r['case'])
    racks = rack_counts.get(short, '?')
    cases.append(f'C{short}\n({racks}r)')
    dims.append([
        geo_pct,
        r['physical_condition']['pct'],
        r['mesh_config']['pct'],
        r['solver_config']['pct'],
    ])

dim_labels = ['Geometry\nmatch', 'Physical\ncondition', 'Meshing\nconfig', 'Solver\nconfig']
matrix = np.array(dims)

fig, ax = plt.subplots(figsize=(7.0, 3.0))

cmap = plt.cm.RdYlGn
norm = plt.Normalize(vmin=0, vmax=100)
im = ax.imshow(matrix.T, cmap=cmap, norm=norm, aspect='auto')

ax.set_xticks(range(len(cases)))
ax.set_xticklabels(cases, fontsize=6.5, linespacing=1.05)
ax.set_yticks(range(len(dim_labels)))
ax.set_yticklabels(dim_labels, fontsize=8.5)

for i in range(len(dim_labels)):
    for j in range(len(cases)):
        val = matrix[j, i]
        color = 'white' if val < 40 or val > 85 else 'black'
        ax.text(j, i, f'{val:.1f}', ha='center', va='center',
                fontsize=6, fontweight='bold', color=color)

cbar = fig.colorbar(im, ax=ax, shrink=0.85, pad=0.02)
cbar.set_label('Score (%)', fontsize=9)
cbar.ax.tick_params(labelsize=7)

ax.set_xlabel('Test case')
ax.tick_params(top=False, bottom=True, labeltop=False, labelbottom=True)

fig.savefig('/mnt/lv/litianyu/mycode/dcdtuni/docs/manuscript/paper_build/png/cfdproj_quality_heatmap.png')
plt.close(fig)
print('Saved: png/cfdproj_quality_heatmap.png')

avgs = matrix.mean(axis=0)
print(f'\nDimension averages (across {len(cases)} cases):')
for label, avg in zip(['Geometry match', 'Physical condition', 'Meshing config', 'Solver config'], avgs):
    print(f'  {label:20s}: {avg:.1f}%')
print(f'  {"Overall":20s}: {avgs.mean():.1f}%')
