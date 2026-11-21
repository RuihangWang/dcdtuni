## Part 1: Geometry and Properties

Data center room, 15.6m x 24.6m x 4.4m (floor-to-ceiling height), raised floor 0.8m.

142 server cabinets arranged in 9 rows (aligned along the Z direction), with alternating face-to-face arrangement between rows.
Cabinet dimensions 0.6m x 1.2m x 2.2m (W x D x H), each housing 5-9 units of 4U fan servers, individual power consumption ranging from 302-1370W, total power approximately 642kW.
Server installation positions, power consumption, and airflow rates for each cabinet are detailed in [server_power.csv](server_power.csv).

7 ACUs aligned along the right wall, bottom supply air, top return air. Positions, dimensions, supply air temperature, and airflow volume for each ACU are detailed in [ACU.csv](ACU.csv).

**Cold Aisle Containment**: 4 cold aisle groups enclosed by end baffles + side partition panels, cold aisle tops strictly sealed (baffle H=2.2m matching rack height, with top covers). Hot aisles are open.
Additionally, one full-width partition wall (24.6m x 3.6m) divides the room into front and rear zones.

## Part 2: Mesh Refinement

- Base mesh size: 0.34m
- Maximum global cell count: 10,000,000
- Rack surface refinement: level [2, 2]
- Server outlet face refinement: level [2, 3] (finer resolution to capture jets)
- ACU surface refinement: level [1, 2]
- Baffle/partition wall surface refinement: level [1, 2]
- Leakage region refinement: level [2, 3]
- No additional region refinement (box refinement [1,1])
- Non-orthogonal correction: enabled, 1 correction layer

## Part 3: Solver Control and Output

- Solver type: steady-state (steady)
- Maximum iteration count: 1000
- Parallel cores: 24
- Solver preset: fast (upwind scheme)
- Convergence residual thresholds:
  - p_rgh: 1e-4
  - U: 1e-5
  - T: 1e-5
  - k/epsilon: 1e-4
- Relaxation factors: p_rgh=0.3, U=0.7, T=0.7, k/epsilon=0.7
- Output interval: write every 1000 steps
- Ambient temperature: 22°C
- Sensors: not configured (can be added later to monitor hot aisle return points)
