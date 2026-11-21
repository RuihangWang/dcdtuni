## Part 1: Geometry and Properties

Data center room, 7.9m × 7.9m × 4m (floor-to-ceiling height).

20 server cabinets arranged in 5 rows, with alternating face-to-face arrangement between rows.
Cabinet dimensions 0.6m × 0.6m × 2m (W×D×H), each equipped with 1-2 × 3U-32U fan servers, 30-2500W per server (varying), total power approximately 16.6kW.
Server installation positions, power ratings, and airflow rates for each cabinet are detailed in [server_power.csv](server_power.csv).

4 ACUs (2 on the left side, 2 on the right side), side supply air, side return air. ACU positions, dimensions, supply air temperatures, and airflow volumes are detailed in [ACU.csv](ACU.csv).

3 baffles/partition panels in total.

## Part 2: Mesh Refinement

- Base mesh cell size: 0.23m
- Maximum global cell count: 500,000
- Rack surface refinement: level [1, 3]
- Server exhaust face refinement: level [1, 2] (finer resolution to capture jets)
- ACU surface refinement: level [1, 2]
- Baffle/partition wall surface refinement: level [1, 2]
- Leakage region refinement: level [2, 3]
- No additional region refinement (box refinement [1, 1])
- Non-orthogonal correction: disabled

## Part 3: Solver Control and Output

- Solver type: steady-state (steady)
- Maximum iteration steps: 500
- Parallel cores: 8
- Solver preset: balanced (upwind scheme)
- Convergence residual thresholds:
  - p_rgh: 0.0001
  - U: 1e-05
  - T: 1e-05
  - k/epsilon: 0.0001
- Relaxation factors:
  p_rgh=0.3, U=0.7, T=0.7, k_epsilon=0.5
- Output interval: write every 50 steps
- Ambient temperature: 22°C
