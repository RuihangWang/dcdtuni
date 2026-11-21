## Part 1: Geometry and Properties

Data center room, 41.3m x 20.7m x 7m (floor-to-ceiling height).

200 server cabinets arranged in 18 rows, with alternating face-to-face arrangement between rows.
Cabinet dimensions 0.6m x 1.2m x 2.35m (W x D x H), each housing 5-16 units of 1U/2U/4U fan servers, individual power consumption ranging from 120-1780W, total power approximately 1136.3kW.
Server installation positions, power consumption, and airflow rates for each cabinet are detailed in [server_power.csv](server_power.csv).

8 ACUs (arranged in 1 row evenly distributed within the room), front supply air, rear return air. Positions, dimensions, supply air temperature, and airflow volume for each ACU are detailed in [ACU.csv](ACU.csv).

One full-width partition wall (41.3m x 7m) divides the room into front and rear zones.

## Part 2: Mesh Refinement

- Base mesh size: 0.34m
- Maximum global cell count: 5,000,000
- Rack surface refinement: level [2, 2]
- Server outlet face refinement: level [1, 1] (finer resolution to capture jets)
- ACU surface refinement: level [1, 1]
- Baffle/partition wall surface refinement: level [1, 1]
- Leakage region refinement: level [1, 2]
- No additional region refinement (box refinement [1, 1])
- Non-orthogonal correction: disabled

## Part 3: Solver Control and Output

- Solver type: steady-state (steady)
- Maximum iteration count: 1000
- Parallel cores: 16
- Solver preset: balanced (upwind scheme)
- Convergence residual thresholds:
  - p_rgh: 0.0001
  - U: 1e-05
  - T: 1e-05
  - k/epsilon: 0.0001
- Relaxation factors:
  p_rgh=0.3, U=0.7, T=0.7, k_epsilon=0.5
- Output interval: write every 1000 steps
- Ambient temperature: 22°C
