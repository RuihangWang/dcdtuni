# Controlled Kit dataset/scene race closure — 2026-10-06

Scope: IT source only, CPU affinity 36–39, CUDA empty. Repository AGENTS.md read.
No real Kit, browser, HTTP/WebSocket API, service change, CFD run, build, or Git operation.

## Observed RED

The actual extension router, load_dataset and _ingest_dataset methods execute
from their source AST; Kit stage/importer/executor dependencies are controlled
CPU fakes. The test deliberately blocks each old load at:
1. EnSight conversion;
2. import_to_stage before writing /World/internal;
3. grid/bounds read after import.

The old code authors a new engineering scene immediately, then the old load
resumes and gives ok=True. The converter/importer cases also import the old
dataset while the fake stage identifies the new project. logs/RED.log preserves
3 failing controlled interleavings plus 1 passing unchanged-scene load.

This is source-level reproduction with controlled dependencies. It is not
live Kit or decoded-video evidence.

## Narrow closure

One product source path changes:
src/frontend/omniverse/cfd_demo/kit/extensions/dc.cfd_bridge/dc/cfd_bridge/extension.py

- A valid incoming scene request increments a display generation synchronously.
  Dataset generation is captured at dispatch, before its coroutine runs.
- Dataset import, bounds read and optional slice restoration hold the same
  async commit lock as editor scene authoring.
- A superseded load refuses success, lets the importer settle, and clears old
  imported prims/source/slice caches before a waiting new scene can commit.
- An already queued old load refuses before touching or clearing a newer result.
- A superseded queued scene refuses without authoring stale geometry.
- Completed display state survives a same-project scene re-author, as before.
- Scene authors still record requestId/revision/content hash only after authoring.
  View/camera/selection application is factored without changing reply fields.
- The existing verified grant and dataset reply run/runId/time are unchanged.
  Air result-layer trusted provenance remains fail-closed.

scene.py is byte-identical to the saved pre-change source.

## Focused GREEN

23 PASS in 0.55 s:
7 dynamic dataset/scene cases, 6 existing routing contracts, 10 grant tests.
The extra cases cover a queued old task, latest-only pending scene commit and
same-project completed-result preservation. Unchanged-scene loading reports the
original grant runId/time.

No tsc, production build or wider test suite was run by this agent.
source-before/, source-after/, source identity JSON and patches are preserved.

## Deployment and practical limit

This change requires ROOT's normal Kit deployment/restart before live behavior
changes. Current resident Kit was not touched.

A new scene can wait for the old converter/importer to settle. Cancelling an
unknown stage-writing Kit importer would not establish safe writer completion;
the barrier deliberately preserves that ordering. Live timing and real Kit
importer behavior still need normal UI verification after deployment.

The closure is for verified loadResultDataset versus loadRenderScene. Legacy
reference-demo loadDataset and independently scheduled legacy display commands
are outside these focused interleavings; this packet does not claim a general
serialization proof for all Kit commands.
