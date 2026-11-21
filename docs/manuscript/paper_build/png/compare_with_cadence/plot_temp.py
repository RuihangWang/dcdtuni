import pandas as pd
import matplotlib.pyplot as plt
import matplotlib

matplotlib.rcParams.update({
    'font.family': 'serif',
    'font.serif': ['DejaVu Serif'],
    'font.size': 11,
    'axes.labelsize': 12,
    'legend.fontsize': 12,
    'figure.dpi': 150,
})

cadence = pd.read_csv("Caence/acu_return_air_temperature.csv")
dctwin = pd.read_csv("DCTwin/sensor_temperatures.csv")

fig, ax = plt.subplots(figsize=(7, 4))

ax.plot(cadence["Iteration"], cadence["ACU01_ReturnAirTemperature"],
        '--', color='#1f77b4', linewidth=3.0, label='Commercial Soft. ACU 1')
ax.plot(cadence["Iteration"], cadence["ACU02_ReturnAirTemperature"],
        '--', color='#d62728', linewidth=3.0, label='Commercial Soft. ACU 2')

sensor_cols = dctwin.columns.tolist()
ax.plot(dctwin["iteration"], dctwin[sensor_cols[1]],
        '-', color='#1f77b4', linewidth=3.0, label='DCTwin ACU 1')
ax.plot(dctwin["iteration"], dctwin[sensor_cols[3]],
        '-', color='#d62728', linewidth=3.0, label='DCTwin ACU 2')

ax.set_xlabel("Iteration")
ax.set_ylabel("ACU Return Air Temperature (°C)")
ax.legend(loc='upper right', framealpha=0.9)
ax.set_xlim(0, max(cadence["Iteration"].max(), dctwin["iteration"].max()))
ax.grid(True, alpha=0.3)

fig.tight_layout()
fig.savefig("case02_acu_temperature.png", dpi=500, bbox_inches='tight')
plt.close()
print("Saved case02_acu_temperature.png")
