#!/usr/bin/env python3
"""Generate academic figures for geometry accuracy evaluation section."""

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

plt.rcParams.update({
    'font.family': 'serif',
    'font.serif': ['Times New Roman', 'DejaVu Serif'],
    'font.size': 9,
    'axes.labelsize': 10,
    'axes.titlesize': 10,
    'xtick.labelsize': 8,
    'ytick.labelsize': 8,
    'legend.fontsize': 8,
    'figure.dpi': 300,
    'savefig.dpi': 300,
    'savefig.bbox': 'tight',
    'savefig.pad_inches': 0.05,
    'axes.linewidth': 0.6,
    'xtick.major.width': 0.6,
    'ytick.major.width': 0.6,
    'grid.linewidth': 0.4,
    'lines.linewidth': 1.0,
})

# --- Data ---
cases = ['Case 02', 'Case 03', 'Case 07', 'Case 08', 'Case 09', 'Case 12']
models = [16, 14, 37, 29, 44, 75]
image_scores = [58.2, 57.0, 18.5, -3.4, 29.3, 17.8]
excel_scores = [42.1, 95.5, 94.2, 74.1, -5.3, 29.4]
descriptions = ['4 racks\nleakage', '2 racks\ncold aisle', '20 racks\ncompact', '14 racks\nmid-size', '24 racks\nstandard', '50 racks\nlarge']

# Image-only additional cases (no excel)
image_only_cases = ['Case 11', 'Case 13']
image_only_models = [169, 324]
image_only_scores = [-10.5, -10.8]

# ============================================================
# Figure 1: Grouped bar chart — Image vs Excel
# ============================================================
fig, ax = plt.subplots(figsize=(5.5, 3.2))

x = np.arange(len(cases))
w = 0.32

bars_img = ax.bar(x - w/2, image_scores, w, label='Image-only',
                  color='#4472C4', edgecolor='white', linewidth=0.3, zorder=3)
bars_xl  = ax.bar(x + w/2, excel_scores, w, label='Excel spreadsheet',
                  color='#ED7D31', edgecolor='white', linewidth=0.3, zorder=3)

for bar, val in zip(bars_img, image_scores):
    va = 'bottom' if val >= 0 else 'top'
    offset = 1.5 if val >= 0 else -1.5
    ax.text(bar.get_x() + bar.get_width()/2, val + offset, f'{val:.1f}',
            ha='center', va=va, fontsize=6.5, color='#4472C4', fontweight='bold')

for bar, val in zip(bars_xl, excel_scores):
    va = 'bottom' if val >= 0 else 'top'
    offset = 1.5 if val >= 0 else -1.5
    ax.text(bar.get_x() + bar.get_width()/2, val + offset, f'{val:.1f}',
            ha='center', va=va, fontsize=6.5, color='#ED7D31', fontweight='bold')

ax.axhline(y=0, color='black', linewidth=0.5, zorder=2)

avg_img = np.mean(image_scores)
avg_xl = np.mean(excel_scores)
ax.axhline(y=avg_img, color='#4472C4', linewidth=0.8, linestyle='--', alpha=0.5, zorder=2)
ax.axhline(y=avg_xl, color='#ED7D31', linewidth=0.8, linestyle='--', alpha=0.5, zorder=2)
ax.text(len(cases)-0.5, avg_img + 2, f'avg = {avg_img:.1f}%', fontsize=7,
        color='#4472C4', ha='right', style='italic')
ax.text(len(cases)-0.5, avg_xl + 2, f'avg = {avg_xl:.1f}%', fontsize=7,
        color='#ED7D31', ha='right', style='italic')

ax.set_xticks(x)
ax.set_xticklabels([f'{c}\n({d})' for c, d in zip(cases, descriptions)],
                   fontsize=7, linespacing=1.1)
ax.set_ylabel('Geometry accuracy score (%)')
ax.set_ylim(-20, 110)
ax.yaxis.set_major_locator(plt.MultipleLocator(20))
ax.grid(axis='y', alpha=0.3, zorder=1)
ax.legend(loc='upper left', framealpha=0.9, edgecolor='gray', fancybox=False)
ax.spines['top'].set_visible(False)
ax.spines['right'].set_visible(False)

fig.savefig('png/geometry_accuracy_comparison.png')
plt.close(fig)
print('Saved: png/geometry_accuracy_comparison.png')

# ============================================================
# Figure 2: Score vs complexity (model count) scatter plot
# ============================================================
fig, ax = plt.subplots(figsize=(5.5, 3.2))

all_image_models = models + image_only_models
all_image_scores = image_scores + image_only_scores

ax.scatter(all_image_models, all_image_scores, s=45, c='#4472C4',
           marker='o', label='Image-only', zorder=4, edgecolors='white', linewidth=0.5)
ax.scatter(models, excel_scores, s=45, c='#ED7D31',
           marker='s', label='Excel spreadsheet', zorder=4, edgecolors='white', linewidth=0.5)

for i in range(len(models)):
    ax.annotate('', xy=(models[i], excel_scores[i]),
                xytext=(models[i], image_scores[i]),
                arrowprops=dict(arrowstyle='->', color='gray', lw=0.8,
                                connectionstyle='arc3,rad=0', shrinkA=4, shrinkB=4),
                zorder=2)

z_img = np.polyfit(all_image_models, all_image_scores, 1)
p_img = np.poly1d(z_img)
x_fit = np.linspace(10, 340, 100)
ax.plot(x_fit, p_img(x_fit), color='#4472C4', linewidth=0.8, linestyle='--', alpha=0.35, zorder=2)

img_label_offsets = {
    0: (5, 5),   # case_02 (16 models, 58.2)
    1: (5, -10), # case_03 (14 models, 57.0)
    2: (-20, -10), # case_07 (37 models, 18.5)
    3: (5, 5),   # case_08 (29 models, -3.4)
    4: (5, -10), # case_09 (44 models, 29.3)
    5: (5, -10), # case_12 (75 models, 17.8)
    6: (5, -10), # case_11 (169 models, -10.5)
    7: (5, 5),   # case_13 (324 models, -10.8)
}
for i, (m, s, c) in enumerate(zip(all_image_models, all_image_scores,
                                   cases + image_only_cases)):
    short = c.split()[-1]
    ox, oy = img_label_offsets.get(i, (4, -8))
    ax.annotate(short, (m, s), textcoords='offset points',
                xytext=(ox, oy), fontsize=6.5, color='#4472C4', fontweight='bold')

xl_label_offsets = {
    0: (-18, -10), # case_02 excel 42.1
    1: (5, 5),     # case_03 excel 95.5
    2: (5, 5),     # case_07 excel 94.2
    3: (5, 5),     # case_08 excel 74.1
    4: (5, 5),     # case_09 excel -5.3
    5: (5, 5),     # case_12 excel 29.4
}
for i, (m, s, c) in enumerate(zip(models, excel_scores, cases)):
    short = c.split()[-1]
    ox, oy = xl_label_offsets.get(i, (4, 5))
    ax.annotate(short, (m, s), textcoords='offset points',
                xytext=(ox, oy), fontsize=6.5, color='#ED7D31', fontweight='bold')

ax.axhline(y=0, color='black', linewidth=0.5, zorder=1)
ax.set_xlabel('Number of geometric models')
ax.set_ylabel('Geometry accuracy score (%)')
ax.set_ylim(-25, 110)
ax.set_xlim(0, 350)
ax.grid(alpha=0.3, zorder=1)
ax.legend(loc='upper right', framealpha=0.9, edgecolor='gray', fancybox=False)
ax.spines['top'].set_visible(False)
ax.spines['right'].set_visible(False)

fig.savefig('png/score_vs_complexity.png')
plt.close(fig)
print('Saved: png/score_vs_complexity.png')
