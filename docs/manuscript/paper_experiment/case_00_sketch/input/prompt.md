## Part 1: Geometry and Properties

Data center room, 14m × 7m × 3m (floor-to-ceiling height).

24 server cabinets arranged in 7 rows, with alternating face-to-face arrangement between rows.
Cabinet dimensions 0.8m × 1.2m × 2.2m (W×D×H), each equipped with 2 × 5U fan servers, 150W per server, total power approximately 7.2kW.
Server installation positions, power ratings, and airflow rates for each cabinet are detailed in [server_power.csv](server_power.csv).

4 ACUs (2 on the left side, 1 on the right side, 1 on the rear side), front supply air, top return air. ACU positions, dimensions, supply air temperatures, and airflow volumes are detailed in [ACU.csv](ACU.csv).

4 baffles/partition panels in total.

## Part 2: Mesh Refinement

- Base mesh cell size: 0.34m
- Maximum global cell count: 1,000,000
- Rack surface refinement: level [1, 2]
- Server exhaust face refinement: level [2, 2] (finer resolution to capture jets)
- ACU surface refinement: level [1, 1]
- Baffle/partition wall surface refinement: level [1, 1]
- Leakage region refinement: level [1, 2]
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
  p_rgh=0.3, U=0.7, T=0.7, k_epsilon=0.7
- Output interval: write every 1000 steps
- Ambient temperature: 22°C
