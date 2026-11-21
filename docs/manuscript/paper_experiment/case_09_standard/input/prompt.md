## Part 1: Geometry and Properties

Data center room, 18.2m x 8.3m x 3.8m (floor-to-ceiling height).

24 server cabinets arranged in 2 rows, with alternating face-to-face arrangement between rows.
Cabinet dimensions 0.75m x 1.2m x 2.35m (W x D x H), each housing 11-17 units of 1U/2U fan servers, individual power consumption 1500W, total power approximately 463.5kW.
Server installation positions, power consumption, and airflow rates for each cabinet are detailed in [server_power.csv](server_power.csv).

3 ACUs (2 on the left side, 1 on the rear side), rear/front supply air, top return air. Positions, dimensions, supply air temperature, and airflow volume for each ACU are detailed in [ACU.csv](ACU.csv).

7 baffles/partition panels in total.

## Part 2: Mesh Refinement

- Base mesh size: 0.339m
- Maximum global cell count: 1,000,000
- Rack surface refinement: level [2, 2]
- Server outlet face refinement: level [3, 3] (finer resolution to capture jets)
- ACU surface refinement: level [1, 1]
- Baffle/partition wall surface refinement: level [1, 1]
- Leakage region refinement: level [2, 3]
- No additional region refinement (box refinement [1, 1])
- Non-orthogonal correction: enabled, 0 correction layers

## Part 3: Solver Control and Output

- Solver type: steady-state (steady)
- Maximum iteration count: 2000
- Parallel cores: 16
- Solver preset: fast (upwind scheme)
- Convergence residual thresholds:
  - p_rgh: 0.001
  - U: 0.001
  - T: 0.001
  - k/epsilon: 0.001
- Relaxation factors:
  p_rgh=0.3, U=0.7, T=0.7, k_epsilon=0.7
- Output interval: write every 2000 steps
- Ambient temperature: 22°C
