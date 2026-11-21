# SCANO AI solve — TTA deployment and the automatic choice (2026-10-29)

Why: Base2-9 (8.69 M nodes, dcCart) ran out of memory in the whole-room forward on a 24 GB RTX 4090 (it asked for
15.35 GiB on top of 7.8 GiB). The AI tab now offers auto / whole / TTA (`backend.aircooling.ai.infer`,
`memory`, `worker`); this directory holds the memory fit behind the estimate and the accuracy check of TTA.

## Criterion (registered 2026-10-29 09:58 SGT, before the accuracy runs)

On the four DCTS example rooms with a converged CFD target (`_tmp/scano_pkg/examples/case_{0112,0388,0390,0414}/
model_input_cache`, SCANO's published model input; the paper's error: mean over u, v, w, p_rgh, T of the
per-field relative L2 error, every node), for each model (`scano_c118_s18`, `abupt_bqa_ags_c86_s18`):

- TTA with one pass is accepted as a deployment when its mean error over the four rooms is at most the whole-room
  forward's + 0.01, and no room is worse than its whole-room error by more than 0.02.
- TTA with 2 and 4 passes are reported as measured (no criterion).
- Every room and every setting is reported; seeds are fixed (infer.EVAL_SEED + pass).

## Accuracy (`eval_tta.py`, `results_2026-10-29.jsonl`; RTX 4090, GPU 5 of 40901, TF32)

Error = the paper's metric against the CFD target; forward and peak memory of the whole room / TTA x1.

`scano_c118_s18`

| room | whole | TTA x1 | TTA x2 | TTA x4 | T error whole / TTA x1 | forward s | peak GiB |
|---|---|---|---|---|---|---|---|
| 0112 | 0.4614 | 0.4601 | 0.4604 | 0.4604 | 0.2306 / 0.2307 | 2.16 / 4.87 | 10.37 / 3.75 |
| 0388 | 0.4346 | 0.4284 | 0.4283 | 0.4280 | 0.2639 / 0.2714 | 0.84 / 6.04 | 12.12 / 4.05 |
| 0390 | 0.3638 | 0.3547 | 0.3547 | 0.3547 | 0.2174 / 0.2169 | 0.70 / 4.73 | 10.17 / 3.71 |
| 0414 | 0.3641 | 0.3558 | 0.3554 | 0.3551 | 0.1312 / 0.1276 | 0.75 / 5.24 | 10.85 / 3.84 |

Mean 0.4060 (whole) / 0.3997 (TTA x1), every room lower: **criterion met**. More passes change the third decimal.
The whole room's first forward (0112, 2.16 s) includes the first CUDA forward of the process.

`abupt_bqa_ags_c86_s18`

| room | whole | TTA x1 | TTA x2 | TTA x4 | T error whole / TTA x1 | forward s | peak GiB |
|---|---|---|---|---|---|---|---|
| 0112 | 0.4280 | 0.4282 | 0.4283 | 0.4281 | 0.2010 / 0.2015 | 223.36 / 16.15 | 7.26 / 2.27 |
| 0388 | 0.3973 | 0.3982 | 0.3978 | 0.3977 | 0.2574 / 0.2584 | 303.34 / 18.65 | 8.48 / 2.41 |
| 0390 | 0.3200 | 0.3205 | 0.3205 | 0.3202 | 0.2041 / 0.2041 | 214.74 / 14.87 | 7.13 / 2.25 |
| 0414 | 0.3418 | 0.3417 | 0.3420 | 0.3418 | 0.1288 / 0.1287 | 244.13 / 16.37 | 7.60 / 2.32 |

Mean 0.3718 / 0.3721 (+0.0004), worst room +0.0009: **criterion met**. The whole-room forward lets every query
attend to every node (1.4 M x 1.4 M; trained with 16,384 anchors), minutes per room and slower than the CFD solve
it would initialise (DCTS_0112 on 24 ranks: 360 s from a uniform field, 118 s from the AI fields); TTA draws the
training anchors. Its card therefore sets `"inference": {"auto": "tta"}`.

## Memory fit (`measure.py`; peak of `torch.cuda.max_memory_allocated`)

| model | room | nodes | deployment | rows | peak GiB | fit GiB |
|---|---|---|---|---|---|---|
| c118 | DCTS_0112 | 1,387,892 | whole | 1,387,892 | 10.20 | 10.21 |
| c118 | DCTS_0112 | 1,387,892 | TTA | 486,189 | 3.75 | 3.76 |
| c118 | Base2-9 (dcCart) | 8,686,403 | TTA (86 s, 126 forwards) | 1,384,791 | 11.61 | 11.65 |
| c118 | Base2-9 | 8,686,403 | whole | — | out of memory (fit: 63.9) | |
| c86 | DCTS_0112 | 1,387,892 | whole (217 s) | 1,387,892 | 7.15 | 7.41 |
| c86 | DCTS_0112 | 1,387,892 | TTA (16 s) | 386,129 | 2.27 | 2.53 |
| c86 | Base2-9 | 8,686,403 | TTA (157 s) | 565,721 | 6.57 | 6.83 |

c118's anchors are a fraction of the room (context.anchor_ratio 0.0125), so its TTA rows grow with it; c86 draws
16,384 anchor nodes, so its rows grow only with the subgraph sizes. Base2-9 has no CFD target on this mesh: its
TTA prediction is a generalisation test (dcCart mesh, six times the training rooms' size), not a measured error.

## Anchor cap (registered 2026-10-29 22:10 SGT, before the runs)

SCANO c118 draws its TTA anchors as a fraction of the room (context.anchor_ratio 0.0125 of the nodes, then their
2-hop subgraph), so a room ten times a training room carries ten times the anchors in every forward
(Alibaba-hongchou, 14.5 M nodes: 17.5 GB peak on a 24 GB card). Capping them at a training room's count is the same,
on a training-size room, as drawing a fraction 1/k of them. Measured on the four DCTS rooms, TTA x1, anchor fraction
x1, x1/2, x1/4, x1/10 of the trained one. Criterion: the cap is used for a room up to k times a training room when
the x1/k error is at most the x1 error + 0.01 on average and + 0.02 in every room; otherwise rooms keep their
fraction (and run on a GPU that fits them).

### Anchor cap: results (`results_anchor_cap_2026-10-29.jsonl`)

| room | x1 | x1/2 | x1/4 | x1/10 | T error x1 / x1/10 | peak GiB x1 / x1/10 |
|---|---|---|---|---|---|---|
| 0112 | 0.4601 | 0.4604 | 0.4605 | 0.4617 | 0.2307 / 0.2326 | 3.74 / 3.03 |
| 0388 | 0.4284 | 0.4285 | 0.4288 | 0.4304 | 0.2714 / 0.2789 | 4.05 / 3.22 |
| 0390 | 0.3547 | 0.3551 | 0.3546 | 0.3567 | 0.2169 / 0.2153 | 3.71 / 3.00 |
| 0414 | 0.3558 | 0.3552 | 0.3552 | 0.3564 | 0.1276 / 0.1295 | 3.84 / 3.10 |

Mean 0.3998 (x1) / 0.4013 (x1/10), worst room +0.0020: **criterion met** for rooms up to ten training rooms.
TTA therefore draws a training room's anchor count (`infer.anchor_scale_for`: the fraction x training nodes / room
nodes, not below x1/10). Alibaba-hongchou (14.46 M nodes, 4.08 M boundary faces): 17.5 GB -> 15.8 GB, 89 s; the
peak of such a room is set by its boundary records (every query row attends to them: 22 KB a query row against
8.7 KB on DCTS), so the card's law for c118 is `node_bytes x N + query rows x (query_row_bytes +
query_row_bytes_per_mface x faces/1e6) + anchor rows x anchor_row_bytes`, and a TTA chunk that still runs out of
memory continues the room at half its size (down to 1/8).
