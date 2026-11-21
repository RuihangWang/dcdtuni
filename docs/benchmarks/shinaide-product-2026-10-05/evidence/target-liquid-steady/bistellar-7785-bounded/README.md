# Actual 7785 bounded bistellar diagnostic

This is a read-only finite diagnostic of the actual 7785c33f written mesh. It authorizes no native operator, mesh replacement, solver, or production promotion. The original full mesh still has strict Failed3 (D909, W100, R2; N755 warnings).

## Finite search and conclusion

The search used the true tetrahedral immediate face-neighbour stars of the two actual low-volume-ratio faces, followed by exactly the first 16 actual poor-D tetrahedra in determinant/rank/cell order. One D seed duplicates an R-star seed; pairs are deduplicated. The search did not expand after rejection.

There were 51 distinct 2-to-3 pairs: 44 had no exact strictly interior new-edge intersection and 7 entered complete actual stock metric and role evaluation. The 144 3-to-2 edge visits are not a unique global edge count: 136 were not closed three-cell rings and 8 had a non-tetrahedral member. There was no qualified 3-to-2 cavity. No qualified positive was found.

All 7 evaluated cavities conserve exact rational volume and first moment, have positive exact new volumes, and preserve the 6 exterior original triangular outlines and patch/zone/source-base/outside identity. The independent exact boundary-halfspace check contains every original and candidate cavity node. Re-reading the actual binary/source arrays verified 55 complete cell records, 172 face records, and 76 point records across the seven compacts. These are totals with overlap between compacts, not unique whole-mesh counts.

Every evaluated candidate fails absolute stock five metrics and inherited passing-role protection. The strongest partial improvement, rank3 [528945,528986], raises Dmin from 4.693706e-7 to 0.0002337513 and reduces maximum nonorthogonality from 87.2988 to 67.5936 degrees, but still misses D>=0.001 and W>=0.05 (Wmin=0.04705163), and regresses one passing D cell and two passing W facets. It is rejected.

The R-neighbourhood rank1 [504266,508831] cavity passes geometry and leaves every complete outside CC/V bit-exact, but its proposed Dmin is 1.551972e-8 and Nmax is 85.7308 degrees. Its passing D/W/N roles regress. Therefore refusal does not rest on a conservative outside arithmetic screen.

## Coordinates, orientation, and role scope

The candidate uses only original native point IDs and coordinates. All changed cavity and complete touched outside cells are screened against coupled-owned cells and protected source zones. Hypothetical owner/neighbour renumbering retains point0 and reverses only the tail when canonical owner ordering requires a direction reversal. The result records expected oriented rings, rather than falsely describing a reversed oriented ring as unchanged.

The extra complete-outside canonical DP equality screen is a conservative sufficient test. Some candidates have a 1-ULP outside moment difference under face orientation/canonical traversal; that screen is not a theorem that all legal flips are impossible. Their five-metric and passing-role failures are separately stored. No remote coupled quality was invented.

result.json contains complete before/candidate stencils, outside cells, source lineage, all actual cell and face metrics, exact partition data, and boundary maps. Raw Python JSON may encode infinite derived severity for zero candidate weight; these entries are refused. summary.json and receipt.json are strict finite JSON.

## Audit correction and tests

The first attempt used compact maximum labels for virtual cells and could alias a real third-party neighbour referenced by a complete outside facet. Its source/results are retained as attempt1-*, explicitly invalid for numerical conclusions; the actual mesh was never changed. The corrected tool allocates virtual labels above the actual rank nCells and nFaces, with assertions against all original references. The same finite seeds/pairs were recomputed, without search expansion.

The two search passes total 4.71159713389352 seconds, below the 60-second search deadline. Preparation, original-array re-read, tests, and evidence freezing are separate audit work and are not included in the search elapsed field.

Four semantic tests passed: actual hidden-neighbour label alias prevention, exact 2-to-3-to-2 bipyramid exterior/volume/moment roundtrip, inheritance of every original passing cell and deleted-facet role, and rejection of a new edge that hits the shared triangle boundary. The original test output is retained.

## Immutable source binding

The source-reader copies and original SHA bindings are in readers/ and reader-sha256.json. exact-containment-and-actual-source-verification.json binds the actual written binary and source trace arrays. The prior 7785 full proof manifest remains byte-exact at SHA b07a5ba3b0e377d76ecbca9e0a5c7cd1da708c48ecf97b43c05a43d5ba32ca22. No prior receipt, native source, actual mesh, API, service, Git index, or browser was changed by this diagnostic.
