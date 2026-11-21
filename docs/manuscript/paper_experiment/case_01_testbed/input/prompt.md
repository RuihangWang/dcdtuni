## Part 1: Geometry and Properties

Data center room, 6.1m × 3.2m × 2.7m (floor-to-ceiling height).

2 server cabinets arranged in 2 rows, all rows with the same exhaust direction: facing left (-X).
Cabinet dimensions 0.6m × 1.02m × 1.96m (W×D×H), each equipped with 2-3 × 4U fan servers, 1000W per server, total power approximately 5.0kW.
Server installation positions, power ratings, and airflow rates for each cabinet are detailed in [server_power.csv](server_power.csv).

4 ACUs (1 on the left side, 1 on the front side, 2 on the rear side), right-side supply air, left-side and top return air. ACU positions, dimensions, supply air temperatures, and airflow volumes are detailed in [ACU.csv](ACU.csv).

8 baffles/partition panels in total.

## Part 2: Mesh Refinement

- Base mesh cell size: 0.2m
- Maximum global cell count: 5,000,000
- Rack surface refinement: level [3, 3]
- Server exhaust face refinement: level [3, 3] (finer resolution to capture jets)
- ACU surface refinement: level [2, 2]
- Baffle/partition wall surface refinement: level [1, 1]
- Leakage region refinement: level [2, 3]
- No additional region refinement (box refinement [1, 1])
- Non-orthogonal correction: enabled, 1 corrector layer

## Part 3: Solver Control and Output

- Solver type: steady-state (steady)
- Maximum iteration steps: 500
- Parallel cores: 4
- Solver preset: fast (upwind scheme)
- Convergence residual thresholds:
  - p_rgh: 0.0001
  - U: 1e-05
  - T: 1e-05
  - k/epsilon: 0.0001
- Relaxation factors:
  p_rgh=0.3, U=0.7, T=0.7, k_epsilon=0.7
- Output interval: write every 500 steps
- Ambient temperature: 22°C
