#!/usr/bin/env python3
"""Combined figure: (a) Image vs Excel bar chart + (b) multi-dim heatmap.
Same 6 cases, unified dc-score compatible scoring."""

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
    'axes.titlesize': 10,
    'xtick.labelsize': 8,
    'ytick.labelsize': 8,
    'legend.fontsize': 7.5,
    'figure.dpi': 300,
    'savefig.dpi': 300,
    'savefig.bbox': 'tight',
    'savefig.pad_inches': 0.08,
    'axes.linewidth': 0.6,
    'xtick.major.width': 0.6,
    'ytick.major.width': 0.6,
    'grid.linewidth': 0.4,
})

# ── Data: 9 cases sorted by rack count (7 with both modes + 2 image-only) ──
case_ids   = ['03', '02', '08', '07', '00', '09', '12', '11', '13']
case_descs = ['2r', '4r', '14r', '20r', '24r', '24r', '50r', '138r', '210r']
models     = [14, 16, 29, 37, 38, 44, 75, 169, 324]

# dc-score geometry accuracy (%) — Image-only vs Excel
# All 9 cases have both modes
image_scores = [57.0, 58.2, -3.4, 18.5, 52.3, 29.3, 17.8, -10.5, -10.8]
excel_scores = [95.5, 92.0, 85.3, 94.2, 98.8, 83.6, 75.2,  62.4,  51.8]

# For heatmap geometry row: use Excel where available, else Image
geo_for_heatmap = [e if e is not None else i for i, e in zip(image_scores, excel_scores)]

# Multi-dimensional scores from diff_cfdproj.py
diff_data = json.load(open('/mnt/lv/litianyu/mycode/dcdtuni/paper_experiment/cfdproj_diff_results.json'))
diff_lookup = {r['case']: r for r in diff_data}

cname_map = {
    '00': 'case_00_sketch', '02': 'case_02_leakage',
    '03': 'case_03_cold_aisle', '07': 'case_07_compact',
    '08': 'case_08_midsize_A', '09': 'case_09_standard',
    '12': 'case_12_large_A', '11': 'case_11_dense_B',
    '13': 'case_13_large_B',
}

phys_scores, mesh_scores, solv_scores = [], [], []
for cid in case_ids:
    r = diff_lookup[cname_map[cid]]
    phys_scores.append(r['physical_condition']['pct'])
    mesh_scores.append(r['mesh_config']['pct'])
    solv_scores.append(r['solver_config']['pct'])

# ── Figure: side by side ──
fig, (ax_bar, ax_heat) = plt.subplots(1, 2, figsize=(12, 3.5),
    gridspec_kw={'width_ratios': [1.15, 1], 'wspace': 0.25})

# ============================================================
# (a) Grouped bar chart — Image vs Excel
# ============================================================
x = np.arange(len(case_ids))
w = 0.32

# Image-only bars: all 9 cases
bars_img = ax_bar.bar(x - w/2, image_scores, w, label='Image-only',
                      color='#4472C4', edgecolor='white', linewidth=0.3, zorder=3)

# Excel bars: all 9 cases
bars_xl = ax_bar.bar(x + w/2, excel_scores, w, label='Excel spreadsheet',
                     color='#ED7D31', edgecolor='white', linewidth=0.3, zorder=3)

for bar, val in zip(bars_img, image_scores):
    va = 'bottom' if val >= 0 else 'top'
    off = 1.5 if val >= 0 else -1.5
    ax_bar.text(bar.get_x() + bar.get_width()/2, val + off, f'{val:.1f}',
                ha='center', va=va, fontsize=5.5, color='#4472C4', fontweight='bold')

for bar, val in zip(bars_xl, excel_scores):
    va = 'bottom' if val >= 0 else 'top'
    off = 1.5 if val >= 0 else -1.5
    ax_bar.text(bar.get_x() + bar.get_width()/2, val + off, f'{val:.1f}',
                ha='center', va=va, fontsize=5.5, color='#ED7D31', fontweight='bold')

ax_bar.axhline(y=0, color='black', linewidth=0.5, zorder=2)

avg_img = np.mean(image_scores)
excel_valid = [v for v in excel_scores if v is not None]
avg_xl = np.mean(excel_valid)
ax_bar.axhline(y=avg_img, color='#4472C4', linewidth=0.7, linestyle='--', alpha=0.5, zorder=2)
ax_bar.axhline(y=avg_xl, color='#ED7D31', linewidth=0.7, linestyle='--', alpha=0.5, zorder=2)
ax_bar.text(len(case_ids)-0.5, avg_img - 5, f'img avg={avg_img:.1f}%', fontsize=6,
            color='#4472C4', ha='right', style='italic')
ax_bar.text(len(case_ids)-0.5, avg_xl + 2.5, f'xlsx avg={avg_xl:.1f}%', fontsize=6,
            color='#ED7D31', ha='right', style='italic')

ax_bar.set_xticks(x)
ax_bar.set_xticklabels([f'C{cid}\n({d})' for cid, d in zip(case_ids, case_descs)],
                       fontsize=6.5, linespacing=1.1)
ax_bar.set_ylabel('Geometry accuracy (%)')
ax_bar.set_ylim(-15, 110)
ax_bar.yaxis.set_major_locator(plt.MultipleLocator(20))
ax_bar.grid(axis='y', alpha=0.3, zorder=1)
ax_bar.legend(loc='upper right', framealpha=0.9, edgecolor='gray', fancybox=False)
ax_bar.spines['top'].set_visible(False)
ax_bar.spines['right'].set_visible(False)
ax_bar.text(-0.08, 1.05, '(a)', transform=ax_bar.transAxes,
            fontsize=11, fontweight='bold', va='bottom')

# ============================================================
# (b) Multi-dimensional heatmap (4 dims × 6 cases)
# ============================================================
dim_labels = ['Geometry\nmatch', 'Physical\ncondition', 'Meshing\nconfig', 'Solver\nconfig']
matrix = np.array([
    geo_for_heatmap,   # Excel dc-score where available, else Image
    phys_scores,
    mesh_scores,
    solv_scores,
])

cmap = plt.cm.RdYlGn
norm = plt.Normalize(vmin=0, vmax=100)
im = ax_heat.imshow(matrix, cmap=cmap, norm=norm, aspect='auto')

ax_heat.set_xticks(range(len(case_ids)))
ax_heat.set_xticklabels([f'C{cid}\n({d})' for cid, d in zip(case_ids, case_descs)],
                        fontsize=6.5, linespacing=1.1)
ax_heat.set_yticks(range(len(dim_labels)))
ax_heat.set_yticklabels(dim_labels, fontsize=8)

for i in range(len(dim_labels)):
    for j in range(len(case_ids)):
        val = matrix[i, j]
        color = 'white' if val < 35 or val > 85 else 'black'
        ax_heat.text(j, i, f'{val:.1f}', ha='center', va='center',
                     fontsize=6.5, fontweight='bold', color=color)

cbar = fig.colorbar(im, ax=ax_heat, shrink=0.85, pad=0.03)
cbar.set_label('Score (%)', fontsize=8)
cbar.ax.tick_params(labelsize=6.5)

ax_heat.tick_params(top=False, bottom=True, labeltop=False, labelbottom=True)
ax_heat.text(-0.12, 1.05, '(b)', transform=ax_heat.transAxes,
             fontsize=11, fontweight='bold', va='bottom')

fig.savefig('/mnt/lv/litianyu/mycode/dcdtuni/docs/manuscript/paper_build/png/evaluation_combined.png')
plt.close(fig)
print('Saved: png/evaluation_combined.png')

print(f'\nBar chart: Image avg={avg_img:.1f}%  Excel avg={avg_xl:.1f}% (n={len(excel_valid)})  Δ={avg_xl-avg_img:+.1f}pp')
print(f'Heatmap averages (all {len(case_ids)} cases):')
print(f'  Geometry={np.mean(geo_for_heatmap):.1f}%  '
      f'PhysCond={np.mean(phys_scores):.1f}%  '
      f'Mesh={np.mean(mesh_scores):.1f}%  '
      f'Solver={np.mean(solv_scores):.1f}%')
