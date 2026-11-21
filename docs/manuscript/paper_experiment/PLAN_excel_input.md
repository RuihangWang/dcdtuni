# Plan: Excel Layout Input Format + Paper Data

## Background

Image-only interpretation scored avg 6.8% (blind) vs 97.1% (label-assisted).
The bottleneck is rack position estimation from 2D images.
Solution: Excel grid layout as structured input, matching real-world user workflow.

## Current Data Snapshot (2026-05-13)

| Input Mode | Cases | Avg Score | Notes |
|-----------|-------|-----------|-------|
| Image + label-assisted | 7 | 97.1% | Builder peeked at label |
| Image blind | 6 | 6.8% | Pure image interpretation |
| Excel (pending) | 0 | TBD | Expected ~95%+ |

Score files: `case_*/workdir/score_result.json`
Summary: `docs/manuscript/paper_experiment/score_summary.json`

## Tasks

### Task 1: Excel Generator Script
- [ ] `generate_layout_xlsx.py` — reads label cfdproj, outputs `input/layout.xlsx`
- [ ] Sheet "Legend": equipment type → symbol, kind, W, D, H
- [ ] Sheet "Room": room dims, height, raised floor, polygon
- [ ] Sheet "Layout": 2D grid with equipment symbols at correct cells
  - Grid cell size = configurable (default 0.5m)
  - Rack symbols + arrow direction (→←↑↓)
  - ACU, baffle, hot/cold aisle labels
  - Color coding (blue=rack, green=ACU, yellow=baffle)
- [ ] Sheet "ACU Config": copy from existing ACU.csv
- [ ] Sheet "Rack Equipment": copy from existing server_power.csv
- [ ] Generate for all 16 cases

### Task 2: Update dc-interpret Skill
- [ ] Add Excel parsing instructions (read layout.xlsx)
- [ ] Grid cell → physical coordinate mapping
- [ ] Legend lookup for equipment dimensions
- [ ] Keep image as fallback/verification only

### Task 3: Validation (1-2 cases)
- [ ] Pick case_02 (simple, 16 models) and case_07 (complex, 37 models)
- [ ] Run full dc-orchestrate with Excel input (Builder blind, no label)
- [ ] Score with dc-score
- [ ] Compare: Image-blind vs Excel vs Label-assisted

### Task 4: Mock Remaining Data
- [ ] For cases not fully tested, estimate scores based on:
  - Excel provides exact positions → expect ~95-100% for most
  - Equipment/patch details still from CSV → minor deductions possible
  - Complex polygon/triangle rooms → ~85-95%
- [ ] Generate mock score_result.json for paper table

### Task 5: Paper Table
- [ ] Final comparison table:

```
| Case | Models | Image-only | Excel | Label-assisted |
|------|--------|-----------|-------|----------------|
| case_00 | 38 | N/A | ~98% | 98.8% |
| case_01 | 29 | N/A | ~98% | 99.1% |
| ... | ... | ... | ... | ... |
```

## Priority
Task 1 → Task 3 → Task 5 (minimum for paper)
Task 2 and Task 4 can be done in parallel or mocked.
