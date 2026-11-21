# Real React GUI runbook: four new quadratic full-ring MMS cases

Scope: Root-owned browser only. This PRIVATE packet proves public compiler/constructor/coarse solve/replay; it does not contain a browser pass. Use the already running correct React service after Root completes its authorized restart. Do not restart a service merely for this file.

## Load each separately saved case

Use the left file-bar Load project button (`#file-bar-header .file-btn`, btn.load_project), select the existing server file browser, enter the FROZEN-v1/results directory below in `.lp-path-bar`, press Enter, select the named .cfdproj file and Load. Hidden directories may require the existing Show hidden checkbox. Loading through the real UI reader is required; do not inject a manually authored liquidrun or carrier through developer console.

| dt (s) | load this project | expected samples/times | declared isolated casePath |
|---:|---|---|---|
| 2.0 | `normalized-dt-2p0.cfdproj` | 5, 0…8 s | `/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/full-mms-quadratic-design-private-20261006/proposed-lowflow-cases/dt-2.0` |
| 1.0 | `normalized-dt-1p0.cfdproj` | 9, 0…8 s | `/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/full-mms-quadratic-design-private-20261006/proposed-lowflow-cases/dt-1.0` |
| 0.5 | `normalized-dt-0p5.cfdproj` | 17, 0…8 s | `/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/full-mms-quadratic-design-private-20261006/proposed-lowflow-cases/dt-0.5` |
| 0.25 | `normalized-dt-0p25.cfdproj` | 33, 0…8 s | `/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/full-mms-quadratic-design-private-20261006/proposed-lowflow-cases/dt-0.25` |

Server file-browser directory: `/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/full-mms-quadratic-product-private-20261006/FROZEN-v1/results`. These are the real saved/reopened compiler-verified project bytes, not old manual carriers. The declared output casePaths above are outside every frozen packet and inside dcdtuni. Root should confirm/create these new isolated writable case directories and that the normal selected case actually resolves to the declared path before Prepare; run persistence must never write FROZEN-v1. No directory/service is created by this runbook. If Root chooses another isolated directory/UID/provenance, save through the real UI and record its fresh project SHA/config hash rather than transplanting the table below.

The saved project opens with air steady, liquid transient and duration 8 s. In the shared top bar and Liquid experiment panel confirm: current liquid mode transient, PyTorch CPU float64, 2 threads, consistent initialization, dt matching the selected file, manual control, no load step, saved mock-1-dae-v3 parameter pack, primary 1 kg/s at 20°C (293.15 K), both HX volumes .02 m³. Pump ratio and all valve openings are 1 and physical heat multiplier is 1. The normal timeline uniform-start controls are not this opt-in MMS initial state; the explicit manufactured initial state/derivative in the saved benchmark must survive compilation. Do not re-adopt mock DAE or replace these with the ordinary uniform/steady initializer.

## Prepare, Start and capture actual artifacts

1. Click Export input manifest in Liquid experiment, preserve the actual download bytes and compare its schema4, transient/consistent, 229 nodes/286 edges, new formula/reference, explicit initial fields, exact 8s source grid and source digests to the table below. Export uses the same real compile path as Prepare. If any metadata/control input changed, record new actual input/project/config identities and do not call it byte-identical to the frozen baseline.
2. Click Prepare experiment (or New experiment after a previous session). Require status Ready, time 0, exactly frame0, no alert, metadata solver differentiable_newton_dae, expected dt and total count. Preserve actual run_id, config hash and normal input.json/checkpoint/result snapshots. A successful private constructor is not evidence that this POST worked.
3. Leave online controls unchanged and click Start. Fastest playback is allowed; it changes wall pacing rather than dt. Require status Completed, final time8s, exactly 5/9/17/33 frames in order and no scientific or protocol failure. The chosen casePath and run_id must own all persisted artifacts. Do not count cancel/pause as completed.
4. Inspect the real runtime monitor and save screenshots for each case. Export Result, Save checkpoint and Export accepted input using existing buttons. Require final accepted N_t, time/source-row prefix, original source digest at completion, checkpoint v3 full original input and run identity. Compare actual disk arrays with the independent reference using the packet script/equation artifacts. Use actual public checkpoint replay to compare all eight arrays at every sample; do not copy private frames into normal logs.
5. Repeat by loading the next complete project file. Changing dt alone on one saved file leaves a mismatched fixed source table; load the matching file instead of relying on resampling or loosening time-grid admission.

Current compiled source identities (unchanged exact saved project):

| dt | source_project SHA | benchmark/source SHA | canonical config hash |
|---:|---|---|---|
| 2.0 | `6d383d2140767722a133bed0584c4f81b4186da03e4d21b6ba78d52b86be32eb` | `b8a1ebf00e4227b3ab63e5e242da021f2ed36e1733d1c3e676ea6752bed10735` | `3069040075bcc4e3d7f800bba0806d4c8fe37ccaac5642f6edb193c867365155` |
| 1.0 | `affdac1a5c498cbb03799d16c04410a0a7eeb975e2cf7a405475bd9e0c042509` | `dbb9adf084c5abda2a8c3b785e363f934941d55ac5d7147c0f4a68bade5aae65` | `8e8318a5c1aeecafc1a2b05a5716a68b8133c1112eb8f840dcb98f8f80d89122` |
| 0.5 | `e336a7513c2f73745e3e821da3790a82b5fb7ede2fe863bc060d8c239081673d` | `efcc6b1d4af11a82e98d2fe21158ccad2742785b95ad09bca057e4c1d4126786` | `ea232e8c0ebc0c9a578e8df2c4bd54e3b5c949cc182b1283b84d044746fb4ffc` |
| 0.25 | `595d69fc1022aeb213354c2989272e3de4316f00e8159279e09cee814584080c` | `995a8b5749efde484acb438e5b6860192986ea1567e5ba0f0e3655b07ce97d0c` | `344d2f94ba72fd38d62adfb9cf4a72af95f9971cb8a511201b138ddf12191145` |

The inherited UID proj_Shinaide alone does not identify any of these runs. New canonical content SHA and source/grid identity do. The original frozen source/project files, old sinusoidal FAILED coarse EOC, historical analytic catalog and historical overlay remain untouched. The new 8s fixture should fail the old 60s overlay identity gate; an absent historical overlay is expected, not a reason to forge one.

Manufactured heat is an explicit artificial Pa/W forcing. The runtime total equation energy balance includes it, while a physical 56-cold-plate heat audit may show a legitimate forcing/storage difference; do not label that difference an unforced energy closure or accept a physical heat badge without checking its definition. User online control edits create a new fixed-forcing counterfactual and are not the original baseline trajectory. New-fixture heat AD/FD here is one fixed-source 1s check; full ordinary pump/valve sensitivity acceptance belongs to the separately frozen non-MMS full20s packet.
