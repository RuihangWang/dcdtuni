# React liquid experiment workbench

> **Status (as of 2026-10-21):** this page describes the **v2 runtime workbench** (fixed speed or PI control, `.liquidrun` v2). New experiments now default to the Newton DAE workbench, described in [liquid-dae-workbench.md](liquid-dae-workbench.md); in the DAE workbench the PI control selector is disabled, and the v2 path remains for experiments saved with it. Result scrubbing along the timeline and the liquid colouring in Three.js and Omniverse have been delivered since this page was written (transient keyframes T5; [OV liquid display receipt](../benchmarks/ov-display-liquid-2026-09-30/README.md)).

React liquid mode → solver opens the dynamic experiment workbench. The legacy boundary-condition solve remains in its own expandable section and its monitor remains under the legacy-monitor tab. HTML air workflows are unchanged.

The workbench compiles the current editor topology into stable UID-bound graph identities, adds an actively cooled CDU loop and physical flow-control valves, and creates a server-owned runtime session. It supports fixed speed, one cold-plate-temperature PI or pump-differential-pressure PI controller, an optional simultaneous multiplicative cold-plate heat-load step, physical timestep/horizon, pacing, start/pause/resume/single-step/cancel, result download and checkpoint download. This version downloads checkpoints; browser restore/replay is a later integration step. Current PI gains are explicit mock starting values, not tuned NV control settings. All outputs stay in simulation.

Temperature setpoints entered in °C convert to Kelvin once. Pressure setpoints and gains entered per kPa convert to Pa. Temperature control uses direct action; pressure control uses reverse action. Pump output limits are 0.2–1.0 with a 0.05/s rate limit. Pressure measurement spans the selected pump, including its discharge node before the HX. The physics timestep and every event lie on one time grid.

The right monitor uses the run's physical time and stable ordered cold-plate/edge IDs for temperature, signed mass flow and controller output. It reports numerical energy residual separately from model validation. Old frames retain their originating project revision; changing geometry shows a stale-snapshot warning. Automatic polling backfills all intermediate frames even when action acknowledgments jump ahead and prevents older polls from reverting newer lifecycle acknowledgments. Server-side execution continues after browser disconnection; the client retries failed polls.

`case/LiquidSingle_Control` is a separately named generic mock variant of the original geometry. Its README records all changed physical assumptions. The original cases remain untouched. The current shared compiler starts solids at 340 K and coolant at 35 °C unless supplied through its fluid fields; the first run therefore includes startup cooling. The UI uses current cold-plate parameters rather than silently replacing them with pack defaults. Mock pump/HX/valve profiles come from one canonical backend JSON file; Vite permits only the required data directory in addition to its existing frontend directory.

Current boundary: live charts, downloaded physical results, UID-bound time scrubbing and the 3D thermal overlays work. Next steps: a checkpoint restore UI in the browser, multiloop controller authoring and optimization comparison. Equipment shape detail is implemented independently and never substitutes for thermal validation.

## Frontend closeout

For PI control, click **读取当前设备** before submitting a run, then select a pump and cold plate; multiple targets require an explicit choice. Dynamic cold-plate identities now derive from the canonical UID. Concurrent refreshes cannot overwrite newer lifecycle state; frames are checked against their time grid and horizon.

Validation: 49 focused React tests passed; production build passed with existing large-bundle/import warnings. Browser load-in-air then switch-to-liquid displays two detailed equipment roots for the Single_Control case without a manual rebuild. Original HTML IDs and original cases remain preserved.

User priority update: freeze further frontend expansion and focus on the differentiable liquid-cooling solver, transient accuracy, gradients and measured speed. Air cooling stays on OpenFOAM; the user-owned Newton/JFNK integration is future work. OV, agent training and new case expansion are deferred. Backend checkpoint/persistence findings remain tracked separately; this frontend closeout does not claim platform-wide acceptance.
