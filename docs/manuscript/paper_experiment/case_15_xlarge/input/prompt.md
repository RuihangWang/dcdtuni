## Part 1: Geometry and Properties

Data center room, 17.4m x 38.5m x 4m (floor-to-ceiling height).

238 server cabinets arranged in 15 rows, with alternating face-to-face arrangement between rows.
Cabinet dimensions 0.6m x 1.2m x 2.2m (W x D x H), each housing 1-21 units of 1U-22U fan servers, individual power consumption ranging from 70-5000W, total power approximately 1560.2kW.
Server installation positions, power consumption, and airflow rates for each cabinet are detailed in [server_power.csv](server_power.csv).

46 ACUs (arranged in 15 rows evenly distributed within the room), alternating rear/front supply air, alternating rear/front return air. Positions, dimensions, supply air temperature, and airflow volume for each ACU are detailed in [ACU.csv](ACU.csv).

30 baffles/partition panels in total.

## Part 2: Mesh Refinement

- Base mesh size: 0.34m
- Maximum global cell count: 10,000,000
- Rack surface refinement: level [3, 3]
- Server outlet face refinement: level [4, 4] (finer resolution to capture jets)
- ACU surface refinement: level [3, 3]
- Baffle/partition wall surface refinement: level [1, 1]
- Leakage region refinement: level [2, 3]
- No additional region refinement (box refinement [1, 1])
- Non-orthogonal correction: enabled, 1 correction layer

## Part 3: Solver Control and Output

- Solver type: steady-state (steady)
- Maximum iteration count: 2000
- Parallel cores: 24
- Solver preset: custom (upwind scheme)
- Convergence residual thresholds:
  - p_rgh: 0.0001
  - U: 1e-05
  - T: 1e-05
  - k/epsilon: 0.0001
- Relaxation factors:
  p_rgh=0.3, U=0.7, T=0.7, k_epsilon=0.3
- Output interval: write every 2000 steps
- Ambient temperature: 22°C
