## Part 1: Geometry and Properties

Data center room, 10.4m × 6.9m × 3.4m (floor-to-ceiling height), raised floor 0.4m.

8 server cabinets arranged in 1 row (aligned along the Z direction), all rows with the same exhaust direction: facing front (-Z).
Cabinet dimensions 0.6m × 1.2m × 2.0m (W×D×H), each equipped with 1 × 3U-7U fan server, 150-3600W per server (varying), total power approximately 8.2kW.
Server installation positions, power ratings, and airflow rates for each cabinet are detailed in [server_power.csv](server_power.csv).

2 ACUs (1 on the left side, 1 on the right side), bottom supply air, top return air. ACU positions, dimensions, supply air temperatures, and airflow volumes are detailed in [ACU.csv](ACU.csv).

5 baffles/partition panels in total.

## Part 2: Mesh Refinement

- Base mesh cell size: 0.34m
- Maximum global cell count: 500,000
- Rack surface refinement: level [2, 2]
- Server exhaust face refinement: level [3, 3] (finer resolution to capture jets)
- ACU surface refinement: level [1, 2]
- Baffle/partition wall surface refinement: level [1, 2]
- Leakage region refinement: level [1, 1]
- No additional region refinement (box refinement [1, 1])
- Non-orthogonal correction: enabled, 0 corrector layers

## Part 3: Solver Control and Output

- Solver type: steady-state (steady)
- Maximum iteration steps: 5000
- Parallel cores: 8
- Solver preset: fast (upwind scheme)
- Convergence residual thresholds:
  - p_rgh: 0.001
  - U: 0.001
  - T: 0.001
  - k/epsilon: 0.001
- Relaxation factors:
  p_rgh=0.3, U=0.7, T=0.7, k_epsilon=0.7
- Output interval: write every 5000 steps
- Ambient temperature: 22°C
