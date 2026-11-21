from pathlib import Path
import json,copy,importlib.util
import pytest
here=Path(__file__).parent
spec=importlib.util.spec_from_file_location("two_lobe_real_audit",here/"audit_two_lobe.py");M=importlib.util.module_from_spec(spec);spec.loader.exec_module(M)
new=json.loads((here/"candidate-best.json").read_text())
def refuse(change,pattern):
 d=copy.deepcopy(new);change(d)
 with pytest.raises(AssertionError,match=pattern):M.certify(d)
def test_fully_exact_positive_two_lobe_shape():
 r=M.certify(new);assert r["FractionWholeVolumeAndFirstMomentExact"]and r["all69OutsideFractionShapesExact"]
 assert len(r["literal38OuterOrderedRingsPoint0RolesAndOutside"])==38 and len(r["strictFractionSubkernel"])==40
 assert all(r["apexContainedInOriginalCells"].values())
def test_actual_stock_hard_success_and_cheby_negative():
 a=json.loads((here/"installed-two-lobe-audit.json").read_text())
 p=a["reports"]["maxmin"];r=a["reports"]["chebyshev"]
 assert p["qualifiedLocalInstalledHardCandidate"] and not r["qualifiedLocalInstalledHardCandidate"]
 assert len(r["inheritedPassingDRefusals"])==1 and len(r["metrics"]["D"]["newBad"])==1
 assert all(not p["metrics"][k]["newBad"]for k in["D","weight","ratio","skew"])
 assert len(p["metrics"]["D"]["oldBad"])==5 and p["newCoreDmin"]>.0015
 assert p["allReplacedPassingOriginCount"]==27 and not p["inheritedPassingDRefusals"]
def test_stock_concavity_and_tets_not_bypassed():
 p=json.loads((here/"installed-two-lobe-audit.json").read_text())["reports"]["maxmin"]
 assert p["actualStockConcavityAnyCompleteCell"]==[False,False]
 assert p["minPyramidV"]>0 and p["maxRelativeClosure"]<1e-10
 assert p["minCentreFanTetQuality"]>1e-30 and p["minSharedAnchorTetQuality"]>1e-30
def test_warning_count_and_max_reported_not_hidden():
 p=json.loads((here/"installed-two-lobe-audit.json").read_text())["reports"]["maxmin"]["metrics"]["N"]
 assert len(p["oldBad"])==4 and len(p["newBad"])==1
 assert p["newMax"]<p["oldMax"] and p["newMax"]>70
def test_original_interior_point_coordinate_change_refused():
 refuse(lambda d:d["points"]["28144"].__setitem__(0,1.0),"original-point-coordinate")
def test_physical_vertex_motion_refused():
 refuse(lambda d:d["points"]["61112"].__setitem__(0,1.6),"original-point-coordinate")
def test_outer_patch_role_change_refused():
 refuse(lambda d:d["faces"]["2217059"]["patch"].update(name="different"),"outer-role")
def test_outer_source_role_change_refused():
 refuse(lambda d:d["faces"]["2217059"].update(sourceZone=-1),"outer-role")
def test_outer_point0_rotation_refused():
 refuse(lambda d:d["faces"]["2217059"].__setitem__("pointIDs",d["faces"]["2217059"]["pointIDs"][1:]+d["faces"]["2217059"]["pointIDs"][:1]),"outer-ring-or-point0")
def test_outside_endpoint_changed_refused():
 def change(d):
  f=d["faces"]["1577322"];f["neighbour"]=f["owner"] if f["neighbour"]is None else 538671
 refuse(change,"incomplete-cell-incidence|outside-endpoint")
def test_incomplete_cell_shape_refused():
 refuse(lambda d:d["cells"][str(d["children"][0])]["faces"].pop(),"incomplete-cell-incidence")
def test_extra_apex_alias_refused():
 refuse(lambda d:d["points"].__setitem__("591826",d["points"]["591824"]),"new-point-identity")
def test_old_point_in_new_face_refused():
 refuse(lambda d:d["faces"]["2251327"]["pointIDs"].__setitem__(2,28142),"old-interior-points-still-used")
def test_separator_ring_not_replaced_by_other_quad():
 refuse(lambda d:d["faces"]["2251326"]["pointIDs"].reverse(),"separator-ring")
def test_separator_cannot_be_physical_boundary():
 refuse(lambda d:d["faces"]["2251326"].update(patch={"name":"wall","type":"wall"}),"separator-role")
def test_separator_core_lobe_binding_refused():
 refuse(lambda d:d["cells"][str(d["faces"]["2251326"]["owner"])].update(lobe=1),"separator-endpoint")
def test_left_apex_crossing_seam_refused():
 refuse(lambda d:d["points"].__setitem__("591824",[1.6,2.827,3.78]),"apex-wrong-lobe")
def test_apex_not_in_subkernel_refused():
 refuse(lambda d:d["points"].__setitem__("591825",[1.59,3.,3.78]),"strict-subkernel")
def test_binary_roundtrip_and_fullrank_original_clearance():
 p=json.loads((here/"binary64-and-global-point-clearance.json").read_text())
 assert p["binaryRoundtripDPExact"]and p["allStoredOriginalPointsAgainstP12DPExact"]
 assert p["fullOriginalPoints"]["count"]==591824
 assert all(r["distanceM"]>3e-6 for r in p["newPointNearestOriginal"])and p["newPairDistanceM"]>3e-6
def test_one_finite_proposal_bounded_no_extra_pool():
 p=json.loads((here/"finite-two-lobe-search.json").read_text())
 assert p["evaluations"]==49 and p["seconds"]<65 and p["stop"]["success"]
 assert p["initial"]["counts"]["D"]==1 and p["best"]["counts"]["D"]==0
def test_stored_reader_moments_do_not_replace_actual_oracle():
 p=json.loads((here/"typed-baseline-vs-stored-moments.json").read_text())
 assert p["completeCells"]==101 and p["differences"]

def test_authoritative_metric_endpoints_have_complete_shapes():
 old=json.loads((here/"complete-star-input.json").read_text())
 for doc in [old,new]:
  complete=set(map(int,doc["cells"]))
  for f in doc["firstHaloMetricFaces"]:
   row=doc["faces"][str(f)]
   assert row["owner"]in complete
   assert row["neighbour"]is None or row["neighbour"]in complete
def test_no_coupled_face_or_hidden_remote_metric():
 assert all(not r["patch"]or("cyclic"not in r["patch"]["type"].lower()and"processor"not in r["patch"]["type"].lower())for r in new["faces"].values())

def verify_exact_pair_separation(doc,cert):
 from fractions import Fraction as F
 from itertools import combinations
 import hashlib
 assert cert["fixedInputSha256"]["candidate-best.json"]==hashlib.sha256((here/"candidate-best.json").read_bytes()).hexdigest()
 vertices={int(c):sorted(set(p for f in r["faces"]for p in doc["faces"][str(f)]["pointIDs"]))for c,r in doc["cells"].items()if int(c)in doc["children"]}
 expected=set(combinations(sorted(doc["children"]),2));seen=set()
 for r in cert["pairSeparationCertificates"]:
  pair=tuple(sorted(r["cells"]));assert pair in expected and pair not in seen
  seen.add(pair);axis=list(map(F,r["axis"]));assert any(axis),"zero-separation-axis"
  a,b=r["lowerCone"],r["upperCone"];assert {a,b}==set(pair)
  lower=max(sum(axis[j]*F(doc["points"][str(p)][j])for j in range(3))for p in vertices[a])
  upper=min(sum(axis[j]*F(doc["points"][str(p)][j])for j in range(3))for p in vertices[b])
  assert lower<=upper,"overlapping-projection"
  assert lower==F(int(r["maxLower"]["numerator"]),int(r["maxLower"]["denominator"]))
  assert upper==F(int(r["minUpper"]["numerator"]),int(r["minUpper"]["denominator"]))
 assert seen==expected,"incomplete-pair-coverage"
def test_independent_pairwise_nonoverlap_rechecked_against_actual_points():
 cert=json.loads((here/"independent/two-lobe-candidate-independent-20261006/candidate-exact-geometry-certificate.json").read_text())
 verify_exact_pair_separation(new,cert)
 assert cert["pairSeparationSummary"]=={"totalPairs":780,"facePlaneSeparated":776,"edgeCrossSATSeparated":4,"unresolvedPairs":[]}
def test_nonoverlap_certificate_zero_axis_tamper_refused():
 cert=json.loads((here/"independent/two-lobe-candidate-independent-20261006/candidate-exact-geometry-certificate.json").read_text())
 cert["pairSeparationCertificates"][0]["axis"]=[0,0,0]
 with pytest.raises(AssertionError,match="zero-separation-axis"):verify_exact_pair_separation(new,cert)
def test_nonoverlap_certificate_missing_pair_refused():
 cert=json.loads((here/"independent/two-lobe-candidate-independent-20261006/candidate-exact-geometry-certificate.json").read_text())
 cert["pairSeparationCertificates"].pop()
 with pytest.raises(AssertionError,match="incomplete-pair-coverage"):verify_exact_pair_separation(new,cert)
