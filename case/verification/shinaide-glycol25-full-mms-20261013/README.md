# Current Shinaide Glycol25 manufactured verification case

This is the complete registered 229-node / 286-edge liquid network from the actual Windows React engineering baseline. It preserves the original synthetic/mock provenance and physical coefficients. `Baseline.cfdproj` is a genuine schema-valid saved project. `GUI-executed.liquidrun` records the independently checked ordinary GUI input for source identity; it is not used as a solved manufactured trajectory.

`PREREGISTRATION.json` is the unchanged original criterion and provenance record. Historical absolute paths in it identify the original evidence; the reproduction tool reads only the adjacent committed inputs and this checkout. It generates new real `.cfdproj` projects with case paths inside the chosen output directory, compiles them through the React compiler, and constructs forcing using independent NumPy equations without calling the product residual or Newton solver.

Run this command-line verification on the Linux backend. Windows is used for the browser interface; this CLI uses Linux process groups and CPU affinity reporting.

From the repository root, install the liquid runtime and the optional independent verification dependency:

```sh
python -m pip install -e ".[liquid,verification]"
npm --prefix src/frontend/react ci --include=dev
```

React development dependencies are required for the actual compiler (`jsdom` and `esbuild`). Use a Node.js version accepted by the committed lockfile: `^20.19.0 || ^22.13.0 || >=24.0.0`. The independent LP and sparse backward-Euler recurrence were verified with SciPy 1.15.3; the optional `verification` extra declares `>=1.10,<2.0`.

From the repository root, prepare the two manufactured projects:

```sh
python -B tools/benchmarks/liquid_current_mms/reproduce.py
```

To run both full CPU grids, the independent backward-Euler thermal error recurrence, and the original complete-trajectory criteria:

```sh
python -B tools/benchmarks/liquid_current_mms/reproduce.py --verify
```

Output is created in a new `_tmp/liquid-current-mms/<timestamp>/` directory. `--output` accepts another new directory inside this checkout. No old run is overwritten. The numerical solver uses CPU, float64 and at most two PyTorch threads; GPU visibility is empty. This is numerical verification, not vendor calibration, all-parameter gradient validation or combined wind/liquid acceptance. Generated projects can subsequently be loaded through the regular React interface; command-line completion is not browser E2E acceptance.
