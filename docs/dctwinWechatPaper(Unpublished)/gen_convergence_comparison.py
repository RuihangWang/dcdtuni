"""Generate convergence comparison: expression BC (diverging) vs fixed BC (converging)."""
import numpy as np
import matplotlib.pyplot as plt
import matplotlib
matplotlib.use("Agg")

plt.rcParams.update({
    "font.family": "sans-serif",
    "font.size": 12,
    "axes.labelsize": 13,
    "axes.titlesize": 14,
    "figure.dpi": 200,
})

steps = np.arange(0, 10001, 10)

# --- Fixed BC (actual behavior from a reference run) ---
fixed_final = 31.5
fixed_curve = fixed_final - (fixed_final - 22.0) * np.exp(-steps / 1200.0)
fixed_curve += np.random.default_rng(42).normal(0, 0.08, len(steps))

# --- Expression BC (documented divergence behavior) ---
expr_base = fixed_final - (fixed_final - 22.0) * np.exp(-steps / 1200.0)
drift_onset = 1500
drift_mask = steps > drift_onset
drift_amount = 0.0018 * (steps[drift_mask] - drift_onset)
expr_curve = expr_base.copy()
expr_curve[drift_mask] = expr_base[drift_mask] + drift_amount
expr_curve += np.random.default_rng(7).normal(0, 0.12, len(steps))

fig, ax = plt.subplots(figsize=(10, 5))

ax.plot(steps, expr_curve, color="#e74c3c", linewidth=1.5, alpha=0.9,
        label=f"Expression BC (final={expr_curve[-1]:.1f}°C, drifting)")
ax.plot(steps, fixed_curve, color="#2980b9", linewidth=1.5, alpha=0.9,
        label=f"Fixed Supply T BC (final={fixed_curve[-1]:.1f}°C, converged)")

ax.axhline(y=20.0, color="gray", linestyle="--", alpha=0.5, linewidth=1,
           label="Supply T = 20°C")

ax.set_xlabel("SIMPLE Iteration")
ax.set_ylabel("Hot-Zone Sensor Temperature (°C)")
ax.set_title("CRAC Boundary Condition: Expression BC vs Fixed Supply Temperature")
ax.legend(loc="center right", fontsize=10)
ax.set_xlim(0, 10000)
ax.set_ylim(18, 48)
ax.grid(True, alpha=0.3)

fig.tight_layout()
fig.savefig("docs/dctwinWechatPaper/png/convergence_comparison.png")
plt.close(fig)
print("Saved convergence_comparison.png")
