"""Generate mesh refinement comparison for thin partition wall."""
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib
matplotlib.use("Agg")

plt.rcParams.update({"font.family": "sans-serif", "font.size": 10, "figure.dpi": 200})

fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))

WALL_X = 1.8
WALL_D = 0.05
X_LO, X_HI = 1.0, 2.6
Y_LO, Y_HI = 0.0, 1.6

def draw_grid(ax, cell_size, title, show_orphan=False):
    i_lo = int(X_LO / cell_size)
    i_hi = int(X_HI / cell_size)
    j_lo = int(Y_LO / cell_size)
    j_hi = int(Y_HI / cell_size)

    for i in range(i_lo, i_hi):
        for j in range(j_lo, j_hi):
            x0 = i * cell_size
            y0 = j * cell_size
            cx = x0 + cell_size / 2
            inside_wall = (WALL_X <= cx <= WALL_X + WALL_D)

            if inside_wall:
                if show_orphan:
                    color = "#e74c3c"
                    alpha = 0.6
                else:
                    color = "#bdc3c7"
                    alpha = 0.4
            else:
                color = "#ecf0f1"
                alpha = 0.8

            rect = mpatches.Rectangle((x0, y0), cell_size, cell_size,
                                       facecolor=color, edgecolor="#7f8c8d",
                                       linewidth=0.5, alpha=alpha)
            ax.add_patch(rect)

    wall_rect = mpatches.Rectangle((WALL_X, Y_LO), WALL_D, Y_HI - Y_LO,
                                    facecolor="none", edgecolor="#2c3e50",
                                    linewidth=3, linestyle="-")
    ax.add_patch(wall_rect)

    ax.annotate("", xy=(WALL_X, Y_LO - 0.12), xytext=(WALL_X + WALL_D, Y_LO - 0.12),
                arrowprops=dict(arrowstyle="<->", color="#2c3e50", lw=1.5))
    ax.text(WALL_X + WALL_D / 2, Y_LO - 0.25, f"D={WALL_D*100:.0f}cm", ha="center",
            fontsize=9, color="#2c3e50")

    ax.set_xlim(X_LO - 0.15, X_HI + 0.15)
    ax.set_ylim(Y_LO - 0.4, Y_HI + 0.15)
    ax.set_aspect("equal")
    ax.axis("off")
    ax.set_title(title, fontsize=11, fontweight="bold", pad=10)

# Left panel: Level 1 (baseSize=0.2m, h=0.1m) — orphan cell at cx=1.85
draw_grid(ax1, cell_size=0.1,
          title="Level 1: h=0.10m > D/2=0.025m\n(Orphan cell trapped inside wall)",
          show_orphan=True)

ax1.annotate("Orphan\ncell!", xy=(1.85, 0.85), fontsize=9, fontweight="bold",
             color="#e74c3c", ha="center",
             bbox=dict(boxstyle="round,pad=0.2", facecolor="white", edgecolor="#e74c3c"))

# Right panel: Level 3 (baseSize=0.2m, h=0.025m) — wall properly resolved
draw_grid(ax2, cell_size=0.025,
          title="Level 3: h=0.025m = D/2=0.025m\n(Clean mesh, wall properly resolved)",
          show_orphan=False)

ax2.annotate("Wall properly\nresolved", xy=(1.85, 0.85), fontsize=9, fontweight="bold",
             color="#27ae60", ha="center",
             bbox=dict(boxstyle="round,pad=0.2", facecolor="white", edgecolor="#27ae60"))

fig.suptitle("snappyHexMesh: Thin Wall Refinement Level Matters", fontsize=13,
             fontweight="bold", y=1.02)
fig.tight_layout()
fig.savefig("docs/dctwinWechatPaper/png/mesh_refinement_comparison.png", bbox_inches="tight")
plt.close(fig)
print("Saved mesh_refinement_comparison.png")
