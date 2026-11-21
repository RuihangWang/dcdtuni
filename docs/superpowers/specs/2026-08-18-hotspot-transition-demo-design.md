# Hotspot transition demo — two rendered animations from three steady states

The project page's cooling story is currently a cross-fade between two stills whose whole
difference is **−0.94 K** (`make_gifs.py:build_cooling`). That number is real but invisible,
and `docs/drag-and-sim/04-demo-headline-sensitivity.md` shows it is also 6.6× short of
physics because the surrogate's training set has no cases in [15.5, 18.5) °C. The demo
therefore needs a different source of motion.

This spec replaces that section with two rendered animations — a thermal one and a flow
one — built from **CFD steady states only**. No transient run. The requested story
("hotspot appears → airflow management → hotspot gone") has two transitions, so it needs
three anchors; the frames between anchors are interpolated fields, not solved ones, and are
labelled as such.

Audience for the output is the public project page and the slide deck, so every string that
ships is subject to the double-blind guard: no `GTC`, no repo names, no paths carrying the
host username. Inside this repo those names are fine, and are used below.

## The three anchors

The scene is `case/GTC` — 31 × 19.2 × 5 m, 50 racks in four rows (z = 3.6 / 8.4 / 10.8 /
15.6), two contained cold aisles (z ∈ [4.8, 7.2] and z ∈ [12.0, 14.4], end-capped at
x = 6 and x = 25 by 3.6 m baffles), and six ACUs on the side walls.

| ACU | wall | z | supply T (°C) | Q (m³/s) | capacity (kW) |
|---|---|---|---|---|---|
| `m_51` | left (x=3.4) | 2.4 | 22.76 | 5.110 | 61.6 |
| `m_52` | left | 7.2 | 18.97 | 6.489 | 78.3 |
| `m_53` | left | 12.2 | 19.88 | 5.208 | 62.8 |
| `m_54` | right (x=27.6) | 5.2 | 18.59 | 5.628 | 67.9 |
| `m_55` | right | 10.0 | 22.04 | 5.640 | 68.0 |
| `m_56` | right | 15.2 | 19.07 | 5.325 | 64.2 |

Total installed cooling is 402.8 kW across 50 racks.

| Anchor | Operating point | Why this one |
|---|---|---|
| **S0** normal | as shipped, all six ACUs | the "before" |
| **S1** failure | `m_51`, `m_54` and `m_52` set to zero supply flow | the first two bracket the **southern contained aisle** (z 4.8–7.2) and `m_52` feeds the same half. Starving one enclosure keeps the hotspot in a readable place instead of thinning cooling everywhere. −207.8 kW = 52% of installed capacity. Three rather than two because the animation is a feature demo on a deadline: buy the visual margin up front instead of solving, measuring, and re-solving. |
| **S2** intervention | the three survivors at Q × 1.8 and supply T → 15 °C | both levers of airflow management at once: more air, colder air |

S0 is re-solved rather than reused from `log/2026-06-29-05-50-44_GTC`, because the mesh in
`Mesh/` was regenerated on 2026-07-08 — later than that solve. Re-solving costs one run and
buys the guarantee that all three anchors sit on the same cells.

### These solves are deliberately not converged

The deliverable is an Omniverse rendering feature demo under a deadline, not a CFD result, so
the iteration budget is cut to what makes the picture read:

| Anchor | Iterations | Initial field |
|---|---|---|
| S0 | 600 | the case's own `0/` |
| S1 | 250–300 | warm-started from S0 |
| S2 | 250–300 | warm-started from S1 |

That is roughly 15 minutes for all three instead of half an hour, and it is honest only if it
is said out loud: **no residual target is enforced and no anchor is claimed as converged.**
The page copy, the deck notes and the burnt-in overlay all say the animation illustrates the
mechanism rather than reporting a validated result. Any number quoted from these fields (the
peak-rise figure in the S1 caption) carries that same qualifier.

S1's peak rise over S0 is still measured and reported before rendering starts — not as a gate
with an escalation ladder, but so the caption states a real number and so a visually dead S1
is caught in seconds rather than after an hour of recording.

## One mesh, three solves

`case/GTC/Mesh/` already holds a 0.9 M-cell `polyMesh`, a populated `0/`, and
`model/GTC_2025.cfdrun`. The server exposes `solveOnly` (`server.py:3025`,
`stlgen_runner.run_solve_only:1212`), and that path regenerates `0/` from the manifest
before solving, which is exactly the knob this needs:

```
edit .cfdproj ACU physics  →  browser export STL + cfdrun  →  POST /api/run-mesh
   pass 1 (S0):  full mesh + solve
   pass 2 (S1):  {"solveOnly": true}
   pass 3 (S2):  {"solveOnly": true}
```

Each solve lands in `case/GTC/log/<timestamp>_GTC/`. Steady `buoyantBoussinesqSimpleFoam`
on 16 ranks costs 568 s for 1000 iterations on this case, so at the reduced budgets above the
three passes come to roughly 15 minutes, plus one meshing pass if `Mesh/` has to be rebuilt.

Geometry is identical across the three variants (only patch *values* change), so re-exporting
STL does not invalidate the mesh. That is an assumption with a cheap test, so it gets one:
**hash `constant/polyMesh/points` and `owner` before and after each export**, and stop if
they move.

Anchors are edited as `.cfdproj` variants driven through the headless browser channel, not
as hand-written `.cfdrun` JSON, so the manifest is byte-for-byte what the frontend would
produce. Mesh and solve are triggered through the server API — never `conda run … run_cfd`.

## Interpolated frames

`foamToVTK -fields '(T U)'` on each anchor gives three `internal.vtu` on identical topology.
A writer then emits one `.vtu` per frame:

```
F(α) = (1 − α) · A + α · B          for T and U, per point
α    = smoothstep(t)                 ease in/out, not linear
Umag = |U| written as its own scalar array
```

`Umag` is materialised rather than left to the renderer: IndeX's `vector_magnitude` is
unreliable on this path, which is why `data/internal.vtu` already carries a real `Umag`.

Interpolation is only defensible if stated correctly. Divergence is a linear operator, so a
convex combination of two divergence-free fields is divergence-free — the intermediate
velocity field still satisfies continuity, and its streamlines are not decorative. It does
**not** satisfy momentum or energy, so the caption reads "稳态工况间过渡" while the three
anchors are labelled as solved states.

Before interpolating, assert the three meshes agree: same point count, and
`max |points_S1 − points_S0| == 0`. If they disagree, resample S1/S2 onto S0's grid with
`vtkResampleWithDataSet` rather than interpolating mismatched arrays.

## Timeline — 6 s, 31 rendered frames

| Segment | Played time | Unique renders | Caption |
|---|---|---|---|
| hold S0 | 1.0 s | 1 | 正常运行 · 6 台 ACU |
| S0 → S1 | 1.5 s | 14 | 3 台 ACU 故障 |
| hold S1 | 1.0 s | 1 | 热点形成 · 峰值 +XX K |
| S1 → S2 | 1.5 s | 14 | 干预 · 风量 ×1.8、送风 15 °C |
| hold S2 | 1.0 s | 1 | 热点消除 |

Three multipliers cut the render count from 120 played frames to **31**:

1. Held segments repeat one image; the assembler duplicates it.
2. Transitions are rendered at 12 fps.
3. The mp4 is assembled at 24 fps by blending adjacent rendered frames. Consecutive frames of
   a smooth field morph differ by very little, so a straight image blend is indistinguishable
   from a rendered in-between — and it costs milliseconds instead of 70 s.

At roughly 70 s per rendered frame that is about 36 minutes for both animations rather than
75. The `+XX K` in the S1 caption is filled from the measured peak, not written by hand.

## Recording through the Omniverse viewport

`scripts/record_transition.py`, Playwright `headless=False` on the Xvfb `:99` display with
`--disable-gpu --use-gl=swiftshader`, matching every other `head_verify_*.py`. Headless
`browser-use` renders the WebRTC viewport black and takes a GPU process — it is forbidden
here.

Per frame, one dataset load feeds **both** animations:

1. `loadDataset(frame_i.vtu)`
2. show `Temp` → settle → screenshot → `thermal/f####.png`
3. enable streamlines (fixed seed sphere, coloured by T) → settle → screenshot → `flow/f####.png`
4. disable streamlines

Two invariants hold for the whole run, and both are the difference between an animation that
shows something and one that shows nothing:

- **The camera never moves.** Set once, never touched again.
- **The colour domain is pinned** via `setVolumeRange(min, max)` (`streamMessages.ts:42`),
  computed once as the p1–p99 union over all three anchors. Auto-ranging per frame would
  renormalise the hotspot away — the same trap the existing `demo_baseline` / `demo_cooled`
  pair had to avoid.

Per-frame reload is safe: `scene.load_dataset` (`scene.py:1399`) removes the IndeX cache
entry, removes the volume prim, waits six updates, then re-ingests — the fix already made for
field-switch re-voxelisation. Cost is 20–35 s per voxelisation and cannot be pushed lower in
this architecture; the frame de-duplication, the shared load and the
24 fps blend-up are what keep the total near 36 minutes for both animations.

The Kit streamer serves one client. Nothing else may hold `:8301` during a recording run.

## Assembly and delivery

`ffmpeg` is absent from this host and is installed into the `dcdtuni` env from conda-forge.

| Output | Spec |
|---|---|
| `hotspot_thermal.mp4`, `hotspot_flow.mp4` | 1920×1080, H.264, yuv420p, 24 fps, ~6 s |
| `hotspot_thermal.gif`, `hotspot_flow.gif` | 900 px wide (the width `make_gifs.py` already uses), 12 fps, palettegen/paletteuse |

Every frame carries a burnt-in overlay: the stage caption, the pinned colour bar with its
numeric limits, a progress bar, and a small line reading "CFD 稳态解（未收敛）· 中间帧为插值".

Three consumers change:

- **Page** — `public/index.html`: the `demo_baseline` / `demo_cooled` A/B block becomes
  `<video autoplay loop muted playsinline>` with the GIF as fallback, and the surrounding
  copy (which currently explains a 0.94 K change) is rewritten to describe the failure and
  the intervention. `verify_page.py` gains a check that both videos are present and playing.
- **Deck** — `make_gifs.py:build_cooling` is replaced by a builder that copies the two new
  GIFs; `build_ppt.py` picks up the new slide and speaker notes.
- **README** — the provenance table gains rows for the three anchors and the interpolation,
  so the next person can tell which frames are solved and which are not.

## What can go wrong

**S1 is not dramatic enough.** Pre-empted by taking out three ACUs instead of two. Still
measured before rendering, because an hour of recording a dead field is the expensive mistake.
If it somehow reads flat, raise rack power — never compensate with the colour map.

**A relaxed solve gets quoted as a result.** The animation will look like CFD output because
it is CFD output, just unconverged. The qualifier has to survive into the page copy, the
speaker notes and the burnt-in overlay, or the demo makes a claim this spec did not earn.

**The three meshes are not identical.** Guarded by the points/owner hashes and the pre-
interpolation assertion, with `vtkResampleWithDataSet` as the fallback.

**A stale AI worker on `:8302` answers with old code** — documented in
`docs/drag-and-sim/CORRECTIONS.md` §1.3. This spec's path does not use the AI worker at all,
but `start_demo.sh` starts it; if it is up, it must not be mistaken for the data source.

**Anonymity leak.** The recording and assembly scripts live in this repo and may name `GTC`;
nothing they *emit* may. The overlay text, output filenames, page copy, and speaker notes are
checked against that before publishing.
