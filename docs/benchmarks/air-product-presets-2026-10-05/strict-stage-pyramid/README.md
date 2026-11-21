# Unsuccessful local pyramid diagnostic

This opt-in prototype is preserved as a patch, not enabled in the product. A real React export and mesh API job completed in 10.1475 s but failed five full `checkMesh -allTopology -allGeometry` checks. It subdivided 15,913 flagged parent cells and retained every original base polygon.

Bad face tets remained 1,914; determinants rose from 107 to 1,379; concave-or-planar cells rose from 16,617 to 43,400; low weights rose from 1,062 to 3,321; low volume ratios rose from 1,369 to 2,391. The generated mesh is rejected.

New side triangles can be coplanar when a base contains collinear edge vertices. Joining only sides between the same pair of children cannot join sides attached to different adjacent base facets. The experiment also leaves unsupported concave base polygons unchanged and creates poor cell-size transitions. No strict thresholds were changed.

`matrix.json` binds input, exported surfaces, native artifact hashes, timings and API rejection. `prototype-source-identity.json` and `unsuccessful-prototype.patch` bind the tested source. The original generated case remains an isolated diagnostic fixture.
