"""Generate data center black-box modeling schematic."""
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib
matplotlib.use("Agg")

plt.rcParams.update({"font.family": "sans-serif", "font.size": 10, "figure.dpi": 200})

fig, ax = plt.subplots(figsize=(12, 6))

room = mpatches.FancyBboxPatch((0.5, 0.5), 11, 5.5, boxstyle="round,pad=0.1",
                                facecolor="#f0f4f8", edgecolor="#2c3e50", linewidth=2)
ax.add_patch(room)

plenum = mpatches.Rectangle((0.5, 0.5), 11, 1.0, facecolor="#d5e8d4", edgecolor="#2c3e50",
                             linewidth=1.5, linestyle="--")
ax.add_patch(plenum)
ax.text(6.0, 1.0, "Raised Floor Plenum", ha="center", va="center", fontsize=9,
        style="italic", color="#666")

# Perforated tiles — in the cold aisle between the two racks
for x_pos in [5.2, 5.8, 6.4]:
    tile = mpatches.Rectangle((x_pos, 1.4), 0.6, 0.2, facecolor="#a8d08d",
                               edgecolor="#2c3e50", linewidth=1)
    ax.add_patch(tile)
    ax.annotate("", xy=(x_pos + 0.3, 2.0), xytext=(x_pos + 0.3, 1.4),
                arrowprops=dict(arrowstyle="->", color="#27ae60", lw=1.5))
ax.text(5.8, 1.15, "Perforated Tiles", ha="center", fontsize=8, color="#27ae60")

# CRAC unit
crac = mpatches.FancyBboxPatch((1.0, 1.8), 1.5, 3.5, boxstyle="round,pad=0.05",
                                facecolor="#aed6f1", edgecolor="#2980b9", linewidth=2)
ax.add_patch(crac)
ax.text(1.75, 3.55, "CRAC", ha="center", va="center", fontsize=12, fontweight="bold",
        color="#2980b9")

ax.annotate("", xy=(1.3, 1.6), xytext=(1.3, 1.8),
            arrowprops=dict(arrowstyle="-|>", color="#3498db", lw=2))
ax.text(1.3, 1.35, "Supply\nT=20°C\nV=2.5 m/s", ha="center", fontsize=7, color="#2980b9")

ax.annotate("", xy=(2.2, 5.3), xytext=(2.2, 5.1),
            arrowprops=dict(arrowstyle="-|>", color="#e74c3c", lw=2))
ax.text(2.2, 5.55, "Return\nT=f(room)", ha="center", fontsize=7, color="#c0392b")

# Rack 1 at [3.5, 5.3] — faces RIGHT (inlet on right side toward cold aisle)
rack1 = mpatches.FancyBboxPatch((3.5, 1.8), 1.8, 3.5, boxstyle="round,pad=0.05",
                                 facecolor="#fadbd8", edgecolor="#e74c3c", linewidth=2)
ax.add_patch(rack1)
ax.text(4.4, 3.55, "Rack 1", ha="center", va="center", fontsize=11,
        fontweight="bold", color="#c0392b")
for j in range(4):
    sy = 2.1 + j * 0.7
    srv = mpatches.Rectangle((3.65, sy), 1.5, 0.5,
                              facecolor="#f5b7b1", edgecolor="#e74c3c", linewidth=0.5)
    ax.add_patch(srv)

# Rack 1 arrows: exhaust LEFT (hot), intake RIGHT (cold)
ax.annotate("", xy=(3.0, 3.0), xytext=(3.5, 3.0),
            arrowprops=dict(arrowstyle="-|>", color="#e74c3c", lw=1.5))
ax.annotate("", xy=(5.3, 3.0), xytext=(5.8, 3.0),
            arrowprops=dict(arrowstyle="-|>", color="#3498db", lw=1.5))

# Rack 2 at [7.0, 8.8] — faces LEFT (inlet on left side toward cold aisle)
rack2 = mpatches.FancyBboxPatch((7.0, 1.8), 1.8, 3.5, boxstyle="round,pad=0.05",
                                 facecolor="#fadbd8", edgecolor="#e74c3c", linewidth=2)
ax.add_patch(rack2)
ax.text(7.9, 3.55, "Rack 2", ha="center", va="center", fontsize=11,
        fontweight="bold", color="#c0392b")
for j in range(4):
    sy = 2.1 + j * 0.7
    srv = mpatches.Rectangle((7.15, sy), 1.5, 0.5,
                              facecolor="#f5b7b1", edgecolor="#e74c3c", linewidth=0.5)
    ax.add_patch(srv)

# Rack 2 arrows: intake LEFT (cold), exhaust RIGHT (hot)
ax.annotate("", xy=(7.0, 3.0), xytext=(6.5, 3.0),
            arrowprops=dict(arrowstyle="-|>", color="#3498db", lw=1.5))
ax.annotate("", xy=(9.3, 3.0), xytext=(8.8, 3.0),
            arrowprops=dict(arrowstyle="-|>", color="#e74c3c", lw=1.5))

# Flow labels
ax.text(2.8, 3.0, "Hot\nAir Out", ha="center", fontsize=7, color="#c0392b")
ax.text(6.15, 3.0, "Cold\nAisle", ha="center", fontsize=8, fontweight="bold", color="#2980b9")
ax.text(9.6, 3.0, "Hot\nAir Out", ha="center", fontsize=7, color="#c0392b")

# Wall labels
ax.text(6.0, 5.8, "Ceiling: adiabatic wall (zeroGradient)", ha="center", fontsize=8,
        color="#7f8c8d")
ax.text(6.0, 0.25, "Floor: adiabatic wall (zeroGradient)", ha="center", fontsize=8,
        color="#7f8c8d")

# BC labels box
bc_text = (
    "Boundary Conditions:\n"
    "  CRAC Supply:  fixedValue T, flowRateInletVelocity U\n"
    "  CRAC Return:  zeroGradient T, inletOutlet U\n"
    "  Server Front:  flowRateOutletVelocity U\n"
    "  Server Rear:   flowRateInletVelocity U, T=T_in+P/(mdot*cp)\n"
    "  Perf. Tiles:  porousBafflePressure p_rgh\n"
    "  Walls:        noSlip U, zeroGradient T"
)
ax.text(11.8, 3.5, bc_text, fontsize=7, fontfamily="monospace", va="center",
        bbox=dict(boxstyle="round,pad=0.4", facecolor="lightyellow", edgecolor="#bbb"))

ax.set_xlim(-0.2, 16.5)
ax.set_ylim(-0.1, 6.5)
ax.set_aspect("equal")
ax.axis("off")
ax.set_title("Data Center CFD: Black-Box Component Modeling", fontsize=14, fontweight="bold",
             pad=15)

fig.tight_layout()
fig.savefig("docs/dctwinWechatPaper/png/blackbox_schematic.png", bbox_inches="tight")
plt.close(fig)
print("Saved blackbox_schematic.png")
