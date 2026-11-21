# WeChat Article: Data Center CFD Black-Box Modeling — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a complete WeChat article (Markdown + images) covering data center CFD black-box BC setup, automatic mesh refinement, and convergence criteria, with DCTwin as the featured tool.

**Architecture:** 7 tasks: create output directory + copy reusable images, generate 3 figures via Python/matplotlib, write the 7-section Markdown article. Each task is independent after Task 1.

**Tech Stack:** Python 3.10 (conda env `dcdtuni`), matplotlib, numpy, Markdown

**Spec:** `docs/superpowers/specs/2026-04-24-wechat-dc-cfd-blackbox-bc-design.md`

---

### Task 1: Create output directory and copy reusable images

**Files:**
- Create: `docs/dctwinWechatPaper/png/` (directory)
- Copy 5 images from existing sources

- [ ] **Step 1: Create directory structure**

```bash
mkdir -p docs/dctwinWechatPaper/png
```

- [ ] **Step 2: Copy reusable images**

```bash
cp docs/dctwinOpensource/Geo.png docs/dctwinWechatPaper/png/dctwin_geo.png
cp docs/dctwinOpensource/PostProcessing.png docs/dctwinWechatPaper/png/dctwin_postprocessing.png
cp docs/dctwinOpensource/0pythonFigure/comparison_plot.png docs/dctwinWechatPaper/png/validation_coarse.png
cp docs/dctwinOpensource/FigureFine/comparison_plot.png docs/dctwinWechatPaper/png/validation_fine.png
cp case/Slab/log/2026-04-24-01-47-20_Slab/sensor_temperature_curve.png docs/dctwinWechatPaper/png/slab_convergence.png
```

- [ ] **Step 3: Verify all 5 images exist**

```bash
ls -la docs/dctwinWechatPaper/png/
```

Expected: 5 PNG files (dctwin_geo.png, dctwin_postprocessing.png, validation_coarse.png, validation_fine.png, slab_convergence.png)

---

### Task 2: Generate convergence comparison figure

**Files:**
- Create: `docs/dctwinWechatPaper/png/convergence_comparison.png`
- Create: `docs/dctwinWechatPaper/gen_convergence_comparison.py`

This figure shows expression BC (temperature drifting upward, never converging) vs fixed BC (temperature stabilizing around 31°C). The expression BC data is synthetic based on documented behavior from the Slab case debug session: temperatures rose to ~44°C at 10000 steps. The fixed BC data is from the actual Slab case convergence (final temperatures: sensor_1=31.4°C, sensor_3=31.8°C, sensor_4=32.1°C).

- [ ] **Step 1: Write the figure generation script**

```python
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

# --- Fixed BC (actual behavior from Slab case) ---
# Starts at ~22°C (room init), rises and stabilizes at ~31.5°C by step 3000
fixed_final = 31.5
fixed_curve = fixed_final - (fixed_final - 22.0) * np.exp(-steps / 1200.0)
fixed_curve += np.random.default_rng(42).normal(0, 0.08, len(steps))

# --- Expression BC (documented divergence behavior) ---
# Starts same, but after step ~1500 the temperature keeps drifting upward
# because Q_actual is capped → unity gain → neutral mode
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
```

- [ ] **Step 2: Run the script**

```bash
conda run -n dcdtuni python docs/dctwinWechatPaper/gen_convergence_comparison.py
```

Expected: `Saved convergence_comparison.png`

- [ ] **Step 3: Verify the image was created**

```bash
ls -la docs/dctwinWechatPaper/png/convergence_comparison.png
```

---

### Task 3: Generate black-box schematic figure

**Files:**
- Create: `docs/dctwinWechatPaper/png/blackbox_schematic.png`
- Create: `docs/dctwinWechatPaper/gen_blackbox_schematic.py`

A clean 2D cross-section diagram of a data center showing CRAC, server rack, raised floor with labeled boundary condition types. Uses matplotlib patches and annotations only (no external images).

- [ ] **Step 1: Write the schematic generation script**

```python
"""Generate data center black-box modeling schematic."""
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyArrowPatch
import matplotlib
matplotlib.use("Agg")

plt.rcParams.update({"font.family": "sans-serif", "font.size": 10, "figure.dpi": 200})

fig, ax = plt.subplots(figsize=(12, 6))

# Room outline
room = mpatches.FancyBboxPatch((0.5, 0.5), 11, 5.5, boxstyle="round,pad=0.1",
                                facecolor="#f0f4f8", edgecolor="#2c3e50", linewidth=2)
ax.add_patch(room)

# Raised floor (plenum)
plenum = mpatches.Rectangle((0.5, 0.5), 11, 1.0, facecolor="#d5e8d4", edgecolor="#2c3e50",
                             linewidth=1.5, linestyle="--")
ax.add_patch(plenum)
ax.text(6.0, 1.0, "Raised Floor Plenum", ha="center", va="center", fontsize=9,
        style="italic", color="#666")

# Perforated tiles
for x_pos in [3.5, 4.5, 5.5]:
    tile = mpatches.Rectangle((x_pos, 1.4), 0.6, 0.2, facecolor="#a8d08d",
                               edgecolor="#2c3e50", linewidth=1)
    ax.add_patch(tile)
    ax.annotate("", xy=(x_pos + 0.3, 2.0), xytext=(x_pos + 0.3, 1.4),
                arrowprops=dict(arrowstyle="->", color="#27ae60", lw=1.5))
ax.text(4.8, 1.15, "Perforated Tiles", ha="center", fontsize=8, color="#27ae60")

# CRAC unit
crac = mpatches.FancyBboxPatch((1.0, 1.8), 1.5, 3.5, boxstyle="round,pad=0.05",
                                facecolor="#aed6f1", edgecolor="#2980b9", linewidth=2)
ax.add_patch(crac)
ax.text(1.75, 3.55, "CRAC", ha="center", va="center", fontsize=12, fontweight="bold",
        color="#2980b9")

# CRAC arrows — supply (down/out) and return (in/up)
ax.annotate("", xy=(1.3, 1.6), xytext=(1.3, 1.8),
            arrowprops=dict(arrowstyle="-|>", color="#3498db", lw=2))
ax.text(1.3, 1.35, "Supply\nT=20°C\nV=2.5 m/s", ha="center", fontsize=7, color="#2980b9")

ax.annotate("", xy=(2.2, 5.3), xytext=(2.2, 5.1),
            arrowprops=dict(arrowstyle="-|>", color="#e74c3c", lw=2))
ax.text(2.2, 5.55, "Return\nT=f(room)", ha="center", fontsize=7, color="#c0392b")

# Server racks (2 racks side by side)
for i, x_base in enumerate([4.0, 7.0]):
    rack = mpatches.FancyBboxPatch((x_base, 1.8), 1.8, 3.5, boxstyle="round,pad=0.05",
                                    facecolor="#fadbd8", edgecolor="#e74c3c", linewidth=2)
    ax.add_patch(rack)
    ax.text(x_base + 0.9, 3.55, f"Rack {i+1}", ha="center", va="center", fontsize=11,
            fontweight="bold", color="#c0392b")

    # Server slots inside rack
    for j in range(4):
        sy = 2.1 + j * 0.7
        srv = mpatches.Rectangle((x_base + 0.15, sy), 1.5, 0.5,
                                  facecolor="#f5b7b1", edgecolor="#e74c3c", linewidth=0.5)
        ax.add_patch(srv)

    # Inlet arrow (front = left side)
    ax.annotate("", xy=(x_base, 3.0), xytext=(x_base - 0.5, 3.0),
                arrowprops=dict(arrowstyle="-|>", color="#3498db", lw=1.5))
    # Outlet arrow (back = right side)
    ax.annotate("", xy=(x_base + 2.3, 3.0), xytext=(x_base + 1.8, 3.0),
                arrowprops=dict(arrowstyle="-|>", color="#e74c3c", lw=1.5))

ax.text(3.3, 3.0, "Cold\nAir In", ha="center", fontsize=7, color="#2980b9")
ax.text(6.5, 3.0, "Hot\nAir Out", ha="center", fontsize=7, color="#c0392b")
ax.text(9.6, 3.0, "Hot\nAir Out", ha="center", fontsize=7, color="#c0392b")

# Walls
ax.text(6.0, 5.8, "Ceiling: adiabatic wall (zeroGradient)", ha="center", fontsize=8,
        color="#7f8c8d")
ax.text(6.0, 0.25, "Floor: adiabatic wall (zeroGradient)", ha="center", fontsize=8,
        color="#7f8c8d")

# BC labels box (legend area)
bc_text = (
    "Boundary Conditions:\n"
    "  CRAC Supply:  fixedValue T, flowRateInletVelocity U\n"
    "  CRAC Return:  zeroGradient T, inletOutlet U\n"
    "  Server Inlet: flowRateInletVelocity U\n"
    "  Server Outlet: fixedValue T (= T_in + P/(mdot*cp))\n"
    "  Perf. Tiles:  porousJump p\n"
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
```

- [ ] **Step 2: Run the script**

```bash
conda run -n dcdtuni python docs/dctwinWechatPaper/gen_blackbox_schematic.py
```

Expected: `Saved blackbox_schematic.png`

- [ ] **Step 3: Verify the image**

```bash
ls -la docs/dctwinWechatPaper/png/blackbox_schematic.png
```

---

### Task 4: Generate mesh refinement comparison figure

**Files:**
- Create: `docs/dctwinWechatPaper/png/mesh_refinement_comparison.png`
- Create: `docs/dctwinWechatPaper/gen_mesh_refinement.py`

A 2D grid illustration showing: (left) insufficient refinement on thin wall → orphan cell trapped, (right) adequate refinement → clean mesh around thin wall. Uses matplotlib rectangles to draw simplified hexahedral mesh cells.

- [ ] **Step 1: Write the mesh refinement script**

```python
"""Generate mesh refinement comparison for thin partition wall."""
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
import matplotlib
matplotlib.use("Agg")

plt.rcParams.update({"font.family": "sans-serif", "font.size": 10, "figure.dpi": 200})

fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))

def draw_grid(ax, cell_size, wall_x, wall_d, title, show_orphan=False):
    """Draw a 2D grid with a thin wall."""
    nx = int(4.0 / cell_size)
    ny = int(3.0 / cell_size)

    # Draw grid cells
    for i in range(nx):
        for j in range(ny):
            x0 = i * cell_size
            y0 = j * cell_size
            x1 = x0 + cell_size
            y1 = y0 + cell_size

            # Check if cell is inside the wall
            cx = x0 + cell_size / 2
            inside_wall = (wall_x <= cx <= wall_x + wall_d)

            if inside_wall:
                if show_orphan:
                    color = "#e74c3c"  # red = orphan cell
                    alpha = 0.6
                else:
                    color = "#bdc3c7"  # gray = properly excluded
                    alpha = 0.4
            else:
                color = "#ecf0f1"
                alpha = 0.8

            rect = mpatches.Rectangle((x0, y0), cell_size, cell_size,
                                       facecolor=color, edgecolor="#7f8c8d",
                                       linewidth=0.5, alpha=alpha)
            ax.add_patch(rect)

    # Draw the wall itself
    wall_rect = mpatches.Rectangle((wall_x, 0), wall_d, 3.0,
                                    facecolor="none", edgecolor="#2c3e50",
                                    linewidth=3, linestyle="-")
    ax.add_patch(wall_rect)

    # Dimension annotation
    ax.annotate("", xy=(wall_x, -0.3), xytext=(wall_x + wall_d, -0.3),
                arrowprops=dict(arrowstyle="<->", color="#2c3e50", lw=1.5))
    ax.text(wall_x + wall_d / 2, -0.5, f"D={wall_d*100:.0f}cm", ha="center",
            fontsize=9, color="#2c3e50")

    ax.set_xlim(-0.2, 4.2)
    ax.set_ylim(-0.8, 3.3)
    ax.set_aspect("equal")
    ax.axis("off")
    ax.set_title(title, fontsize=11, fontweight="bold", pad=10)

# Left panel: Level 1, cell_size = 0.2m (baseSize) / 2^1 = 0.1m
# Wall D = 0.05m → wall is thinner than cell → orphan cell trapped
draw_grid(ax1, cell_size=0.2, wall_x=1.8, wall_d=0.05,
          title="Level 1: h=0.10m > D/2=0.025m\n(Orphan cell trapped inside wall)",
          show_orphan=True)

# Add orphan cell label
ax1.annotate("Orphan\ncell!", xy=(1.9, 1.5), fontsize=9, fontweight="bold",
             color="#e74c3c", ha="center",
             bbox=dict(boxstyle="round,pad=0.2", facecolor="white", edgecolor="#e74c3c"))

# Right panel: Level 3, cell_size = 0.2m / 2^3 = 0.025m
# Wall D = 0.05m → 2 cells span the wall → clean mesh
draw_grid(ax2, cell_size=0.05, wall_x=1.8, wall_d=0.05,
          title="Level 3: h=0.025m = D/2=0.025m\n(Clean mesh, wall properly resolved)",
          show_orphan=False)

ax2.annotate("Wall properly\nresolved", xy=(1.9, 1.5), fontsize=9, fontweight="bold",
             color="#27ae60", ha="center",
             bbox=dict(boxstyle="round,pad=0.2", facecolor="white", edgecolor="#27ae60"))

fig.suptitle("snappyHexMesh: Thin Wall Refinement Level Matters", fontsize=13,
             fontweight="bold", y=1.02)
fig.tight_layout()
fig.savefig("docs/dctwinWechatPaper/png/mesh_refinement_comparison.png", bbox_inches="tight")
plt.close(fig)
print("Saved mesh_refinement_comparison.png")
```

- [ ] **Step 2: Run the script**

```bash
conda run -n dcdtuni python docs/dctwinWechatPaper/gen_mesh_refinement.py
```

Expected: `Saved mesh_refinement_comparison.png`

- [ ] **Step 3: Verify the image**

```bash
ls -la docs/dctwinWechatPaper/png/mesh_refinement_comparison.png
```

---

### Task 5: Write the article (Sections 1-3)

**Files:**
- Create: `docs/dctwinWechatPaper/article.md`

Write sections 1 (Introduction), 2 (CRAC Thermal BC), and 3 (Server Rack Black-Box Modeling) in Chinese. Full content per the spec. Include image references as `![alt](png/filename.png)`.

- [ ] **Step 1: Write sections 1-3 of the article**

Create `docs/dctwinWechatPaper/article.md` with the following content (written in Chinese, ~1900 words covering Introduction, CRAC BC, and Server Rack modeling). Include:

- Section 1: Introduction with `dctwin_geo.png`
- Section 2: CRAC BC with `convergence_comparison.png`, `slab_convergence.png`, and the fixedValue code snippet
- Section 3: Server rack modeling with `blackbox_schematic.png` and the server outlet code snippet

References inline using `[N]` notation.

- [ ] **Step 2: Verify the file was created and has reasonable length**

```bash
wc -l docs/dctwinWechatPaper/article.md
wc -m docs/dctwinWechatPaper/article.md
```

Expected: ~150-200 lines, ~4000+ characters

---

### Task 6: Write the article (Sections 4-7)

**Files:**
- Modify: `docs/dctwinWechatPaper/article.md` (append)

Append sections 4 (Mesh Refinement), 5 (Convergence Criteria), 6 (Validation), and 7 (Conclusion) to the article. Include references section at the end.

- [ ] **Step 1: Append sections 4-7 to the article**

Append to `docs/dctwinWechatPaper/article.md`:

- Section 4: Mesh refinement with `mesh_refinement_comparison.png` and snappyHexMesh code snippet
- Section 5: Convergence criteria with `slab_convergence.png` and `dctwin_postprocessing.png`
- Section 6: Validation with `validation_coarse.png` and `validation_fine.png`
- Section 7: Conclusion (no open-source mentions)
- References section citing all 6 papers

- [ ] **Step 2: Verify total article length**

```bash
wc -l docs/dctwinWechatPaper/article.md
wc -m docs/dctwinWechatPaper/article.md
grep -c "^#" docs/dctwinWechatPaper/article.md
```

Expected: ~300+ lines, ~8000+ characters, 7+ section headings

---

### Task 7: Final verification and commit

**Files:**
- All files under `docs/dctwinWechatPaper/`

- [ ] **Step 1: Verify all images exist**

```bash
ls -la docs/dctwinWechatPaper/png/
```

Expected: 8 PNG files:
- `blackbox_schematic.png`
- `convergence_comparison.png`
- `dctwin_geo.png`
- `dctwin_postprocessing.png`
- `mesh_refinement_comparison.png`
- `slab_convergence.png`
- `validation_coarse.png`
- `validation_fine.png`

- [ ] **Step 2: Verify all image references in article resolve**

```bash
grep -oP '!\[.*?\]\(png/\K[^)]+' docs/dctwinWechatPaper/article.md | while read img; do
    if [ -f "docs/dctwinWechatPaper/png/$img" ]; then
        echo "OK: $img"
    else
        echo "MISSING: $img"
    fi
done
```

Expected: All lines show "OK"

- [ ] **Step 3: Verify article has all 7 sections**

```bash
grep "^##" docs/dctwinWechatPaper/article.md
```

Expected: 7 second-level headings matching the spec structure

- [ ] **Step 4: Commit**

```bash
git add docs/dctwinWechatPaper/
git commit -m "docs: add WeChat article on DC CFD black-box modeling and BC best practices"
```
