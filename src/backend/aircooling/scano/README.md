# scano — the SCANO AI solve inside dcdtuni

Everything the AI solve (solver tab → AI 推理) uses, in one folder since 2026-11-16 (formerly
`src/backend/aircooling/ai` plus `src/backend/scano-Infra`): the dcdtuni pipeline that feeds the model (the modules
here: `pipeline`, `infer`, `models`, `memory`, `worker`, `foam_*`, `adapters/`; see `docs/developer/scano-ai.md`),
the vendored SCANO forward code under `vendored/`, and the checkpoints under `checkpoints/`.

| Path | What |
|---|---|
| `vendored/scano/` | The SCANO forward code, **copied verbatim** from `Litianyu141/SCANO` `src/scano` (the import closure of `scano.Main.cfg_common`, `dcts_common`, `eval_dcts`, `scano.Datasets.dcts_source`). Never edit it here. |
| `vendored/VENDOR.json` | The SCANO commit it came from and the sha256 of every copied file. |
| `vendored/sync_from_scano.py` | Refresh `vendored/scano/` from a SCANO checkout (`--check` compares against `VENDOR.json`). |
| `checkpoints/<id>/` | One model: `checkpoint.pt` (weights + run configuration + training statistics), `config.json`, `status.json`, the selection / evaluation summaries, and `model_card.json` (below). |
| `checkpoints/dcts_normalizer/` | The DCTS dataset's files the cards point to: `first_iteration.json` (the solver recipe of its boundary input), `training_ranges.json` (server loads and airflows, ACU airflows, room sizes of the 400 training rooms; the AI tab and the worker warn outside them), the cache statistics (`stats.json`, train cases only), `meta.json` (splits). |

## Models (2026-10-27, from Google Drive `SCANO/realtime_demo_20261027/model/`)

| id | model | DCTS test error | note |
|---|---|---|---|
| `scano_c118_s18` | SCANO (c118, seed 18), recommended | 0.393 | one whole-room forward, about 1 s on an RTX 4090, 11 GB peak |
| `abupt_bqa_ags_c86_s18` | AB-UPT + BQA + AGS (c86, seed 18) | 0.368 | every cell is an anchor: minutes and more memory per room |

Error: the mean over u, v, w, p_rgh, T of the per-field relative L2 error, 50 test rooms (SCANO `eval_v1_summary.json`).

## Updating

```bash
python src/backend/aircooling/scano/vendored/sync_from_scano.py --scano ~/mycode/SCANO   # SCANO's src/scano must be committed
python src/backend/aircooling/scano/vendored/sync_from_scano.py --check
```

## Model card: everything dcdtuni knows about a checkpoint

Nothing in dcdtuni names a model or a dataset; the card does (`backend.aircooling.scano.models`):

| key | meaning |
|---|---|
| `adapter` | input format and forward code (`backend/aircooling/scano/adapters/`): `scano-dcts-v1` = DCTS cache v1, vendored `GtcData`, the whole-room forward or the paper's TTA (`infer.py`) |
| `first_iteration` | the solver recipe of the boundary input: a file relative to the checkpoint directory or the object itself (`dcdtuni.first-iteration/v1`: `controlDict` / `fvSolution` / `SIMPLE` entries, `explicitBoundaryValues` mode, `processes`, `time`) |
| `training_ranges` | file or object; the result warns outside it, the real-time sliders span it |
| `mesh` | `{engine, base_cell_m, note}` of the training meshes; the AI tab warns when the project meshes otherwise |
| `memory` | `{node_bytes, row_bytes, tta_rows0, tta_rows_per_node}`: the GPU-memory fit the AI tab's estimate and the automatic choice between the whole-room forward and TTA read (`backend.aircooling.scano.memory`); measure a new checkpoint with `docs/benchmarks/scano-tta-2026-10-29/measure.py`, without it the c118 values are used |
| `name`, `name_zh`, `description(_zh)`, `dataset`, `recommended`, `test_error`, ... | what the model selector shows |

Two checkpoints with the same adapter and recipe share a case's work directory (first iteration, graph, model
input), so switching between them costs one forward.

**A new checkpoint of an existing format** (e.g. a larger model trained on more rooms in the DCTS format): copy its
`checkpoint.pt` (and summaries) into `checkpoints/<id>/`, write `model_card.json` with its adapter, recipe, ranges
and mesh. No code changes. A `scano-dcts-v1` checkpoint must be a decoupled DCTS run (`dataset.name = "dcts"`,
`context.mode = "decoupled"`); `infer.ScanoModel` refuses anything else.

**A new input format** (another cache layout, other boundary features, other forward code): add one module under
`adapters/` implementing `ingest`, `open_room` (whose room gives `state` and `predict`), `load_model` and
`warm_up` (contract in `adapters/__init__.py`), vendor its forward code here, and name it in the card. The
pipeline, worker, server, post-processing injection and editor stay as they are: they only read the prediction
contract (every cell, then every boundary face in the graph's order, physical u v w p_rgh T).

## Environment

The vendored code runs in the `dcdtuni` conda environment (Python 3.10, PyTorch 2.5, torch_geometric, torch_scatter):
`tomllib` (3.11+) is only used by SCANO's TOML loader, which inference never calls, so `infer.import_scano`
installs a stub when it is missing; `einops` is only needed by a legacy model the DCTS checkpoints do not use.
