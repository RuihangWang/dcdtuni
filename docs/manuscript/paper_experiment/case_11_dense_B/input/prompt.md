## Part 1: Geometry and Properties

Data center room, 17m x 20m x 4.82m (floor-to-ceiling height), raised floor 0.6m.

138 server cabinets arranged in 8 rows, with alternating face-to-face arrangement between rows.
Cabinet dimensions 0.6m x 1.2m x 2.2m (W x D x H), each housing 1-10 units of 1U-14U fan servers, individual power consumption 1195W, total power approximately 381.2kW.
Server installation positions, power consumption, and airflow rates for each cabinet are detailed in [server_power.csv](server_power.csv).

8 ACUs (arranged in 6 rows evenly distributed within the room), bottom supply air, top return air. Positions, dimensions, supply air temperature, and airflow volume for each ACU are detailed in [ACU.csv](ACU.csv).

**Aisle containment**: baffles + partition panels enclosing cold/hot aisles (16 baffles/partition panels in total).
One full-width partition wall (20.0m x 4.2m) divides the room into front and rear zones.

## Part 2: Mesh Refinement

- Base mesh size: 0.34m
- Maximum global cell count: 10,000,000
- Rack surface refinement: level [1, 2]
- Server outlet face refinement: level [2, 3] (finer resolution to capture jets)
- ACU surface refinement: level [1, 2]
- Baffle/partition wall surface refinement: level [1, 2]
- Leakage region refinement: level [2, 3]
- No additional region refinement (box refinement [1, 1])
- Non-orthogonal correction: enabled, 1 correction layer

## Part 3: Solver Control and Output

- Solver type: steady-state (steady)
- Maximum iteration count: 1000
- Parallel cores: 24
- Solver preset: custom (upwind scheme)
- Convergence residual thresholds:
  - p_rgh: 0.0001
  - U: 1e-05
  - T: 1e-05
  - k/epsilon: 0.0001
- Relaxation factors:
  p_rgh=0.3, U=0.5, T=0.5, k_epsilon=0.5
- Output interval: write every 1000 steps
- Ambient temperature: 22°C
