## Part 1: Geometry and Properties

Data center room, 7.6m × 3.8m × 3m (floor-to-ceiling height).

2 server cabinets arranged in 2 rows, with alternating face-to-face arrangement between rows.
Cabinet dimensions 0.6m × 1m × 1.87m (W×D×H), each equipped with 1-2 × 8U/9U fan servers, 2000-2250W per server (varying), total power approximately 6.2kW.
Server installation positions, power ratings, and airflow rates for each cabinet are detailed in [server_power.csv](server_power.csv).

1 ACU, rear supply air, top return air. ACU positions, dimensions, supply air temperatures, and airflow volumes are detailed in [ACU.csv](ACU.csv).

## Part 2: Mesh Refinement

- Base mesh cell size: 0.34m
- Maximum global cell count: 500,000
- Rack surface refinement: level [1, 2]
- Server exhaust face refinement: level [2, 3] (finer resolution to capture jets)
- ACU surface refinement: level [1, 1]
- Baffle/partition wall surface refinement: level [1, 1]
- Leakage region refinement: level [2, 3]
- No additional region refinement (box refinement [1, 1])
- Non-orthogonal correction: enabled, 0 corrector layers

## Part 3: Solver Control and Output

- Solver type: steady-state (steady)
- Maximum iteration steps: 500
- Parallel cores: 8
- Solver preset: fast (upwind scheme)
- Convergence residual thresholds:
  - p_rgh: 0.0001
  - U: 0.0001
  - T: 0.0001
  - k/epsilon: 0.0001
- Relaxation factors:
  p_rgh=0.3, U=0.7, T=0.7, k_epsilon=0.7
- Output interval: write every 500 steps
- Ambient temperature: 22°C
