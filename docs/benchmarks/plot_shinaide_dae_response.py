"""Plot only recorded CPU DAE values; no fitting, smoothing or simulated data."""
from pathlib import Path
import csv
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

base=Path(__file__).resolve().parent
with (base/'shinaide-dae-load-step-2026-09-26.csv').open() as stream:
    rows=list(csv.DictReader(stream))
def column(name):return np.array([float(row[name]) for row in rows])
t=column('time_s')
heat=column('Qit_W')/1000  # CSV's historical label: LIQUID heat, not total IT power.
fig,axes=plt.subplots(3,1,figsize=(9,8),sharex=True,layout='constrained')
fig.suptitle('Coupled liquid-cooling DAE: a 20% heat-load step',fontsize=15)
axes[0].plot(np.r_[0,0,t[1:]],np.r_[heat[0],heat[1],heat[1:]],color='#333333',label='Liquid heat input')
axes[0].plot(t,column('primary_rejection_W')/1000,color='#0072B2',label='Heat carried out by primary circuit')
axes[0].step(t,column('storage_rate_W')/1000,where='pre',color='#D55E00',linestyle='--',label='Storage rate (BE step mean)')
axes[0].set_ylabel('Thermal power [kW]');axes[0].set_ylim(0,28)
axes[0].legend(fontsize=9,loc='center right',frameon=False)
primary=column('primary_return_K')-273.15
axes[1].plot(t,primary,color='#0072B2',linewidth=2)
axes[1].set_ylabel('Primary return [°C]')
axes[1].set_title(f'Primary inlet held at 20 °C; return changes by {primary[-1]-primary[0]:.3f} K',fontsize=11,loc='left')
axes[1].ticklabel_format(axis='y',style='plain',useOffset=False)
axes[2].plot(t,column('secondary_supply_K')-273.15,color='#0072B2',label='Secondary supply')
axes[2].plot(t,column('secondary_return_K')-273.15,color='#D55E00',linestyle='--',label='Secondary return')
axes[2].set_ylabel('Secondary water [°C]');axes[2].set_xlabel('Simulated time [s]')
axes[2].legend(frameon=False,fontsize=9)
for ax in axes:
    ax.set_xlim(0,t[-1]);ax.grid(alpha=.2)
    ax.spines[['top','right']].set_visible(False)
fig.supxlabel('Mock two-volume CDU; CPU float64 Newton + BE, dt = 5 s.\n600 s is still transient. Not calibrated to NV operating data.',fontsize=9)
fig.savefig(base/'shinaide-dae-response-2026-09-26.png',dpi=160)
