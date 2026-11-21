# User Guide

Step-by-step guides for installing, configuring and using DCTwin. The editor is the React app that `src/start_server.sh` serves on port 8000 by default; the legacy single-file HTML editor is still available with `--app html` as a reference implementation.

## Getting Started

- **[Installation](installation.md)** — run the bootstrap, start the server, log in with the launcher's credentials
- **[Quick Start](quick-start.md)** — build and run your first CFD case end-to-end
- **[Scripted API](../reference/scripted-api.md)** — run export → mesh → solve without a browser

## Core Workflows

- **[STL Modeling](modeling.md)** — create data center geometry in the 3D editor and the 2D floor-plan editor
- **[Mesh engines](../reference/mesh-engines.md)** — `dcCart` (default) and `snappy`

## File Formats

- **[.cfdproj Format](cfdproj-format.md)** — the editor's project file (current version 5)
- **[.cfdrun Format](cfdrun-format.md)** — the run manifest the CFD backend consumes (current version 4)

## Results

- **[Post-Processing](post-processing.md)** — fields, slices, streamlines and volumes in the browser or Omniverse, the REST API, ParaView, probes and liquid-cooling results
