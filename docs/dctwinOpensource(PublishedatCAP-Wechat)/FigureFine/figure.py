import pandas as pd
import matplotlib.pyplot as plt
import numpy as np

plt.rcParams.update({
    'font.size': 12,
    'axes.labelsize': 13,
    'xtick.labelsize': 10,
    'ytick.labelsize': 11,
    'legend.fontsize': 10,
    'font.family': 'DejaVu Sans',
    'axes.grid': True,
    'grid.alpha': 0.3,
    'grid.linestyle': '--',
})

df = pd.read_csv("ref.csv")
df.columns = df.columns.str.strip().str.replace('﻿', '')

sensors = df['Sensor'].astype(str).values
experiment = df['Exp'].astype(float).values
fluent = df['Fluent'].astype(float).values
of_vals = df['OF'].astype(float).values

mae_fluent = np.mean(np.abs(fluent - experiment))
mae_of = np.mean(np.abs(of_vals - experiment))

x = np.arange(len(sensors))

fig, ax = plt.subplots(figsize=(12, 5))

ax.plot(x, experiment, marker='o', markersize=4, linewidth=1.5,
        color='black', label=f'Experiment', zorder=3)
ax.plot(x, fluent, marker='s', markersize=4, linewidth=1.5,
        color='#2166AC', label=f'6SigmaET (MAE={mae_fluent:.2f} °C)', zorder=2)
ax.plot(x, of_vals, marker='^', markersize=4, linewidth=1.5,
        color='#D62728', label=f'OpenFOAM / DCTwin (MAE={mae_of:.2f} °C)', zorder=2)

ax.set_xlabel('Sensor Location')
ax.set_ylabel('Temperature (°C)')
ax.set_title('Fine Case: Temperature Comparison at 80 Sensor Locations')

tick_step = 4
tick_positions = x[::tick_step]
tick_labels = sensors[::tick_step]
ax.set_xticks(tick_positions)
ax.set_xticklabels(tick_labels, rotation=45, ha='right')

y_min = min(experiment.min(), fluent.min(), of_vals.min()) - 1
y_max = max(experiment.max(), fluent.max(), of_vals.max()) + 1
ax.set_ylim(y_min, y_max)
ax.yaxis.set_major_locator(plt.MultipleLocator(4))

ax.legend(loc='upper right', framealpha=0.9)
ax.set_xlim(-0.5, len(sensors) - 0.5)

fig.tight_layout()
fig.savefig("comparison_plot.png", dpi=300, bbox_inches='tight')
print(f"MAE 6SigmaET: {mae_fluent:.3f}")
print(f"MAE OpenFOAM: {mae_of:.3f}")
print("Saved: comparison_plot.png")
