# AIFLUIDs 2026 Paper Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a complete ~6-page paper submission for AIFLUIDs 2026 (deadline May 15) comparing schema-constrained LLM generation vs direct LLM-to-OpenFOAM for data-center CFD case creation.

**Architecture:** Three experimental methods (A=full system, B=direct LLM-to-OF, C=no-validation) evaluated on three cases (S=SingleRack, M=Shinaide, L=GTC) using semantic accuracy, executability, and physical fidelity metrics. Results compiled into tables, paper written in Word using AIFLUIDs template.

**Tech Stack:** Python 3.10 (conda env `dcdtuni`), Pydantic v2, OpenFOAM (buoyantBoussinesqSimpleFoam), Three.js GUI, Claude Code / GPT-4o as LLM agent, Word (AIFLUIDs template)

**Spec:** `docs/manuscript/2026-05-04-aifluids-paper-design.md`

---

## File Structure

```
docs/manuscript/
├── 2026-05-04-aifluids-paper-design.md    # Spec (exists)
├── Ref_template/                           # AIFLUIDs Word templates (exists)
├── input_docs/                             # Input documents for experiments
│   ├── case_S/                             # SingleRack input docs
│   │   └── description.md                  # Text description
│   ├── case_M/                             # Shinaide input docs
│   │   ├── description.md                  # Text description
│   │   ├── floor_plan.png                  # CAD screenshot
│   │   └── power_table.xlsx                # Excel power table
│   └── case_L/                             # GTC input docs
│       ├── description.md                  # Text description
│       ├── floor_plan.png                  # CAD screenshot
│       ├── power_table.xlsx                # Excel power table
│       └── equipment_list.md               # Equipment schedule
├── experiments/                            # Experiment outputs
│   ├── method_A/                           # Schema-constrained (ours)
│   │   ├── case_S/  trial_{1,2,3}/        # 3 trials per case
│   │   ├── case_M/  trial_{1,2,3}/
│   │   └── case_L/  trial_{1,2,3}/
│   ├── method_B/                           # Direct LLM-to-OF
│   │   ├── case_S/  trial_{1,2,3}/
│   │   ├── case_M/  trial_{1,2,3}/
│   │   └── case_L/  trial_{1,2,3}/
│   └── method_C/                           # No-validation
│       ├── case_S/  trial_{1,2,3}/
│       ├── case_M/  trial_{1,2,3}/
│       └── case_L/  trial_{1,2,3}/
├── eval/                                   # Evaluation scripts
│   ├── compare_cfdproj.py                  # Semantic accuracy: generated vs ground truth
│   ├── check_executability.py              # Mesh + solver success checker
│   ├── compare_fields.py                   # Physical fidelity: T field comparison
│   └── compile_tables.py                   # Aggregate results into LaTeX/Markdown tables
├── results/                                # Compiled results
│   ├── table1_semantic_accuracy.md
│   ├── table2_executability.md
│   ├── table3_physical_fidelity.md
│   └── error_gallery/                      # GUI screenshots
└── paper/                                  # Final paper
    └── DataCenterCFD-IR_AIFLUIDs2026.docx
```

Ground truth cfdproj files (already exist):
- `STLGeneratorForOpenFoam/projects/SingleRack.cfdproj`
- `STLGeneratorForOpenFoam/projects/Shinaide.cfdproj`
- `STLGeneratorForOpenFoam/projects/GTC_2025.cfdproj`

---

### Task 1: Create Experiment Directory Structure

**Files:**
- Create: `docs/manuscript/input_docs/`, `docs/manuscript/experiments/`, `docs/manuscript/eval/`, `docs/manuscript/results/`

- [ ] **Step 1: Create all directories**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni
mkdir -p docs/manuscript/input_docs/{case_S,case_M,case_L}
mkdir -p docs/manuscript/experiments/method_{A,B,C}/{case_S,case_M,case_L}/{trial_1,trial_2,trial_3}
mkdir -p docs/manuscript/eval
mkdir -p docs/manuscript/results/error_gallery
mkdir -p docs/manuscript/paper
```

- [ ] **Step 2: Verify structure**

```bash
find docs/manuscript/experiments/ -type d | head -20
```

Expected: 27 trial directories (3 methods × 3 cases × 3 trials)

- [ ] **Step 3: Commit**

```bash
git add docs/manuscript/input_docs docs/manuscript/experiments docs/manuscript/eval docs/manuscript/results docs/manuscript/paper
git commit -m "scaffold: create experiment directory structure for AIFLUIDs paper"
```

---

### Task 2: Prepare Input Documents for Case S (SingleRack)

**Files:**
- Create: `docs/manuscript/input_docs/case_S/description.md`
- Read: `STLGeneratorForOpenFoam/projects/SingleRack.cfdproj` (ground truth, do NOT give to LLM in method A/B/C)

The input document must describe the data center in natural language as an engineer would, without revealing the cfdproj structure. Extract the key parameters from the ground truth and write a realistic engineering description.

- [ ] **Step 1: Read ground truth cfdproj and extract key parameters**

```bash
conda run -n dcdtuni python3 -c "
import json
d = json.load(open('STLGeneratorForOpenFoam/projects/SingleRack.cfdproj'))
room = d['cfdRoom']
poly = d.get('roomPolygon', {})
models = d['models']

print('=== Room ===')
print(f'Height: {room[\"height\"]}m')
print(f'Polygon vertices: {poly.get(\"vertices\", [])}')
print(f'Room temp: {room.get(\"roomTemperature\", 22)}C')

for m in models:
    if m['kind'] == 'rack':
        r = m['rack']
        print(f'\\n=== Rack: {m[\"name\"]} ===')
        print(f'Anchor: {m[\"anchor\"]}')
        print(f'Base: {m[\"base\"]}')
        print(f'Units: {r[\"unitCount\"]}')
        print(f'Equipments: {len(r[\"equipments\"])}')
        for eq in r['equipments']:
            print(f'  {eq[\"name\"]}: kind={eq[\"kind\"]}, startUnit={eq[\"startUnit\"]}, spanUnits={eq[\"spanUnits\"]}')
            phys = m['cfd'].get('servers', [])
            for s in phys:
                print(f'    server physics: power={s.get(\"physics\",{}).get(\"power\")}, flow={s.get(\"physics\",{}).get(\"volumeFlowRate\")}')
    elif m['kind'] == 'floor':
        print(f'\\n=== Floor: {m[\"name\"]} kind={m[\"kind\"]} ===')
        print(f'Anchor: {m[\"anchor\"]}, Base: {m[\"base\"]}')
    elif m['kind'] == 'wall':
        print(f'Wall: {m[\"name\"]} anchor={m[\"anchor\"]} base={m[\"base\"]}')
"
```

- [ ] **Step 2: Write natural-language input document**

Create `docs/manuscript/input_docs/case_S/description.md` based on the extracted parameters. Write it as an engineer would describe the setup to a colleague — room dimensions, rack specs, server power, no JSON structure hints. Example structure:

```markdown
# SingleRack Test Case — Engineering Description

## Room
- Rectangular room, 2m × 2m floor, 3m ceiling height
- Ambient temperature: 22°C

## Equipment
- 1 server rack (42U standard)
  - Position: centered in room at (x, y, z) = ...
  - Orientation: front facing ...
  - Equipment: [describe what's installed, power, airflow]

## Cooling
- No dedicated ACU (natural convection / open boundaries)

## Notes
- Simple validation case, single rack in enclosed room
```

- [ ] **Step 3: Commit**

```bash
git add docs/manuscript/input_docs/case_S/
git commit -m "docs: add Case S (SingleRack) input document for experiments"
```

---

### Task 3: Prepare Input Documents for Case M (Shinaide)

**Files:**
- Create: `docs/manuscript/input_docs/case_M/description.md`
- Create: `docs/manuscript/input_docs/case_M/power_table.csv` (extracted from cfdproj)
- Read: `STLGeneratorForOpenFoam/projects/Shinaide.cfdproj` (ground truth)

Same approach as Task 2 but more complex: 14 racks, 4 ACUs, 3 baffles.

- [ ] **Step 1: Extract all rack positions, server configs, ACU parameters from Shinaide.cfdproj**

```bash
conda run -n dcdtuni python3 -c "
import json, csv, sys
d = json.load(open('STLGeneratorForOpenFoam/projects/Shinaide.cfdproj'))
models = d['models']

print('=== Room ===')
print(json.dumps(d['cfdRoom'], indent=2)[:500])
print('Polygon:', json.dumps(d.get('roomPolygon'), indent=2)[:300])

racks = [m for m in models if m['kind'] == 'rack']
acus = [m for m in models if m['kind'] == 'acu']
baffles = [m for m in models if m['kind'] == 'baffle']
floors = [m for m in models if m['kind'] == 'floor']

print(f'\n=== {len(racks)} Racks ===')
for r in racks:
    equips = r['rack']['equipments']
    servers = r['cfd'].get('servers', [])
    total_power = sum(s.get('physics',{}).get('power', 0) for s in servers)
    print(f'{r[\"name\"]}: pos=({r[\"anchor\"][\"x\"]:.2f},{r[\"anchor\"][\"y\"]:.2f},{r[\"anchor\"][\"z\"]:.2f}) base={r[\"base\"]} equips={len(equips)} servers={len(servers)} totalPower={total_power}W')

print(f'\n=== {len(acus)} ACUs ===')
for a in acus:
    print(f'{a[\"name\"]}: pos=({a[\"anchor\"][\"x\"]:.2f},{a[\"anchor\"][\"y\"]:.2f},{a[\"anchor\"][\"z\"]:.2f}) base={a[\"base\"]}')

print(f'\n=== {len(baffles)} Baffles ===')
for b in baffles:
    print(f'{b[\"name\"]}: pos=({b[\"anchor\"][\"x\"]:.2f},{b[\"anchor\"][\"y\"]:.2f},{b[\"anchor\"][\"z\"]:.2f}) base={b[\"base\"]}')

print(f'\n=== {len(floors)} Floors ===')
for f in floors:
    print(f'{f[\"name\"]}: pos=({f[\"anchor\"][\"x\"]:.2f},{f[\"anchor\"][\"y\"]:.2f},{f[\"anchor\"][\"z\"]:.2f}) base={f[\"base\"]}')
"
```

- [ ] **Step 2: Create power_table.csv**

Extract per-rack power data into a CSV that mimics a real engineering Excel export:

```python
# Script to generate power_table.csv from ground truth
import json, csv
d = json.load(open('STLGeneratorForOpenFoam/projects/Shinaide.cfdproj'))
with open('docs/manuscript/input_docs/case_M/power_table.csv', 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['Rack Name', 'Total Power (W)', 'Server Count', 'Per-Server Power (W)'])
    for m in d['models']:
        if m['kind'] != 'rack':
            continue
        servers = m['cfd'].get('servers', [])
        powers = [s.get('physics',{}).get('power', 0) for s in servers]
        total = sum(powers)
        avg = total / len(powers) if powers else 0
        w.writerow([m['name'], total, len(servers), f'{avg:.0f}'])
```

- [ ] **Step 3: Write description.md with CAD-like layout description**

Write `docs/manuscript/input_docs/case_M/description.md` describing the Shinaide data center layout: room shape, rack rows, ACU positions, baffle locations, cooling strategy. Include reference to power_table.csv.

- [ ] **Step 4: Optionally capture a floor-plan screenshot from the GUI**

If the STLGenerator server is running, load Shinaide.cfdproj in browser and screenshot the top-down view → save as `docs/manuscript/input_docs/case_M/floor_plan.png`. If not possible, skip — the text + CSV is sufficient for experiments.

- [ ] **Step 5: Commit**

```bash
git add docs/manuscript/input_docs/case_M/
git commit -m "docs: add Case M (Shinaide) input documents for experiments"
```

---

### Task 4: Prepare Input Documents for Case L (GTC)

**Files:**
- Create: `docs/manuscript/input_docs/case_L/description.md`
- Create: `docs/manuscript/input_docs/case_L/power_table.csv`
- Create: `docs/manuscript/input_docs/case_L/equipment_list.md`
- Read: `STLGeneratorForOpenFoam/projects/GTC_2025.cfdproj` (ground truth)

Same approach as Tasks 2-3 but largest case: 50 racks, 6 ACUs, 10 baffles.

- [ ] **Step 1: Extract all parameters from GTC_2025.cfdproj**

Same script pattern as Task 3 Step 1, adapted for GTC_2025.cfdproj.

- [ ] **Step 2: Create power_table.csv**

Same pattern as Task 3 Step 2, for GTC_2025.cfdproj.

- [ ] **Step 3: Create equipment_list.md**

A structured equipment schedule listing all 50 racks with their row/column identifiers, dimensions, U-slot configuration, and referencing the power table. Also list ACUs with supply temperature and flow rates.

- [ ] **Step 4: Write description.md**

Describe the GTC data center: room geometry, hot-aisle/cold-aisle layout, rack rows, ACU placement, baffle positions, raised floor (if present). Reference power_table.csv and equipment_list.md.

- [ ] **Step 5: Commit**

```bash
git add docs/manuscript/input_docs/case_L/
git commit -m "docs: add Case L (GTC) input documents for experiments"
```

---

### Task 5: Build Semantic Accuracy Evaluation Script

**Files:**
- Create: `docs/manuscript/eval/compare_cfdproj.py`
- Read: `src/backend/aircooling/runner/cfdproj_schema.py` (for understanding the schema structure)

This script compares a generated .cfdproj against the ground-truth .cfdproj and computes all Table 1 metrics.

- [ ] **Step 1: Write the comparison script**

```python
#!/usr/bin/env python3
"""Compare generated .cfdproj against ground truth for semantic accuracy metrics.

Usage:
    python compare_cfdproj.py --generated path/to/generated.cfdproj \
                              --ground-truth path/to/ground_truth.cfdproj \
                              [--output results.json]

Outputs a JSON dict with:
  rack_count_accuracy, rack_position_rmse, rack_dimension_error,
  server_count_accuracy, uslot_mapping_accuracy, server_power_mae,
  acu_count_accuracy, boundary_patch_type_accuracy, schema_valid
"""
import argparse
import json
import math
import sys
from pathlib import Path


def load_cfdproj(path: str) -> dict:
    return json.loads(Path(path).read_text())


def extract_racks(proj: dict) -> dict:
    """Return {name: {anchor, base, equipments, servers}} for all rack models."""
    racks = {}
    for m in proj.get('models', []):
        if m.get('kind') != 'rack':
            continue
        name = m['name']
        racks[name] = {
            'anchor': m['anchor'],
            'base': m['base'],
            'equipments': m.get('rack', {}).get('equipments', []),
            'servers': m.get('cfd', {}).get('servers', []),
            'unitCount': m.get('rack', {}).get('unitCount', 42),
        }
    return racks


def extract_acus(proj: dict) -> dict:
    acus = {}
    for m in proj.get('models', []):
        if m.get('kind') != 'acu':
            continue
        acus[m['name']] = {
            'anchor': m['anchor'],
            'base': m['base'],
        }
    return acus


def euclidean(a: dict, b: dict) -> float:
    return math.sqrt(
        (a.get('x', 0) - b.get('x', 0)) ** 2 +
        (a.get('y', 0) - b.get('y', 0)) ** 2 +
        (a.get('z', 0) - b.get('z', 0)) ** 2
    )


def compare(gen_path: str, gt_path: str) -> dict:
    gen = load_cfdproj(gen_path)
    gt = load_cfdproj(gt_path)

    gen_racks = extract_racks(gen)
    gt_racks = extract_racks(gt)
    gen_acus = extract_acus(gen)
    gt_acus = extract_acus(gt)

    results = {}

    # Rack count accuracy
    results['rack_count_gen'] = len(gen_racks)
    results['rack_count_gt'] = len(gt_racks)
    results['rack_count_accuracy'] = 1.0 if len(gen_racks) == len(gt_racks) else len(gen_racks) / max(len(gt_racks), 1)

    # Match racks by name (best-effort)
    matched_racks = set(gen_racks.keys()) & set(gt_racks.keys())
    results['rack_matched'] = len(matched_racks)

    # Rack position RMSE
    if matched_racks:
        pos_errors = [euclidean(gen_racks[n]['anchor'], gt_racks[n]['anchor']) for n in matched_racks]
        results['rack_position_rmse'] = math.sqrt(sum(e**2 for e in pos_errors) / len(pos_errors))
    else:
        results['rack_position_rmse'] = float('inf')

    # Rack dimension error (avg absolute error in W, D, H)
    if matched_racks:
        dim_errors = []
        for n in matched_racks:
            gb = gen_racks[n]['base']
            tb = gt_racks[n]['base']
            for dim in ['W', 'D', 'H']:
                dim_errors.append(abs(gb.get(dim, 0) - tb.get(dim, 0)))
        results['rack_dimension_mae'] = sum(dim_errors) / len(dim_errors) if dim_errors else 0
    else:
        results['rack_dimension_mae'] = float('inf')

    # Server count accuracy
    gen_server_count = sum(len(r['servers']) for r in gen_racks.values())
    gt_server_count = sum(len(r['servers']) for r in gt_racks.values())
    results['server_count_gen'] = gen_server_count
    results['server_count_gt'] = gt_server_count
    results['server_count_accuracy'] = 1.0 if gen_server_count == gt_server_count else min(gen_server_count, gt_server_count) / max(gt_server_count, 1)

    # Server power MAE (across matched racks)
    power_errors = []
    uslot_correct = 0
    uslot_total = 0
    for n in matched_racks:
        gen_servers = gen_racks[n]['servers']
        gt_servers = gt_racks[n]['servers']
        for i, gs in enumerate(gt_servers):
            uslot_total += 1
            if i < len(gen_servers):
                gp = gen_servers[i].get('physics', {}).get('power', 0)
                tp = gs.get('physics', {}).get('power', 0)
                power_errors.append(abs(gp - tp))
                # U-slot mapping: check startSlot match
                if gen_servers[i].get('startSlot') == gs.get('startSlot') and \
                   gen_servers[i].get('spanSlots', gen_servers[i].get('spanUnits', 1)) == gs.get('spanSlots', gs.get('spanUnits', 1)):
                    uslot_correct += 1

    results['server_power_mae'] = sum(power_errors) / len(power_errors) if power_errors else 0
    results['uslot_mapping_accuracy'] = uslot_correct / uslot_total if uslot_total > 0 else 1.0

    # ACU count accuracy
    results['acu_count_gen'] = len(gen_acus)
    results['acu_count_gt'] = len(gt_acus)
    results['acu_count_accuracy'] = 1.0 if len(gen_acus) == len(gt_acus) else min(len(gen_acus), len(gt_acus)) / max(len(gt_acus), 1)

    # Schema validity (try Pydantic validation on the .cfdproj models list)
    # Note: cfdproj is the GUI format, not the cfdrun format.
    # Schema validity here means: does the JSON have the required top-level keys?
    required_keys = {'models', 'cfdRoom', 'roomPolygon', 'schemaVersion'}
    present = set(gen.keys())
    results['schema_valid'] = required_keys.issubset(present)

    return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--generated', required=True)
    parser.add_argument('--ground-truth', required=True)
    parser.add_argument('--output', default=None)
    args = parser.parse_args()

    results = compare(args.generated, args.ground_truth)
    out = json.dumps(results, indent=2)
    print(out)
    if args.output:
        Path(args.output).write_text(out)
```

- [ ] **Step 2: Test the script against ground truth vs itself (sanity check — should give perfect scores)**

```bash
conda run -n dcdtuni python3 docs/manuscript/eval/compare_cfdproj.py \
    --generated STLGeneratorForOpenFoam/projects/SingleRack.cfdproj \
    --ground-truth STLGeneratorForOpenFoam/projects/SingleRack.cfdproj
```

Expected: all accuracy = 1.0, all errors = 0, schema_valid = true

- [ ] **Step 3: Commit**

```bash
git add docs/manuscript/eval/compare_cfdproj.py
git commit -m "eval: add semantic accuracy comparison script for cfdproj"
```

---

### Task 6: Build Results Compilation Script

**Files:**
- Create: `docs/manuscript/eval/compile_tables.py`

This script walks all trial directories, runs compare_cfdproj.py on each, and aggregates into mean ± std tables.

- [ ] **Step 1: Write compile_tables.py**

```python
#!/usr/bin/env python3
"""Compile experiment results into summary tables.

Usage:
    python compile_tables.py --experiments-dir docs/manuscript/experiments/ \
                             --ground-truth-dir STLGeneratorForOpenFoam/projects/ \
                             --output-dir docs/manuscript/results/

Reads all trial directories, runs comparison, outputs markdown tables.
"""
import argparse
import json
import statistics
from pathlib import Path

CASE_GT_MAP = {
    'case_S': 'SingleRack.cfdproj',
    'case_M': 'Shinaide.cfdproj',
    'case_L': 'GTC_2025.cfdproj',
}

METHODS = ['method_A', 'method_B', 'method_C']
CASES = ['case_S', 'case_M', 'case_L']

SEMANTIC_METRICS = [
    'rack_count_accuracy',
    'rack_position_rmse',
    'rack_dimension_mae',
    'server_count_accuracy',
    'uslot_mapping_accuracy',
    'server_power_mae',
    'acu_count_accuracy',
    'schema_valid',
]


def find_generated_cfdproj(trial_dir: Path) -> Path | None:
    """Find the generated .cfdproj in a trial directory."""
    candidates = list(trial_dir.glob('*.cfdproj'))
    if candidates:
        return candidates[0]
    return None


def aggregate(values: list[float]) -> str:
    if not values:
        return '—'
    mean = statistics.mean(values)
    if len(values) > 1:
        std = statistics.stdev(values)
        return f'{mean:.3f} +/- {std:.3f}'
    return f'{mean:.3f}'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--experiments-dir', default='docs/manuscript/experiments/')
    parser.add_argument('--ground-truth-dir', default='STLGeneratorForOpenFoam/projects/')
    parser.add_argument('--output-dir', default='docs/manuscript/results/')
    args = parser.parse_args()

    exp_dir = Path(args.experiments_dir)
    gt_dir = Path(args.ground_truth_dir)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Import compare function
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from compare_cfdproj import compare

    # Collect results
    all_results = {}  # {method: {case: [results_dict, ...]}}
    for method in METHODS:
        all_results[method] = {}
        for case in CASES:
            all_results[method][case] = []
            gt_path = gt_dir / CASE_GT_MAP[case]
            for trial in sorted((exp_dir / method / case).glob('trial_*')):
                gen = find_generated_cfdproj(trial)
                if gen is None:
                    continue
                try:
                    r = compare(str(gen), str(gt_path))
                    all_results[method][case].append(r)
                except Exception as e:
                    all_results[method][case].append({'error': str(e)})

    # Build Table 1: Semantic Accuracy
    lines = ['# Table 1: Engineering Semantic Accuracy\n']
    lines.append('| Metric | Case S | | | Case M | | | Case L | | |')
    lines.append('|---|---|---|---|---|---|---|---|---|---|')
    lines.append('| | A | B | C | A | B | C | A | B | C |')

    for metric in SEMANTIC_METRICS:
        row = [f'| {metric}']
        for case in CASES:
            for method in METHODS:
                trials = all_results[method][case]
                vals = [t.get(metric) for t in trials if metric in t and t[metric] is not None]
                # Convert booleans
                if vals and isinstance(vals[0], bool):
                    row.append(f'{sum(vals)}/{len(vals)}')
                elif vals:
                    row.append(aggregate([float(v) for v in vals]))
                else:
                    row.append('—')
        row.append('|')
        lines.append(' | '.join(row))

    table1 = '\n'.join(lines)
    (out_dir / 'table1_semantic_accuracy.md').write_text(table1)
    print(table1)


if __name__ == '__main__':
    main()
```

- [ ] **Step 2: Commit**

```bash
git add docs/manuscript/eval/compile_tables.py
git commit -m "eval: add results compilation script"
```

---

### Task 7: Run Method A Experiments (Schema-Constrained — Our System)

**Files:**
- Write to: `docs/manuscript/experiments/method_A/case_{S,M,L}/trial_{1,2,3}/`
- Read: `docs/manuscript/input_docs/case_{S,M,L}/`

For each case × trial, use Claude Code (or the user's preferred LLM agent) to generate a .cfdproj from the input documents. The LLM is given:
1. The input documents (description.md, power_table.csv, etc.)
2. The Pydantic schema definitions from `src/backend/aircooling/runner/cfdproj_schema.py` as reference
3. An example of a minimal cfdproj structure (NOT the ground truth for that case)
4. Instructions: "Generate a .cfdproj file for this data center"

- [ ] **Step 1: Create a prompt template for Method A**

Create `docs/manuscript/eval/prompt_method_A.md`:

```markdown
# Method A Prompt Template

You are a data-center CFD engineer. Given the following engineering documents,
generate a .cfdproj JSON file that describes this data center for CFD simulation.

The .cfdproj must conform to the following structure:
- Top-level keys: models (array), cfdRoom (object), roomPolygon (object),
  schemaVersion (integer), inputUnit (string)
- Each model has: id, kind, name, anchor, base, and kind-specific fields
- Rack models have: rack.equipments[], cfd.servers[]
- ACU models have: cfd.supplyFaces[], cfd.returnFaces[]

[Paste Pydantic schema excerpt here for reference]

## Input Documents:
[Paste case-specific input documents here]

## Output:
Generate the complete .cfdproj JSON.
```

- [ ] **Step 2: Run 3 trials for Case S**

For each trial, start a fresh LLM conversation:
1. Provide the prompt template + Case S description.md
2. Let LLM generate .cfdproj
3. Save output to `docs/manuscript/experiments/method_A/case_S/trial_N/generated.cfdproj`
4. Record: conversation length, token count, time taken, any validation errors

- [ ] **Step 3: Run 3 trials for Case M**

Same as Step 2 but with Case M documents (description.md + power_table.csv).

- [ ] **Step 4: Run 3 trials for Case L**

Same as Step 2 but with Case L documents (description.md + power_table.csv + equipment_list.md).

- [ ] **Step 5: Run semantic accuracy evaluation on all Method A trials**

```bash
for case in case_S case_M case_L; do
    for trial in trial_1 trial_2 trial_3; do
        echo "=== Method A / $case / $trial ==="
        conda run -n dcdtuni python3 docs/manuscript/eval/compare_cfdproj.py \
            --generated "docs/manuscript/experiments/method_A/$case/$trial/generated.cfdproj" \
            --ground-truth "STLGeneratorForOpenFoam/projects/$(python3 -c "m={'case_S':'SingleRack','case_M':'Shinaide','case_L':'GTC_2025'}; print(m['$case'])"').cfdproj" \
            --output "docs/manuscript/experiments/method_A/$case/$trial/semantic_results.json"
    done
done
```

- [ ] **Step 6: Commit all Method A results**

```bash
git add docs/manuscript/experiments/method_A/
git commit -m "exp: Method A (schema-constrained) results for all cases"
```

---

### Task 8: Run Method B Experiments (Direct LLM-to-OpenFOAM)

**Files:**
- Write to: `docs/manuscript/experiments/method_B/case_{S,M,L}/trial_{1,2,3}/`

For each case × trial, use the same LLM to directly generate OpenFOAM case files. The LLM is given:
1. The same input documents as Method A
2. Instructions: "Generate a complete OpenFOAM case for buoyantBoussinesqSimpleFoam"
3. NO schema, NO cfdproj structure, NO GUI

The LLM must produce: blockMeshDict, snappyHexMeshDict, boundary files (0/T, 0/U, 0/p_rgh etc.), and a Python script for STL generation.

- [ ] **Step 1: Create prompt template for Method B**

Create `docs/manuscript/eval/prompt_method_B.md`:

```markdown
# Method B Prompt Template

You are a data-center CFD engineer using OpenFOAM. Given the following engineering
documents, generate a complete OpenFOAM case for buoyantBoussinesqSimpleFoam.

You must produce:
1. A Python script (generate_stl.py) that creates multi-solid ASCII STL files
   for all geometry (room walls, racks, ACUs, baffles, floors)
2. system/blockMeshDict
3. system/snappyHexMeshDict
4. system/controlDict
5. system/fvSchemes
6. system/fvSolution
7. 0/T, 0/U, 0/p_rgh, 0/k, 0/epsilon with correct boundary conditions
8. constant/transportProperties, constant/turbulenceProperties

## Input Documents:
[Paste case-specific input documents here]

## Output:
Generate all files listed above.
```

- [ ] **Step 2: Run 3 trials for each case (S, M, L)**

For each trial:
1. Fresh LLM conversation with Method B prompt + case documents
2. Save all generated files to the trial directory
3. Record conversation length, token count, time

- [ ] **Step 3: Evaluate — for semantic accuracy, extract rack/server info from generated OF files**

For Method B, semantic accuracy is harder to measure since there's no cfdproj. Create a lightweight parser that extracts rack count from STL solid names and power values from boundary condition files. Save a compatible results JSON.

- [ ] **Step 4: Evaluate — try to run the generated cases**

```bash
for case in case_S case_M case_L; do
    for trial in trial_1 trial_2 trial_3; do
        dir="docs/manuscript/experiments/method_B/$case/$trial"
        echo "=== Method B / $case / $trial ==="
        # Try to run the STL generation script
        cd "$dir" && conda run -n dcdtuni python3 generate_stl.py 2>&1 | tail -5
        # Try blockMesh
        blockMesh 2>&1 | tail -3
        # Try snappyHexMesh
        snappyHexMesh -overwrite 2>&1 | tail -3
        cd -
    done
done
```

Record success/failure for each step.

- [ ] **Step 5: Commit**

```bash
git add docs/manuscript/experiments/method_B/
git commit -m "exp: Method B (direct LLM-to-OF) results for all cases"
```

---

### Task 9: Run Method C Experiments (No Validation)

**Files:**
- Write to: `docs/manuscript/experiments/method_C/case_{S,M,L}/trial_{1,2,3}/`

Same as Method A but WITHOUT providing the Pydantic schema to the LLM and WITHOUT running validation on the output. The LLM is told to generate a "JSON project file" with a vague structure description.

- [ ] **Step 1: Create prompt template for Method C**

Create `docs/manuscript/eval/prompt_method_C.md` — same as Method A but remove schema definitions, provide only a vague description: "Generate a JSON file with room, racks, acus, floors, walls, meshConfig, simulationConfig."

- [ ] **Step 2: Run 3 trials for each case**

Same procedure as Task 7 Steps 2-4.

- [ ] **Step 3: Try to load generated JSON through the compiler (without Pydantic validation)**

For each trial, attempt to load the JSON and see if the compiler can process it. Record which ones fail and why (missing fields, wrong types, etc.).

- [ ] **Step 4: Run semantic accuracy evaluation**

```bash
# Same evaluation loop as Task 7 Step 5, but for method_C
```

- [ ] **Step 5: Commit**

```bash
git add docs/manuscript/experiments/method_C/
git commit -m "exp: Method C (no-validation) results for all cases"
```

---

### Task 10: Run OpenFOAM and Collect Physical Fidelity Data

**Files:**
- Read: `case/SingleRack/`, `case/Shinaide/`, `case/GTC/` (manual baseline results)
- Write to: `docs/manuscript/results/table3_physical_fidelity.md`

Only for methods that produced runnable cases (likely Method A, possibly some Method B trials).

- [ ] **Step 1: Verify manual baseline cases have converged results**

```bash
for casedir in case/SingleRack case/Shinaide case/GTC; do
    echo "=== $casedir ==="
    # Check if results exist
    ls "$casedir"/Mesh/processor0/ 2>/dev/null | tail -3
    # Check latest time directory
    latest=$(ls -d "$casedir"/Mesh/processor0/[0-9]* 2>/dev/null | sort -n | tail -1)
    echo "Latest time: $latest"
done
```

If manual baselines don't have converged results, run them first using the server API or existing cfdrun pipeline.

- [ ] **Step 2: For each runnable AI-generated case, run OpenFOAM**

Load generated .cfdproj in GUI → export .cfdrun + STL → run mesh + solve via server API.

For Method A trials that pass validation:
1. Copy generated.cfdproj to STLGeneratorForOpenFoam/projects/
2. Load in GUI, export, run via server
3. Extract results

- [ ] **Step 3: Compare temperature fields**

Use postprocessing to extract rack inlet temperatures and compare:

```python
# docs/manuscript/eval/compare_fields.py — outline
# Read probe data or field slices from both AI-generated and manual baseline
# Compute MAE for rack inlet temperatures
# Compute volume-averaged temperature difference
# Output to JSON
```

- [ ] **Step 4: Compile Table 3**

```bash
conda run -n dcdtuni python3 docs/manuscript/eval/compile_tables.py \
    --experiments-dir docs/manuscript/experiments/ \
    --ground-truth-dir STLGeneratorForOpenFoam/projects/ \
    --output-dir docs/manuscript/results/
```

- [ ] **Step 5: Commit**

```bash
git add docs/manuscript/results/ docs/manuscript/eval/compare_fields.py
git commit -m "exp: physical fidelity results and field comparison"
```

---

### Task 11: Capture Error Gallery Screenshots

**Files:**
- Write to: `docs/manuscript/results/error_gallery/`

During Method A/B/C experiments, collect representative examples of LLM generation errors visible in the GUI.

- [ ] **Step 1: Identify errors from experiment results**

Review semantic accuracy results and find trials with specific error types:
- Rack orientation wrong
- Server power mismatch
- Missing equipment
- Wrong position
- Missing baffle/ACU

- [ ] **Step 2: Load erroneous cfdproj in GUI and screenshot**

For each error type:
1. Load the generated .cfdproj in STLGenerator GUI
2. Screenshot the 3D view showing the error
3. Load the ground truth .cfdproj and screenshot the correct version
4. Save as `error_gallery/error_N_{generated,corrected}.png`

- [ ] **Step 3: Annotate screenshots**

Create `docs/manuscript/results/error_gallery/captions.md` with:

```markdown
## Error Gallery Captions

### Error 1: Rack Orientation Reversed
- File: error_1_generated.png / error_1_corrected.png
- Case: M, Trial 2
- Description: Rack A03 inlet faces wall instead of cold aisle.
  AI rotated rack 180° from correct orientation.

### Error 2: Server Power Mismatch
...
```

- [ ] **Step 4: Commit**

```bash
git add docs/manuscript/results/error_gallery/
git commit -m "exp: error gallery screenshots with captions"
```

---

### Task 12: Write Paper — Introduction

**Files:**
- Create: `docs/manuscript/paper/sections/01_introduction.md`

- [ ] **Step 1: Write Introduction (~0.75 page, ~400 words)**

Structure:
1. Para 1: Data-center CFD importance (thermal management, hotspot detection, energy optimization, 30% of global electricity by 2030)
2. Para 2: Current pain — manual, error-prone process from CAD/Excel/specs to OpenFOAM case (cite typical setup times, error rates if available)
3. Para 3: Recent LLM-CFD work promising but limited — cite OpenFOAMGPT, Foam-Agent, ChatCFD as evidence of trend; cite ChatTwin/ChatDC for DC digital twin angle; note gap = no one does document-to-CFD-ready-case with schema constraint + GUI verification
4. Para 4: Our contribution — schema-constrained IR, deterministic compiler, GUI debugging loop. One sentence per contribution.

- [ ] **Step 2: Commit**

```bash
git add docs/manuscript/paper/sections/01_introduction.md
git commit -m "paper: draft Introduction section"
```

---

### Task 13: Write Paper — Related Work

**Files:**
- Create: `docs/manuscript/paper/sections/02_related_work.md`

- [ ] **Step 1: Write Related Work (~0.75 page, ~400 words)**

Three subsections:
1. **LLM for CFD automation** — OpenFOAMGPT [1], MetaOpenFOAM [4], Foam-Agent [2], ChatCFD [3], FoamGPT [12], Engineering.ai [9]. Note: all directly generate OF files; ChatCFD's physical fidelity metric (68%) highlights gap between runnable and correct.
2. **LLM for data-center digital twins** — ChatTwin [6], ChatDC [5], Fusion Intelligence [10], DCoPilot [14]. Scene-level, not CFD-ready.
3. **AI-CAD generation** — VideoCAD [8], CAD-Llama [7], Text2CAD. IR-based (SPCC, sketch sequences) but geometry-only.

Include the 2×2 positioning matrix as Fig 2.

Close with: "Our work occupies the intersection: domain-specific (data center), IR-based (schema-constrained JSON), and CFD-complete (geometry through solver configuration)."

- [ ] **Step 2: Commit**

```bash
git add docs/manuscript/paper/sections/02_related_work.md
git commit -m "paper: draft Related Work section"
```

---

### Task 14: Write Paper — Problem Definition & Method

**Files:**
- Create: `docs/manuscript/paper/sections/03_problem_definition.md`
- Create: `docs/manuscript/paper/sections/04_method.md`

- [ ] **Step 1: Write Problem Definition (~0.5 page)**

Formal statement:
- Input: set of heterogeneous engineering documents D = {d_1, ..., d_n} (CAD, Excel, text)
- Output: valid OpenFOAM case C = (STL, mesh dicts, BC files, solver config)
- Intermediate: DataCenterCFD-IR J — a JSON conforming to schema S
- Task: find mapping f: D → J such that compile(J) produces a valid, physically meaningful C
- Constraints: geometric validity, physical consistency, OpenFOAM executability, human verifiability

- [ ] **Step 2: Write Method (~1.5 pages)**

Subsections:
1. System pipeline (reference Fig 1)
2. DataCenterCFD-IR schema — key hierarchy, rack model depth, JSON snippet (Fig 3), include Table 4 (IR capability comparison: ours vs ChatTwin vs CAD-Llama vs OF dict — from spec)
3. LLM document agent — input processing, schema-as-prompt, iterative generation
4. Validation layer — Pydantic v2, cross-field validators, error feedback to LLM
5. Deterministic compiler — STL gen, mesh dicts, BC assignment, field init (brief, point to pipeline)
6. GUI verification loop — visual inspection capabilities, correction-recompile cycle

- [ ] **Step 3: Commit**

```bash
git add docs/manuscript/paper/sections/03_problem_definition.md docs/manuscript/paper/sections/04_method.md
git commit -m "paper: draft Problem Definition and Method sections"
```

---

### Task 15: Write Paper — Experiments & Results

**Files:**
- Create: `docs/manuscript/paper/sections/05_experiments.md`

- [ ] **Step 1: Write Experimental Setup (~0.5 page)**

- 3 test cases table (S/M/L with rack counts, complexity, input doc types)
- 3 methods table (A/B/C with descriptions)
- Evaluation protocol: 3 trials per method-case pair, metrics for each experiment
- Hardware/software: OpenFOAM version, LLM model, mesh/solver settings

- [ ] **Step 2: Write Results (~1 page)**

- Table 1: Semantic accuracy — populate from compiled results
- Table 2: Executability — populate from experiment logs
- Table 3: Physical fidelity (if available) — populate from field comparison
- Fig 4: Error gallery — 4 representative screenshots with captions
- Key observations paragraph: Method A achieves X% rack accuracy vs Y% for B; schema validation catches Z errors; GUI reveals N types of semantic errors before solver execution

- [ ] **Step 3: Commit**

```bash
git add docs/manuscript/paper/sections/05_experiments.md
git commit -m "paper: draft Experiments and Results section"
```

---

### Task 16: Write Paper — Conclusions & References

**Files:**
- Create: `docs/manuscript/paper/sections/06_conclusions.md`
- Create: `docs/manuscript/paper/references.bib` (or inline numbered references)

- [ ] **Step 1: Write Conclusions (~0.5 page)**

- Summary: presented DataCenterCFD-IR, schema-constrained generation, deterministic compilation, GUI verification
- Key finding: schema constraint improves semantic accuracy by X% over direct generation; compiler eliminates OF syntax errors; GUI catches Y errors before solver
- Limitations: CAD parsing quality (depends on image resolution), LLM model dependence, schema evolution for new DC equipment types, no formal user study yet
- Future work: sensor calibration, optimization loop, multi-model comparison, surrogate model integration, Computers & Fluids extended study

- [ ] **Step 2: Compile reference list**

Gather all citations in numbered format [1]-[~20], following AIFLUIDs style.

- [ ] **Step 3: Commit**

```bash
git add docs/manuscript/paper/sections/06_conclusions.md docs/manuscript/paper/references.bib
git commit -m "paper: draft Conclusions and reference list"
```

---

### Task 17: Create Figures

**Files:**
- Create: `docs/manuscript/paper/figures/fig1_pipeline.{svg,png}`
- Create: `docs/manuscript/paper/figures/fig2_positioning_matrix.{svg,png}`
- Create: `docs/manuscript/paper/figures/fig3_json_snippet.png`
- Create: `docs/manuscript/paper/figures/fig4_error_gallery.png`

- [ ] **Step 1: Create Fig 1 (pipeline diagram)**

Draw the system pipeline: Documents → LLM Agent → DataCenterCFD-IR (JSON) → Validation → Compiler → {STL, mesh, BC, solver} → GUI → OpenFOAM. Use a vector graphics tool or programmatic SVG.

- [ ] **Step 2: Create Fig 2 (2×2 positioning matrix)**

The related-work matrix from the spec. Simple 2×2 table graphic.

- [ ] **Step 3: Create Fig 3 (JSON snippet)**

Extract a minimal rack definition from SingleRack.cfdproj showing: anchor, base, rack.equipments[0], cfd.servers[0]. Format as a code listing.

- [ ] **Step 4: Compose Fig 4 (error gallery)**

Arrange 4 error screenshots (from Task 11) into a 2×2 grid with labels (a)-(d) and captions.

- [ ] **Step 5: Commit**

```bash
git add docs/manuscript/paper/figures/
git commit -m "paper: add all figures"
```

---

### Task 18: Assemble Final Paper in Word Template

**Files:**
- Read: `docs/manuscript/Ref_template/Full Manusscript_Example2026.docx.docx`
- Create: `docs/manuscript/paper/DataCenterCFD-IR_AIFLUIDs2026.docx`

- [ ] **Step 1: Copy the AIFLUIDs Word template**

```bash
cp "docs/manuscript/Ref_template/Full Manusscript_Example2026.docx.docx" \
   docs/manuscript/paper/DataCenterCFD-IR_AIFLUIDs2026.docx
```

- [ ] **Step 2: Populate the Word document**

Transfer all section text from markdown drafts into the Word template. Insert figures and tables. Apply AIFLUIDs formatting: single column, numbered references, header "Proceedings of the 2nd International Symposium on AI and Fluid Mechanics".

This step is manual (Word editing) but the markdown sections serve as complete drafts.

- [ ] **Step 3: Proofread**

Check: title, author list, abstract word count (~200), figure/table numbering, reference completeness, page count (~6).

- [ ] **Step 4: Commit final paper**

```bash
git add docs/manuscript/paper/DataCenterCFD-IR_AIFLUIDs2026.docx
git commit -m "paper: final AIFLUIDs 2026 submission"
```

---

## Critical Path & Parallelism

```
Day 1-2:  Task 1 (dirs) + Tasks 2-4 (input docs) — parallelizable
Day 2-3:  Task 5-6 (eval scripts)
Day 3-5:  Tasks 7-9 (experiments A/B/C) — A and B parallelizable, C after
Day 5-6:  Task 10 (OpenFOAM runs + physical fidelity)
Day 6-7:  Task 11 (error gallery)
Day 7-9:  Tasks 12-16 (write paper sections) — can parallelize sections
Day 9-10: Task 17 (figures)
Day 10-11: Task 18 (assemble + proofread + submit)
```

Minimum viable paper (if time runs short): Tasks 1-9 + Tasks 12-16 + Task 18 — skip Task 10 (physical fidelity) and Task 11 (error gallery), report only Tables 1-2. Still a solid 6-page paper.
