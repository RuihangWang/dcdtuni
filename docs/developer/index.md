# Developer Documentation

Guides for understanding DCTwin's architecture and contributing to the codebase. For what works today and what comes next, start with **[Project state](PROJECT_STATE.md)** (Chinese, updated 2026-10-21). Agent and engineering rules live in `AGENTS.md` at the repository root.

## Setup and contributing

- **[Fresh clone deployment](fresh_clone_deploy.md)** — bootstrap, the in-repo environment, the launcher, optional Omniverse
- **[Contributing](contributing.md)** — development setup, coding conventions, tests
- **[macOS build notes](macos_build_notes.md)** — building the OpenFOAM third-party tools on macOS

## Architecture and server

- **[Architecture](architecture.md)** — system layers, data flow, package structure
- **[REST API Reference](api-reference.md)** — HTTP endpoints served by `src/server/server.py`
- **[WebSocket Terminal](websocket.md)** — the optional admin terminal (`--enable-terminal`)
- **[Render-scene contract](render-scene-contract.md)** — the renderer-neutral scene shared by Three.js and Omniverse

## Formats and references

- **[.cfdproj v5](../reference/cfdproj-v5-schema.md)** / **[v4](../reference/cfdproj-v4-schema.md)**, **[.cfdrun v2–v4](../reference/cfdrun-v2-schema.md)**, **[.liquidrun](../reference/index.md)** — field references
- **[Mesh engines](../reference/mesh-engines.md)** — `dcCart` (default), `snappy` (`meshgen` retired 2026-10-26)
- **[Scripted API](../reference/scripted-api.md)** — headless runs through a private server

## Air-cooling runner

- **[Runner Internals](runner-internals.md)** — STLGenRunner pipeline: mesh → solve → results
- **[Boundary Conditions](bridge-bc.md)** — STL solid names → OpenFOAM patch → BC mapping
- **[Air run result receipt V1](air-run-result-receipt-v1-2026-09-27.md)** — content identity of a solved time step (`air_run_result_receipt.py`)
- **[dcCart memory profile](dccart-memory-profile-2026-09-21.md)** — basis of the dcCart memory guard (cited by the runner)
- **[snappy castellation findings](mesh-castellation-performance-findings.md)** — where snappyHexMesh time goes (cited by the runner)
- **[SCANO AI solve](scano-ai.md)** — the solver tab's AI sub-tab: first iteration → DCTS cache → SCANO → a post-processable run (validated on four DCTS test rooms)

## Geometry topology (open design)

- **[ADR: explicit geometry topology V1](adr-geometry-topology-v1-2026-09-27.md)** — proposed; declaration schema and export blocks exist, geometric admission is the next step
- **[Physical source surfaces V1](physical-source-surfaces-v1-2026-09-27.md)** — source-surface identities carried in `.cfdrun`
- `geometry-topology-v1.schema.json`, `geometry-topology-v1.example.json` — the ADR's schema and example

## Transient keyframes

- **[Transient keyframes design](transient_keyframes_design_2026-09-30.md)** — cfdproj v5 time line, `.liquidrun` v4 (cited by code comments; the completed plan is archived)

## Liquid cooling

- **[Solver first: Newton DAE](liquid-dae-solver-first.md)** — the production differentiable DAE solver
- **[DAE workbench](liquid-dae-workbench.md)** — the React liquid experiment workbench on the DAE solver
- **[Liquid runtime API](liquid-runtime-api.md)** — `/api/liquid/runs` sessions, frames, checkpoints, replay
- **[MMS product derivation](liquid_dae_mms_product_derivation.md)** — manufactured-solution acceptance case
- **[Parameter packs](liquid-parameter-packs.md)**, **[Equipment visuals](liquid-equipment-visuals.md)**, **[Project contract](liquid-project-contract.md)**
- **[Dynamic editor graph](liquid-dynamic-editor-graph.md)** — editor-side graph compiler for liquid experiments
- Compatibility paths: **[v2 runtime workbench](react-liquid-runtime-workbench.md)**, **[PID control (v2)](liquid-control.md)**, **[Differentiable rollout (v2 baseline)](liquid-differentiable-rollout.md)**

## History

Dated handoffs, decision snapshots, audits and completed plans are in **[docs/archive](../archive/README.md)**, with the original path and the reason for each move. Benchmark receipts stay in `docs/benchmarks/`; [its index](../benchmarks/README.md) groups them by topic.
