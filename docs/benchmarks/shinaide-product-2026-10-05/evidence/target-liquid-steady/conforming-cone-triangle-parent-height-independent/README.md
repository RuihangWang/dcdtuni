# Frozen N2 triangle parent-height necessary bounds
The actual eight-rank nonorth-sequential mesh has 1,086 D-bad cells. This audit classifies 881 selected-mask tetrahedra (482 with three selected physical-side faces, 399 with all four selected faces), proving the unique new apex common to all written children of each source parent. The remaining 205 cells are non-tet/unsupported by this tool.

It applies the previously proved planar cone determinant envelope only when original source vertices/faces give an exact outward cap plane or a closed convex source shell. The maximum original-vertex inward normal height is not automatically used for an arbitrary concave/warped parent. All 332 unsupported cases remain explicitly unsupported. There are 549 certified finite heights, of which 178 are below the free-height optimum.

The triangle formulas are the n=3 specializations of audit_air_pyramid_remaining_bound.py: physical selected-side coefficient 54 and all-four coefficient 128. A finite height may restrict the optimum further; necessary upper bounds above .001 do not prove parent feasibility.

Two actual all-four triangle cones newly have upper bounds below .001 even though their free any-apex bounds pass:
- rank1 c462654, original parent652920/sourceface103114 n5: original cap faces2084625/2084626 certify H=1.837056732177711mm; D upper .00040327906732606187 (free .08015858596854109).
- rank1 c462655, original parent652956/sourceface103965 n5: cap2084661/2084662, same H; D upper .0003370809960157395 (free .07026074191794174).

They are the already identified thin original parents, supporting an outline-preserving refan or conforming parent split. This proves only impossibility of those fixed triangle tetrahedra with an apex inside the certified original kernel. It does not prove arbitrary polyhedral repair or the whole target impossible.

Actual bad-set, all mesh/trace and point-native identities are in the JSON. Original coordinates and original cap planes remain separate from final written apex coordinates. No old cell labels are reused against the later thin-split topology.

Reproduce tools/audit_air_triangle_parent_height.py --mesh <frozen N2 Mesh> --output <repo JSON>. Five tests passed in .22s (CPU36–39): thin cap excluding a freely feasible triangle for both masks, free-height limit reducing to54/128, unsupported mask/arity/negativeheight refusal. No native/API/browser/CPP or geometry/threshold mutation.
