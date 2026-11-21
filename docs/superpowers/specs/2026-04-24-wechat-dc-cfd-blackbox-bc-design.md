# Design: WeChat Article — Data Center CFD Black-Box Modeling

**Date:** 2026-04-24
**Output path:** `docs/dctwinWechatPaper/`
**Format:** Markdown article + generated PNG images in `docs/dctwinWechatPaper/png/`

---

## Scope

A WeChat public account article covering data center CFD black-box modeling best practices, based on engineering experience from developing DCTwin. Straightforward narrative style (not storytelling), with key OpenFOAM code snippets at 2-3 critical points.

---

## Title

**数据中心CFD仿真中的黑箱建模：边界条件、自动网格与收敛判据**

Subtitle: **基于开源软件DCTwin的工程实践**

---

## Article Structure

### 1. Introduction (~300 words)

**Content:** Data center CFD's core challenge: servers and CRACs cannot resolve internal flow fields and must be modeled as "black boxes." The quality of boundary condition setup directly determines simulation credibility. This article covers three practical topics from DCTwin development: CRAC thermal BC, automatic mesh refinement, and steady-state convergence criteria.

**Images:** DCTwin 3D modeling screenshot (reuse `Geo.png`)

**References cited:** Wibron et al. 2018 (Energies) for general DC CFD context.

### 2. CRAC Thermal Boundary Conditions (~1000 words, main body)

**Content breakdown:**

- **2.1 The four CRAC BC methods** — Summarize CoolSim WP105's classification: Supply Temperature, Temperature Drop, Cooling Capacity, Performance Data. Explain the core formula Q = mdot * cp * (T_return - T_supply). Most academic DC CFD papers use fixed supply temperature for steady-state.

- **2.2 Expression BC trap in steady-state SIMPLE** — When supply temperature is computed dynamically from return temperature inside the SIMPLE loop (T_supply = max(T_return - Q/(mdot*cp), T_min)), and cooling capacity is capped (Q_actual = min(Q_needed, Q_max)), the derivative dT_supply/dT_return = 1 (unity gain). This creates a neutral temperature mode that SIMPLE's under-relaxation cannot damp. Result: temperatures drift indefinitely.

- **2.3 The correct approach** — Steady-state: use fixedValue BC at the set point temperature. Transient (PIMPLE): expression BC is appropriate because the outer time-stepping loop provides the necessary anchoring. For full dynamic coupling: co-simulation with 1D refrigeration model via FMU (cite IBPSA 2023, bs2023_1197).

- **2.4 DCTwin's implementation** — Automatic solver-type dispatch: steady → fixedValue, transient → expression BC. Show the key code snippet from fields.py (5-6 lines, the solver type check + BC generation).

**Images:**
- GENERATED: `convergence_comparison.png` — Side-by-side or overlay plot showing expression BC divergence (temperature drifting up to 40+°C) vs fixed BC convergence (stabilizing at ~31°C by step 3000). Use data from Slab case logs.
- REUSE: `sensor_temperature_curve.png` from Slab case (fixed supply T=20°C, 10000 steps)

**Code snippet:** The OpenFOAM T field entry for fixed supply temperature:
```
ACU_Back_patch
{
    type            fixedValue;
    value           uniform 293.15;  // 20°C set point
}
```

**References:** CoolSim WP105, IBPSA 2023 (Lu et al.), Wibron 2018

### 3. Server Rack Black-Box Modeling (~600 words)

**Content breakdown:**

- **3.1 Fan servers** — Modeled as inlet velocity BC (volumeFlowRate → velocity via patch area) + outlet heat source (fixedValue temperature or power-based expression). The server is a through-flow channel: cold air enters from the front, heated air exits from the back.

- **3.2 Non-fan equipment** — Enclosed wall boundaries. No air flow through the equipment; heat dissipates via surface convection only.

- **3.3 Raised floor / perforated tiles** — Porous jump BC. Pressure drop: ΔP = K * (0.5 * ρ * V²), where K depends on open-area ratio α. Cite the Fried & Idelchik formula from IBPSA paper.

- **3.4 DCTwin's U-slot system** — Automatic BC assignment based on equipment type. Each rack is divided into 44.45mm U-height slots; fan-servers create through-channels, non-fan equipment creates wall boundaries.

**Images:**
- GENERATED: `blackbox_schematic.png` — A clean schematic diagram showing a simplified data center cross-section with labeled BC types: CRAC (inlet T + flowrate, outlet = return air), Server (inlet velocity, outlet heat), Raised Floor (porous jump), Walls (no-slip adiabatic).

**Code snippet:** Server outlet temperature BC (simplified):
```
Server_1_outlet
{
    type            fixedValue;
    value           uniform 308.15;  // 35°C exhaust
}
```

### 4. Automatic Mesh Refinement Strategy (~600 words)

**Content breakdown:**

- **4.1 Two-stage mesh pipeline** — blockMeshDict defines the base hexahedral mesh; snappyHexMeshDict handles surface-conforming refinement and layer addition. The base mesh cell size (e.g., 0.34m) is globally set, then local refinement levels (2^n subdivision) are applied per-surface.

- **4.2 Thin wall problem** — Partition walls (D=0.05m) thinner than 2*h_local cause snappyHexMesh to trap orphan cells inside the wall body, creating disconnected mesh regions. DCTwin's preflight check: compute minimum refinement level L such that baseSize/2^L ≤ D/2, warn the user if current level is insufficient. The frontend auto-detect button sets the safe level.

- **4.3 Multi-region diagnosis** — After meshing, checkMesh may report "Number of regions: N". This is NOT always an error: cold/hot aisle containment creates legitimate separate fluid domains. Only regions with 1-3 cells (orphan fragments) should be removed. DCTwin's `_remove_disconnected_cells()` uses `oneInternalFaceCells` set + subsetMesh to clean only true orphans.

**Images:**
- GENERATED: `mesh_refinement_comparison.png` — Diagram showing: (left) insufficient refinement level on thin wall → orphan cell trapped inside, (right) adequate refinement → clean mesh. Can be a simplified 2D cross-section sketch.

**Code snippet:** snappyHexMesh refinement entry:
```
merged_partition_wall.stl
{
    level (1 1);  // too coarse for D=0.05m wall
    // DCTwin warns: recommended level >= 3
}
```

### 5. Steady-State Convergence Criteria (~400 words)

**Content breakdown:**

- **5.1 Residual convergence is necessary but not sufficient** — SIMPLE residuals dropping to 1e-4 does not guarantee physical convergence. Temperature fields can still be evolving even when residuals plateau.

- **5.2 Sensor temperature monitoring** — Place virtual sensors at critical locations (hot aisle, cold aisle, ACU return). Track temperature over iterations. Physical convergence = sensor temperatures stable within ±0.1°C over 500+ iterations.

- **5.3 DCTwin's dual-criteria approach** — Real-time residual curves + sensor temperature curves in the browser UI. The Slab case demonstrates: fixed supply temperature converges at ~3000-5000 SIMPLE steps with sensors stabilizing at 31-32°C (hot zone) and 20°C (cold zone).

**Images:**
- REUSE: `sensor_temperature_curve.png` (the 10000-step convergence plot)
- REUSE: `PostProcessing.png` (DCTwin's in-browser post-processing showing temperature field)

### 6. Validation (~300 words)

**Content:** Brief validation against experimental data, reusing the existing comparison plots from the DCTwin open-source article. DCTwin/OpenFOAM achieves MAE of 1.19°C (Coarse, 26 sensors) and 2.56°C (Fine, 80 sensors), compared to 6SigmaET's 0.70°C and 1.98°C respectively.

**Images:**
- REUSE: `comparison_plot_coarse.png` (copy from `0pythonFigure/comparison_plot.png`)
- REUSE: `comparison_plot_fine.png` (copy from `FigureFine/comparison_plot.png`)

### 7. Conclusion (~200 words)

**Content:** Summary of the three key practices (CRAC BC, mesh refinement, convergence criteria). Brief mention of DCTwin as the software platform where these practices are implemented. Mention AI Agent-driven automation (ChatTwin/ChatDC) and FVGN-based fast inference as future directions.

**Images:** None

---

## Images to Generate (Python scripts)

| # | Filename | Description | Method |
|---|----------|-------------|--------|
| 1 | `convergence_comparison.png` | Expression BC (diverging) vs Fixed BC (converging) sensor temperatures | matplotlib, using Slab case log data; if expression BC data not available, generate synthetic curve based on documented behavior (drift to 44°C at 10000 steps) |
| 2 | `blackbox_schematic.png` | Data center cross-section showing CRAC, server, raised floor with labeled BC types | matplotlib patches/arrows, clean engineering diagram style |
| 3 | `mesh_refinement_comparison.png` | Thin wall mesh: insufficient vs adequate refinement level | matplotlib, simplified 2D grid illustration |

## Images to Reuse (copy to png/)

| Source | Target filename |
|--------|----------------|
| `dctwinOpensource/Geo.png` | `dctwin_geo.png` |
| `dctwinOpensource/PostProcessing.png` | `dctwin_postprocessing.png` |
| `dctwinOpensource/0pythonFigure/comparison_plot.png` | `validation_coarse.png` |
| `dctwinOpensource/FigureFine/comparison_plot.png` | `validation_fine.png` |
| `case/Slab/log/2026-04-24-01-47-20_Slab/sensor_temperature_curve.png` | `slab_convergence.png` |

---

## References

1. CoolSim, "WP105: CRAC Thermal Boundary Condition Options in CoolSim," 2010.
2. Lu H., Zhang J., et al., "A cosimulation strategy based on OPENFOAM for solving thermal distribution and airflow in data center," Proc. IBPSA Building Simulation 2023, DOI: 10.26868/25222708.2023.1197.
3. Wibron E., Ljung A.-L., Lundstrom T. S., "Computational Fluid Dynamics Modeling and Validating Experiments of Airflow in a Data Center," Energies, 11(3), 644, 2018.
4. SimScale, "Validation Case: Airflow in a Data Center," SimScale Documentation.
5. Li T., et al., "ChatTwin," ACM BuildSys 2023, DOI: 10.1145/3600100.3623719.
6. Li T., et al., "ChatDC," ACM Trans. Internet of Things, 2026, DOI: 10.1145/3772078.

---

## Constraints

- Language: Chinese (Simplified)
- Target length: ~3500 words total
- Code snippets: 2-3 places only, kept short (5-8 lines each)
- Image style: clean, professional, consistent color scheme
- Output: `docs/dctwinWechatPaper/article.md` + `docs/dctwinWechatPaper/png/*.png`
