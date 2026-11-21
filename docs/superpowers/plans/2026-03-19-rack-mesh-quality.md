# Rack Mesh Snap Quality at Geometry Junctions — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the visible mesh gap at rack geometry junctions (e.g., Top patch meeting inlet_safe_top patch) by improving snappyHexMesh snap quality and feature edge refinement.

**Architecture:** The rack STL is geometrically correct — shared edges between adjacent solids have identical vertex coordinates. The `surfaceCheck` "not closed" warning is expected (rack has intentional openings for airflow). The visual gap in ParaView is caused by snappyHexMesh's snap step not conforming perfectly to 90-degree corners at the given mesh resolution.

**Tech Stack:** OpenFOAM 2406, snappyHexMesh, Python (stlgen_runner.py)

---

## Root Cause Analysis

The rack geometry consists of ~25 planar rectangles per rack (Top, Bottom, Left, Right, safe margins, frame walls, U-slot walls, equipment faces, partitions, inner walls). Each is 2 triangles. At 90-degree corners where perpendicular faces meet (e.g., Top and inlet_safe_top), snappyHexMesh must snap mesh points to BOTH surfaces simultaneously. At the current refinement level, the cells are too large to capture these corners cleanly.

Key measurements:
- Base mesh size: 0.2m
- Rack refinement level: [2, 3] → cell size at level 3 = 0.025m
- Safe margin height: 0.04445m (~1.8 cells at level 3)
- Feature edge refinement: level 2 (from eMesh)

The feature edge refinement (level 2) is LOWER than the surface refinement (level 3). This means the mesh cells at edges are LARGER than at surfaces, causing poor corner conformity.

## Fix

### Task 1: Increase feature edge refinement for rack STL

**Files:**
- Modify: `dctwin/dctwin/runner/stlgen_runner.py`

The `features` section in snappyHexMeshDict currently uses a hardcoded `level 2` for ALL eMesh files. The rack eMesh should use a higher level to match the rack surface refinement.

- [ ] **Step 1: Read current features section generation**

Read `_generate_snappy_hex_mesh_dict` around line 660 where `feature_lines` are built.

- [ ] **Step 2: Set per-STL feature edge levels**

Change from:
```python
feature_lines.append(f"""    {{
    file "{emesh_name}";
    level 2;
}}""")
```

To:
```python
# Use rack refinement level for rack eMesh, default for others
feat_level = rl_rack[1] if filename == "merged_rack.stl" else 2
feature_lines.append(f"""    {{
    file "{emesh_name}";
    level {feat_level};
}}""")
```

This ensures rack feature edges get refined to the same level as rack surfaces.

- [ ] **Step 3: Improve snap controls in snappyHexMeshDict**

In the `snapControls` section, increase `nSmoothPatch` from 3 to 5 and `nRelaxIter` from 5 to 8 for better corner snapping:

Find the snap controls in the generated snappyHexMeshDict template string and update:
```
nSmoothPatch 5;
nRelaxIter 8;
```

- [ ] **Step 4: Commit**

```bash
git add dctwin/dctwin/runner/stlgen_runner.py
git commit -m "fix: improve rack mesh snap quality at geometry junctions"
```

### Task 2: Verify mesh quality

- [ ] **Step 1: Re-run mesh generation**

```bash
conda run -n dcdtuni python -m dctwin.runner.run_cfd \
  --from-stlgen dctwin/case/V5_CFD_Station/model/cfd_export.json --mesh-only
```

- [ ] **Step 2: Check mesh quality at rack junctions in ParaView**

Open `dctwin/case/V5_CFD_Station/Mesh/case.foam` in ParaView. Check the Top/inlet_safe_top junction — the gap should be significantly smaller or eliminated.

- [ ] **Step 3: Verify patch face counts**

```bash
grep -A3 "Default_Rack_1_Top\|Default_Rack_1_inlet_safe_top" \
  dctwin/case/V5_CFD_Station/Mesh/constant/polyMesh/boundary
```

Both patches should have reasonable face counts (not 0 or very small).
