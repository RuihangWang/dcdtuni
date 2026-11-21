# Necessary collinear-point candidates on target bad-tet faces

This read-only audit scans every local polygon incident to each point on the 32 saved bad-tet faces. It excludes processor, cyclic, mapped and AMI incidences. No point or face is removed.

The 235 distinct local input points contain 57 candidates that lie strictly between their previous and next points on a common line in every incident polygon. Every single-point deletion would leave at least three face vertices. All 57 have the same unordered neighboring point-ID pair in every incidence, with three or four incident faces. The largest line distance is 6.269678786557333e-17 m. None touches a coupled patch. All 32 saved bad faces contain at least one candidate.

Processor0 point 203877 is a concrete example at (1.54515, 2.802500009536743, 1.6150046875). Its incidences are internal face 442843 and physical Rack_B_01_frame faces 1458724 and 1459922, with original vertex counts 6/8/6. Their previous/next IDs agree and all recorded line distances are zero. The JSON includes every original face ID, point list, cell incidence and patch identity. Two other candidate points touch two distinct physical patch names; their identities must survive any later native cleanup.

These are necessary geometric candidates, not a certified repair. Removing several neighboring points simultaneously can invalidate a polygon even if each individual removal passed. Any native cleanup needs a fresh check of the remaining polygon cycle, at least three distinct vertices, orientation, area, volume/closure and all cell/face/point references and zone mappings. It must preserve each physical patch identity. Candidate removal also does not prove that every remaining original-vertex tetrahedral fan has acceptable quality.

The frozen mesh remains unchanged. A later mesh repair would require the complete existing API check with allTopology and allGeometry, with every strict threshold retained. Four directed geometry/patch-veto tests passed.

Recompute with tools/audit_air_collinear_points.py --mesh <recorded Mesh path> --workers 8 --output <new JSON path>. The receipt binds the source files, original mesh files, saved set and raw output.
