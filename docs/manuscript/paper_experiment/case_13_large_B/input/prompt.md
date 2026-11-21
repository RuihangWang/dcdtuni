## Part 1: Geometry and Properties

Data center room, 30.0m x 36.6m x 6.4m (floor-to-ceiling height).

210 server cabinets arranged in 10 rows, with alternating face-to-face arrangement between rows.
Cabinet dimensions 0.8m x 1.2m x 1.8m (W x D x H), each housing 5-10 units of 4U fan servers, individual power consumption ranging from 305-1394W, total power approximately 994.9kW.
Server installation positions, power consumption, and airflow rates for each cabinet are detailed in [server_power.csv](server_power.csv).

16 ACUs (arranged in 15 rows evenly distributed within the room), front supply air, top return air. Positions, dimensions, supply air temperature, and airflow volume for each ACU are detailed in [ACU.csv](ACU.csv).

**Aisle containment**: baffles + partition panels enclosing cold/hot aisles (86 baffles/partition panels in total).
2 full-width partition walls (36.6m x 6.4m) divide the room into multiple zones.

## Part 2: Mesh Refinement

- Base mesh size: 0.34m
- Maximum global cell count: 10,000,000
- Rack surface refinement: level [2, 2]
- Server outlet face refinement: level [2, 3] (finer resolution to capture jets)
- ACU surface refinement: level [1, 2]
- Baffle/partition wall surface refinement: level [1, 2]
- Leakage region refinement: level [2, 3]
- No additional region refinement (box refinement [1, 1])
- Non-orthogonal correction: disabled

## Part 3: Solver Control and Output

- Solver type: transient
- End time: 300s
- Time step size: 0.1s
- Parallel cores: 8
- Solver preset: balanced (upwind scheme)
- Convergence residual thresholds:
  - p_rgh: 0.0001
  - U: 1e-05
  - T: 1e-05
  - k/epsilon: 0.0001
- Relaxation factors:
  p_rgh=0.3, U=0.7, T=0.7, k_epsilon=0.3
- Output interval: write every 500 steps
- Ambient temperature: 22°C
