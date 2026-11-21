## Part 1: Geometry and Properties

Data center room, 31m x 19.2m x 5m (floor-to-ceiling height).

50 server cabinets arranged in 4 rows, with alternating face-to-face arrangement between rows.
Cabinet dimensions 0.6m x 1.2m x 2.09m (W x D x H), each housing 2-7 units of 2U/4U fan servers, individual power consumption ranging from 600-1999W, total power approximately 420.9kW.
Server installation positions, power consumption, and airflow rates for each cabinet are detailed in [server_power.csv](server_power.csv).

6 ACUs (arranged in 6 rows evenly distributed within the room), front supply air, rear return air. Positions, dimensions, supply air temperature, and airflow volume for each ACU are detailed in [ACU.csv](ACU.csv).

10 baffles/partition panels in total.

## Part 2: Mesh Refinement

- Base mesh size: 0.34m
- Maximum global cell count: 1,000,000
- Rack surface refinement: level [3, 3]
- Server outlet face refinement: level [3, 3] (finer resolution to capture jets)
- ACU surface refinement: level [1, 1]
- Baffle/partition wall surface refinement: level [1, 1]
- Leakage region refinement: level [2, 3]
- No additional region refinement (box refinement [1, 1])
- Non-orthogonal correction: enabled, 0 correction layers

## Part 3: Solver Control and Output

- Solver type: steady-state (steady)
- Maximum iteration count: 1000
- Parallel cores: 16
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
