## Part 1: Geometry and Properties

Data center room, 4.4m × 6m × 3.63m (floor-to-ceiling height), raised floor 1m.

4 server cabinets arranged in 2 rows (aligned along the Z direction), with alternating face-to-face arrangement between rows.
Cabinet dimensions 0.6m × 1m × 1.96m (W×D×H), each equipped with 1-2 × 6U/12U/14U fan servers, 1600-3600W per server (varying), total power approximately 17.6kW.
Server installation positions, power ratings, and airflow rates for each cabinet are detailed in [server_power.csv](server_power.csv).

2 ACUs (evenly distributed across 2 rows within the room), bottom supply air, top return air. ACU positions, dimensions, supply air temperatures, and airflow volumes are detailed in [ACU.csv](ACU.csv).

3 baffles/partition panels in total.

## Part 2: Mesh Refinement

- Base mesh cell size: 0.17m
- Maximum global cell count: 5,000,000
- Rack surface refinement: level [1, 2]
- Server exhaust face refinement: level [1, 2] (finer resolution to capture jets)
- ACU surface refinement: level [2, 2]
- Baffle/partition wall surface refinement: level [1, 1]
- Leakage region refinement: level [2, 3]
- No additional region refinement (box refinement [1, 1])
- Non-orthogonal correction: enabled, 1 corrector layer

## Part 3: Solver Control and Output

- Solver type: steady-state (steady)
- Maximum iteration steps: 1000
- Parallel cores: 8
- Solver preset: fast (upwind scheme)
- Convergence residual thresholds:
  - p_rgh: 0.0001
  - U: 1e-05
  - T: 1e-05
  - k/epsilon: 0.0001
- Relaxation factors:
  p_rgh=0.5, U=0.7, T=0.7, k_epsilon=0.7
- Output interval: write every 1000 steps
- Ambient temperature: 20°C
