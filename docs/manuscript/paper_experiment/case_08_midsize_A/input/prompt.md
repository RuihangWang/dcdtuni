## Part 1: Geometry and Properties

Data center room, 9.0m x 7.9m x 3.8m (floor-to-ceiling height).

14 server cabinets arranged in 2 rows, with alternating face-to-face arrangement between rows.
Cabinet dimensions 0.6m x 1.2m x 2.05m (W x D x H), each housing 4 units of 4U/8U fan servers, individual power consumption ranging from 1000-2000W, total power approximately 84.0kW.
Server installation positions, power consumption, and airflow rates for each cabinet are detailed in [server_power.csv](server_power.csv).

4 ACUs (arranged in 1 row evenly distributed within the room), front supply air, rear return air. Positions, dimensions, supply air temperature, and airflow volume for each ACU are detailed in [ACU.csv](ACU.csv).

3 baffles/partition panels in total.

## Part 2: Mesh Refinement

- Base mesh size: 0.339m
- Maximum global cell count: 5,000,000
- Rack surface refinement: level [3, 3]
- Server outlet face refinement: level [1, 1] (finer resolution to capture jets)
- ACU surface refinement: level [1, 1]
- Baffle/partition wall surface refinement: level [1, 1]
- Leakage region refinement: level [2, 3]
- No additional region refinement (box refinement [1, 1])
- Non-orthogonal correction: enabled, 1 correction layer

## Part 3: Solver Control and Output

- Solver type: steady-state (steady)
- Maximum iteration count: 500
- Parallel cores: 8
- Solver preset: balanced (upwind scheme)
- Convergence residual thresholds:
  - p_rgh: 0.001
  - U: 0.001
  - T: 0.001
  - k/epsilon: 0.001
- Relaxation factors:
  p_rgh=0.3, U=0.7, T=0.7, k_epsilon=0.5
- Output interval: write every 500 steps
- Ambient temperature: 22°C
