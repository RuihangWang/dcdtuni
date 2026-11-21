## Evaluation Result: PASS

Case: case_05_triangle, round 1. Input mode: Excel + image (`input/layout.xlsx` is the ground truth for positions, `input/topview.png` is the visual cross-check), plus `ACU.csv`, `server_power.csv` and `prompt.md`.
What was checked: `workdir/topview_generated.png`, `workdir/dc-params.json`, and the built `workdir/case_05_triangle.cfdproj` (footprints recomputed from its anchors and quaternions). Pixels in `input/topview.png` were converted to metres at 482.8 px/m (3840 px image), using the 7.6 m top edge and the room vertices as the reference.

### Object Inventory Comparison
| Object Type | Input | Generated | Status |
|-------------|-------|-----------|--------|
| Rack | 2 (Default_Rack_1, Default_Rack_2) | 2 | MATCH |
| ACU | 1 (ACU_1) | 1 | MATCH |
| Baffle | 0 | 0 | MATCH |
| Containment | 0 | 0 | MATCH |
| Floor Opening / raised floor | 0 (raised_floor_m = 0) | 0 (raisedFloor = null) | MATCH |
| Box/Support | 0 | 0 | MATCH |
| Room walls | 3 (triangle) | 3 (m_1, m_2, m_3) | MATCH |

### Item-by-Item Checklist
- [x] Room shape: MATCH. The room is a triangle with vertices (4.5, 3.3), (8.4369, 7.1252), (0.8369, 7.1252), matching xlsx `Room.polygon` exactly. The wall lengths are 5.4892 / 7.6 / 5.2963 m, the same as the input annotations (5489.1589 / 7600 / 5296.2576 mm). The area is 14.54 m² (0.5 × 7.6 × 3.8252), the winding is CCW, and the height is 3 m.
- [x] Rack count + grouping: MATCH. There are 2 single racks in a V arrangement, one on each side of the room axis x ≈ 4.5. Each rack is 0.6 × 1.0 × 1.8669 m (xlsx Legend; the prompt's 1.87 m is the rounded value).
- [x] Rack position: MATCH, within about 1 cm.
  - Default_Rack_2 (left): the input image centroid is (3.728, 5.354) and the built centre is (3.723, 5.348). The four input corners (3.865, 5.915), (4.288, 5.495), (3.588, 4.793), (3.163, 5.213) match the built corners (3.865, 5.914), (4.289, 5.490), (3.582, 4.783), (3.157, 5.207).
  - Default_Rack_1 (right): the input centroid is (5.316, 5.390) and the built centre is (5.317, 5.381). The input left corner (4.754, 5.530) matches the built (4.752, 5.523), and the input bottom corner (5.456, 4.828) matches the built (5.459, 4.816).
  - The xlsx Layout grid tags (J5 = (4.84, 5.63) for Rack_1, H6 = (3.84, 5.13) for Rack_2) are within one 0.5 m cell of both centres. They agree at grid resolution.
- [x] Rack orientation: MATCH.
  - Input arrow, right rack: down-right, i.e. +X/−Z (SE).
  - Built Default_Rack_1: yaw 135° (quaternion y = 0.9239, w = 0.3827). Its inlet-to-outlet vector is (+0.707, −0.707) = SE.
  - Input arrow, left rack: down-left, i.e. −X/−Z (SW).
  - Built Default_Rack_2: yaw 225° (quaternion y = 0.9239, w = −0.3827). Its inlet-to-outlet vector is (−0.707, −0.707) = SW.
  - Both inlets face up, toward the ACU wall and the space between the racks. This fits the prompt's "face-to-face". The arrows in the generated topview point the same ways as in the input.
- [x] Rack equipment: MATCH with server_power.csv.
  - Rack_1: fan-server_1 at U25, 9U, 2250 W, 0.1553 m³/s.
  - Rack_2: fan-server_1 at U13, 8U, 2000 W, 0.1381 m³/s, and fan-server_2 at U32, 8U, 2000 W, 0.1381 m³/s.
  - The power values are in the `transient.tracks` t = 0 keyframes. The total is 6.25 kW, against the prompt's "approximately 6.2 kW".
- [x] ACU count + position + wall: MATCH. ACU_1 sits on the 7.6 m wall at z = 7.1252.
  - Built footprint: x 3.893–5.093, z 6.525–7.125.
  - Input image bbox: x 3.898–5.087, top at 7.121.
  - The anchor (5.0925, 0, 7.1252), size 1.2 × 0.6 × 2 m and orientation 180° are exactly as in ACU.csv / xlsx ACU_Config.
- [x] ACU supply/return: MATCH with ACU.csv and the prompt ("rear supply air, top return air").
  - Supply patch p_1 covers the whole back face (1.2 × 2 m). With yaw 180°, the back face is the one at z = 6.525, facing into the room. The supply therefore blows toward −Z, toward the racks.
  - Return patch p_2 covers the whole top face.
  - Supply values are 15 °C and 4 m³/s (`transient.tracks` acu.supplyTemp / acu.airflow → p_1, t = 0) and 2 m/s (p_1.physics.velocity).
- [x] Baffle / Containment: MATCH. There are none in the input and none in the build.
- [x] Other: MATCH. There is no raised floor, floor opening or box in either.
- [x] Run settings (prompt Parts 2–3) that have editor fields: MATCH.
  - Mesh: meshBaseSize 0.34; maxGlobalCells 500000; ACU / box / baffle / partition-wall surface levels [1, 1] (unset in the file, so they export as [1, 1]); nonOrthoCorrection on with 0 correctors.
  - Solver: steady; endTime 500; writeInterval 500; solveProcess 8; preset fast with bounded Gauss upwind.
  - Convergence and relaxation: residuals 1e-4 for all fields; relaxation p_rgh 0.3, U/T/k-ε 0.7.
  - Ambient: geometry.temperature 22 °C.

### Corrections Needed
None for geometry, orientation or boundary values. The topview and the cfdproj match the input in count, position (within about 1 cm), orientation, and ACU wall and face placement.

### Notes for the Builder (no change required this round)
1. **Three refinement levels from the prompt have no editor field, so they are not carried.** These are rack surface [1, 2], server exhaust face [2, 3] and leakage region [2, 3]. The React mesh panel (`src/frontend/react/src/run/meshConfig.ts`, `REFINEMENT_ROWS`) offers only a single rack-region feature level (`rackRegionLevel`, currently 3) and no exhaust or leakage surface rows. The Builder's claim is confirmed. This is a gap in what the editor exposes, not a build error, and it does not affect the topview.
   - Next step if wanted: map the three values onto the nearest existing field in a mesh-settings pass.
2. **Supply flow and supply velocity are not consistent with each other in the input itself.** ACU.csv gives 4 m³/s and 2 m/s. Over the 2.4 m² back face, 4 m³/s gives 1.67 m/s. The Builder copied both values faithfully, which is correct for this round. Which value governs is decided by the runner, not by the layout.
3. **Supply values are stored in the transient tracks; the return patch also carries leftover supply-like fields.**
   - In the saved file, the supply patch p_1 carries only `velocity: 2`. Its temperature and flow are stored in the v5 `transient.tracks` t = 0 keyframes, which target p_1.
   - The return patch p_2 also carries `temperature: 15, volumeFlowRate: 4`. `buildCfdrunJson` reads only volumeFlowRate/velocity from return faces (`src/frontend/shared/js/cfdrun_build.js:727-731`). The values match the input anyway, so the exported boundary conditions are as intended.
4. **UNCERTAIN, informational only:** the xlsx Layout sheet has five "↑" glyphs in cells I3, J3, K3, H4 and I4, between the ACU and the racks. No legend defines them. They do not override the structured ACU_Config (orientation 180°, back-face supply) or the image arrows, which the build already matches. No change is suggested.

### Missing Features
None.
