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

df = pd.read_csv("Sigma-Ex-OF.csv")
locations = df.columns[1:].values
experiment = df.iloc[0, 1:].astype(float).values
sigma_rui = df.iloc[1, 1:].astype(float).values
of1 = df.iloc[7, 1:].astype(float).values

mae_sigma = np.mean(np.abs(sigma_rui - experiment))
mae_of = np.mean(np.abs(of1 - experiment))

x = np.arange(len(locations))

fig, ax = plt.subplots(figsize=(12, 5))

ax.plot(x, experiment, marker='o', markersize=6, linewidth=1.5,
        color='black', label=f'Experiment', zorder=3)
ax.plot(x, sigma_rui, marker='s', markersize=6, linewidth=1.5,
        color='#2166AC', label=f'6SigmaET (MAE={mae_sigma:.2f} °C)', zorder=2)
ax.plot(x, of1, marker='^', markersize=6, linewidth=1.5,
        color='#D62728', label=f'OpenFOAM / DCTwin (MAE={mae_of:.2f} °C)', zorder=2)

ax.set_xlabel('Sensor Location')
ax.set_ylabel('Temperature (°C)')
ax.set_title('Coarse Case: Temperature Comparison at 26 Sensor Locations')

ax.set_xticks(x)
ax.set_xticklabels(locations, rotation=45, ha='right')

y_min = min(experiment.min(), sigma_rui.min(), of1.min()) - 1
y_max = max(experiment.max(), sigma_rui.max(), of1.max()) + 1
ax.set_ylim(y_min, y_max)
ax.yaxis.set_major_locator(plt.MultipleLocator(2))

ax.legend(loc='upper right', framealpha=0.9)
ax.set_xlim(-0.5, len(locations) - 0.5)

fig.tight_layout()
fig.savefig("comparison_plot.png", dpi=300, bbox_inches='tight')
print(f"MAE 6SigmaET: {mae_sigma:.3f}")
print(f"MAE OpenFOAM: {mae_of:.3f}")
print("Saved: comparison_plot.png")
