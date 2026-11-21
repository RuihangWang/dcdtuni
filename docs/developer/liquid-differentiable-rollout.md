# Differentiable transient liquid API

> **Status (as of 2026-10-21):** this rollout belongs to the v2 runtime equations and is kept as a comparison baseline. The production solver is the Newton DAE (pipe and solid storage, HX volumes, implicit adjoint); its differentiable entry points are `DAEExperiment(definition, record_frames=False)` and `agent.scripted_api.solve_liquid_steady` (see [liquid-dae-solver-first.md](liquid-dae-solver-first.md) and [Scripted API](../reference/scripted-api.md)).

`backend.liquidcooling.solver.rollout.differentiable_rollout` runs the own-repository hydraulic and thermal kernels without JSON conversion or a `no_grad` boundary across the trajectory. The v2 browser/session API uses the same equations. Both paths share time-slice preparation and the same physical equations; tensor optimization does not silently change their closure laws.

This implementation builds on the implicit-adjoint approach already present in the project's own `ddl/src/softlc.py` (kept at the tag `agent-pre-slim-2026-10-22` since the DDL tree was removed), extending the backend interface to pump/valve controls, pressure boundaries and thermal trajectories. No third-party solver code is used.

## Basic use

```python
import torch
from backend.liquidcooling.schema import LiquidrunConfig
from backend.liquidcooling.graphcompiling.compiler import compile_graph
from backend.liquidcooling.solver.rollout import differentiable_rollout

# configuration is a resolved .liquidrun dictionary, including parameter pack.
graph = compile_graph(LiquidrunConfig.model_validate(configuration))
# Clone the current schedule; one row per pump, one column per physical sample.
speed = graph.pump_params.rpm_ratio.detach().clone().requires_grad_(True)
trajectory = differentiable_rollout(graph, pump_rpm_ratio=speed)
loss = trajectory.solid_temperature[:, -1].square().mean()
sensitivity, = torch.autograd.grad(loss, speed)
```

The example objective is illustrative, not a recommended NV control objective. Real optimization needs thermal/flow/actuator constraints and a justified cost model. Pump electrical consumption is not inferred from speed alone.

Inputs `pump_rpm_ratio`, `valve_opening`, and `heat_W` accept `[component, sample]` schedules or `[component]` constants. `dt` can be a scalar tensor. `initial_state` is a `(node temperature, pipe-cell temperature, cold-plate solid temperature)` tuple. Parameters such as cold-plate/HX UA and material/pipe properties can be differentiable tensors in the compiled graph. Keep topology immutable during forward/backward; derive all linked quantities consistently (for example diameter, area and coolant volume).

Outputs are tensors with `[component, sample]` shape: `pressure` (Pa), `flow` (kg/s), `node_temperature`, `water_temperature`, `solid_temperature`, and `edge_outlet` (K). `time_s` includes sample zero. `diagnostics` contains one thermal diagnostic dictionary per advancing step, aligned with `time_s[1:]`. Heat at sample zero is not integrated into an already supplied initial condition.

## Closed-loop control

Supply `controller(index, previous_frame, state) -> (commands, new_state)` and an explicit initial `controller_state`. Commands may contain `pump_rpm_ratio` and/or `valve_opening`, with one vector entry per corresponding component. Return tensors directly. The controller reads the previous accepted physical frame, so its output affects the next hydraulic/thermal step without an algebraic look-ahead loop.

```python
from backend.liquidcooling.control import PIDConfig, initial_pid_state, pid_step

pid = PIDConfig(kp=0.003, ki=0.0002, setpoint=330.0,
                action='direct', bias=0.8, output_min=0.2, output_max=1.0)
gains = torch.tensor([0.003, 0.0002, 0.0], dtype=torch.float64,
                     requires_grad=True, device=graph.bc_P.device)

def controller(index, previous, state):
    update = pid_step(pid, state, previous.solid_temperature[0],
                      graph.dt, gains=gains)
    return {'pump_rpm_ratio': update.output.reshape(1)}, update.state

trajectory = differentiable_rollout(
    graph, controller=controller, controller_state=initial_pid_state(pid))
```

This single-pump example uses Kelvin. Gains/setpoint are mock values, not calibrated equipment settings. Construct fresh controller state for each independent rollout. The existing PID keeps tensor integral/measurement history, anti-windup and optional slew limiting. Gradient semantics at clamps follow the selected piecewise branch.

## Hydraulic derivative

Forward uses the converged GGA state. It fails explicitly on nonconvergence and never differentiates an unconverged iterate. For active edges, the residual is

`R_momentum = dp(m, theta) - A_free.T @ P_free - boundary_pressure_drop`,
`R_mass = A_free @ m`.

Backward forms the exact derivative of the headloss residual and solves its transpose system. The normal path uses a node Schur complement; zero-slope edges use the full saddle-point matrix. No artificial diagonal regularization is substituted for an unavailable physical derivative. Non-finite/singular adjoints fail explicitly. Fixed pressure values also receive their direct output sensitivity.

Closed valves are removed from hydraulic connectivity and have exactly zero flow. Their opening sensitivity is zero at exact closure by the existing model convention; this is not a claim of a smooth physical derivative through a topology change. Dead-end pressures are included in the adjoint. The API supplies **first-order** derivatives, not higher-order Newton-through-adjoint derivatives.

## Evidence and limits

Validation used CPU float64 and small synthetic networks:

- The tensor and session paths agree on pressure, flow, temperatures and nonconstant pump schedule timing.
- Centered finite differences agree for pump speed, valve opening, heat load, UA, consistently linked pipe diameter/area/volume, pressure reference, timestep, initial temperature and closed-loop PI gain.
- Separate checks cover every entry of a nonconstant pump schedule, causal heat sensitivities, positive/negative pressure-driven flow, dead ends, exact closure, nonconvergence and unchanged input graph data.
- A bounded mock parameter-fit regression decreases trajectory error through the actual coupled gradient. This is not a deployment-ready optimizer or an energy-savings result.
- Main affected-kernel/runtime/API run: 84 passed, one opt-in performance probe skipped. Four additional new gradient/fit checks passed separately; 18 tests cover the new differentiable API in total.

Hydraulics remain quasi-steady, with no fluid-inertia/water-hammer state. Thermal dynamics use implicit Euler, pipe-fluid and solid storage, quasi-steady cold-plate coolant and a prescribed facility reservoir. Flow-direction changes and exact shutoff are piecewise regimes. Long horizons retain a thermal autograd graph; checkpointing, large-topology speed and GPU-scale validation remain future numerical work.

Tensor runs do not automatically create browser-session artifacts. An optimization integration must save the configuration/parameter provenance and selected tensor schedules, then validate the candidate through the normal runtime. Existing checkpoint/persistence review findings remain separate open work.
