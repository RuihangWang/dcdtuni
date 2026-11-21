# DCTwin Engine

DCTwin is a multiphysics digital twin engine for data center thermal simulation. It automates the air-cooling OpenFOAM pipeline — from a browser editor (`.cfdproj`) through geometry export (`.cfdrun` + STL), meshing, parallel solving and in-browser results — and couples it with a differentiable liquid-cooling solver for CDU, pipe and cold-plate networks. It is built for data center engineers who need temperature, pressure and velocity fields without configuring OpenFOAM by hand.

## Pipeline

| Step | Tool | Output |
| ---- | ---- | ------ |
| **1. Model** | React editor: 3D model, 2D floor plan, liquid network, transient timeline → `.cfdproj` v5 | Editable project |
| **2. Export** | Live project → multi-region ASCII STL + `.cfdrun` v4 manifest | Run inputs in `case/<name>/` |
| **3. Mesh** | `dcCartMesh` cut-cell Cartesian mesher (default, MPI) · `snappyHexMesh`; strict `checkMesh` | Volume mesh with a quality receipt |
| **4. Solve** | `buoyantBoussinesqSimpleFoam_new` (steady) / `buoyantBoussinesqPimpleFoam_new` (transient), MPI | Fields and a convergence verdict (ACU return air steady within 0.5 K) |
| **5. Visualize** | Post-processing in the browser (Three.js) or in Omniverse (kit-cae) | Surface fields, slices, streamlines, volumes, probes |
| **Liquid** | Newton DAE solver (steady, transient, implicit-adjoint gradients) in server sessions | Flows, pressures, temperatures; heat handed to the air run for hybrid cases |

## Components

| Component | Description |
| --------- | ----------- |
| **backend** | Python package (`src/backend`): `.cfdrun` schema and reader, boundary conditions, mesh engines, solver orchestration, receipts and convergence verdict, post-processing, the liquid-cooling DAE solver, and the scripted API (`python -m agent.scripted_api`) |
| **frontend** | React + TypeScript + Three.js r184 editor (`src/frontend/react`) with model logic shared with the legacy single-file editor (`src/frontend/shared`): geometry authoring, 2D floor plan, model library, first-person mode, liquid-cooling design and experiments, transient keyframes, post-processing; optional Omniverse display bridge |
| **server** | REST + SSE API server (`src/server/server.py`) for case and job management, mesh/solve, post-processing, liquid sessions and browser automation; started with `src/start_server.sh` on port 8000 by default |

## Getting Started

<div class="grid cards" markdown>

-   **Installation**

    ---

    Run the bootstrap: in-checkout environment, OpenFOAM 2412, native builds, the editor.

    [:octicons-arrow-right-24: Installation](user-guide/installation.md)

-   **Quick Start**

    ---

    Run your first simulation end-to-end in a few commands.

    [:octicons-arrow-right-24: Quick Start](user-guide/quick-start.md)

-   **STL Modeling**

    ---

    Build multi-region geometry visually in the browser editor.

    [:octicons-arrow-right-24: STL Modeling](user-guide/modeling.md)

-   **Scripted API**

    ---

    Run export → mesh → solve or a liquid steady state without a browser.

    [:octicons-arrow-right-24: Scripted API](reference/scripted-api.md)

-   **REST API**

    ---

    Integrate with the server's endpoints for automation.

    [:octicons-arrow-right-24: REST API Reference](developer/api-reference.md)

-   **Project state**

    ---

    What works today, the evidence behind it, and the next experiments (Chinese).

    [:octicons-arrow-right-24: Project state](developer/PROJECT_STATE.md)

</div>
