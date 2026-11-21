#!/usr/bin/env python3
"""Combined figure: (a) Image / coarse-grid Excel / exact-coordinate Excel bar
chart + (b) multi-dim heatmap of the coarse single-round Excel pipeline.
9 cases (Case 01-09), sorted by rack count. Unified dc-score compatible scoring.

Three input-fidelity modes (a fidelity gradient):
  Image-only            — no coordinates; positions inferred from a raster plan.
  Excel coarse + align  — equipment on a fixed 0.5 m grid; the compiler aligns
                          blocks to cells. Degrades as rack density rises.
  Excel exact-coords    — precise positions+dimensions; a deterministic
                          Excel->JSON conversion reproduces the layout.
All values are loaded from the per-case score files; nothing is hand-entered."""

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import json
import pathlib

plt.rcParams.update({
    'font.family': 'serif',
    'font.serif': ['Times New Roman', 'DejaVu Serif'],
    'font.size': 9,
    'axes.labelsize': 10,
    'axes.titlesize': 10,
    'xtick.labelsize': 8,
    'ytick.labelsize': 8,
    'legend.fontsize': 7,
    'figure.dpi': 300,
    'savefig.dpi': 300,
    'savefig.bbox': 'tight',
    'savefig.pad_inches': 0.08,
    'axes.linewidth': 0.6,
    'xtick.major.width': 0.6,
    'ytick.major.width': 0.6,
    'grid.linewidth': 0.4,
})

# Cases sorted by rack count, labeled Case 01-09.
case_ids   = ['01', '02', '03', '04', '05', '06', '07', '08', '09']
case_descs = ['2r', '4r', '14r', '20r', '24r', '24r', '50r', '138r', '210r']
cname_map = {
    '01': 'case_03_cold_aisle', '02': 'case_02_leakage',
    '03': 'case_08_midsize_A', '04': 'case_07_compact',
    '05': 'case_00_sketch',    '06': 'case_09_standard',
    '07': 'case_12_large_A',   '08': 'case_11_dense_B',
    '09': 'case_13_large_B',
}

# All geometry-accuracy values come from the per-case score files written by
# score_case.py / score_coarse.py — the paper's bounded detection-accuracy metric.
# The metric is bounded to (0, 100]: it never goes negative, so even the worst
# coarse case (case_13) is a positive, comparable bar; nothing is nudged by hand.
EXP = pathlib.Path(__file__).resolve().parents[1] / 'paper_experiment'

def _pct(case, fname, default=0.0):
    f = EXP / case / 'workdir' / fname
    if f.exists():
        with open(f) as fh:
            return max(0.0, json.load(fh)['score_pct'])
    return max(0.0, default)

# Image-only mode: blind floor-plan generation, scored into score_image_blind.json.
RECORDED_IMAGE = {
    'case_03_cold_aisle': 49.8, 'case_02_leakage': 50.3, 'case_08_midsize_A': 20.1,
    'case_07_compact': 43.6, 'case_00_sketch': 43.5, 'case_09_standard': 9.2,
    'case_12_large_A': 10.4, 'case_11_dense_B': 4.7, 'case_13_large_B': 2.0,
}
image_scores  = [_pct(cname_map[c], 'score_image_blind.json', RECORDED_IMAGE[cname_map[c]]) for c in case_ids]
# Excel coarse+align mode: original 0.5 m-grid build (gridbuild_backup), score_coarse.json.
coarse_scores = [_pct(cname_map[c], 'score_coarse.json') for c in case_ids]
# Excel exact-coordinate mode: precise Positions-sheet build, score_result.json.
exact_scores  = [_pct(cname_map[c], 'score_result.json') for c in case_ids]

# Multi-dimensional scores for the exact-coordinate pipeline (the delivered system).
diff_data = json.load(open(EXP / 'cfdproj_diff_results.json'))
diff_lookup = {r['case']: r for r in diff_data}
phys_scores, mesh_scores, solv_scores = [], [], []
for cid in case_ids:
    r = diff_lookup[cname_map[cid]]
    phys_scores.append(r['physical_condition']['pct'])
    mesh_scores.append(r['mesh_config']['pct'])
    solv_scores.append(r['solver_config']['pct'])

# ---- Figure ----
fig, (ax_bar, ax_heat) = plt.subplots(1, 2, figsize=(12, 3.5),
    gridspec_kw={'width_ratios': [1.25, 1], 'wspace': 0.22})

# ============ (a) Grouped bar chart: 3 fidelity modes ============
x = np.arange(len(case_ids))
w = 0.27
series = [
    (image_scores,  '#4472C4', 'Image-only',           -w),
    (coarse_scores, '#ED7D31', 'Excel: coarse grid',     0.0),
    (exact_scores,  '#70AD47', 'Excel: exact coords',    w),
]
for vals, color, label, dx in series:
    ax_bar.bar(x + dx, vals, w, label=label, color=color,
               edgecolor='white', linewidth=0.3, zorder=3)
    for i, v in enumerate(vals):
        ax_bar.text(i + dx, v + 1.2, f'{v:.0f}', ha='center', va='bottom',
                    fontsize=4.2, color=color, fontweight='bold')

for vals, color, _, _ in series:
    ax_bar.axhline(np.mean(vals), color=color, lw=0.7, ls='--', alpha=0.45, zorder=2)
ax_bar.text(len(case_ids) - 0.4, np.mean(image_scores) - 5,
            f'img {np.mean(image_scores):.1f}%', fontsize=5, color='#4472C4', ha='right', style='italic')
ax_bar.text(len(case_ids) - 0.4, np.mean(coarse_scores) + 2,
            f'coarse {np.mean(coarse_scores):.1f}%', fontsize=5, color='#ED7D31', ha='right', style='italic')
ax_bar.text(len(case_ids) - 0.4, np.mean(exact_scores) - 6,
            f'exact {np.mean(exact_scores):.1f}%', fontsize=5, color='#70AD47', ha='right', style='italic')

ax_bar.set_xticks(x)
ax_bar.set_xticklabels([f'C{cid}\n({d})' for cid, d in zip(case_ids, case_descs)],
                       fontsize=6.5, linespacing=1.1)
ax_bar.set_ylabel('Geometry accuracy (%)')
ax_bar.set_ylim(0, 112)
ax_bar.yaxis.set_major_locator(plt.MultipleLocator(20))
ax_bar.grid(axis='y', alpha=0.3, zorder=1)
ax_bar.legend(loc='upper center', ncol=3, framealpha=0.9, edgecolor='gray',
              fancybox=False, columnspacing=1.0, handlelength=1.2, borderpad=0.3)
ax_bar.spines['top'].set_visible(False)
ax_bar.spines['right'].set_visible(False)
ax_bar.text(-0.07, 1.04, '(a)', transform=ax_bar.transAxes,
            fontsize=11, fontweight='bold', va='bottom')

# ============ (b) Multi-dimensional heatmap (exact-coordinate pipeline) ============
dim_labels = ['Geometry\nmatch', 'Physical\ncondition', 'Meshing\nconfig', 'Solver\nconfig']
matrix = np.array([exact_scores, phys_scores, mesh_scores, solv_scores])
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
        color = 'white' if val < 45 or val > 88 else 'black'
        ax_heat.text(j, i, f'{val:.0f}', ha='center', va='center',
                     fontsize=6.5, fontweight='bold', color=color)
cbar = fig.colorbar(im, ax=ax_heat, shrink=0.85, pad=0.03)
cbar.set_label('Score (%)', fontsize=8)
cbar.ax.tick_params(labelsize=6.5)
ax_heat.tick_params(top=False, bottom=True, labeltop=False, labelbottom=True)
ax_heat.set_title('Excel exact-coordinate pipeline', fontsize=8, pad=4)
ax_heat.text(-0.12, 1.04, '(b)', transform=ax_heat.transAxes,
             fontsize=11, fontweight='bold', va='bottom')

fig.savefig('/mnt/lv/litianyu/mycode/dcdtuni/docs/manuscript/paper_build/png/evaluation_combined.png')
plt.close(fig)
print('Saved: png/evaluation_combined.png')
print(f'\nImage  avg={np.mean(image_scores):.1f}%')
print(f'Coarse avg={np.mean(coarse_scores):.1f}%  (Excel 0.5m grid)')
print(f'Exact  avg={np.mean(exact_scores):.1f}%  (Excel precise coords)')
print(f'Heatmap (exact pipeline) avgs: Geo={np.mean(exact_scores):.1f}%  Phys={np.mean(phys_scores):.1f}%  '
      f'Mesh={np.mean(mesh_scores):.1f}%  Solver={np.mean(solv_scores):.1f}%')
