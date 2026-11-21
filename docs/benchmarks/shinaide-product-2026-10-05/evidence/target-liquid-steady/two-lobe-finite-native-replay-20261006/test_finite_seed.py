from pathlib import Path
from fractions import Fraction as F
import json,copy,itertools,pytest
HERE=Path(__file__).parent
source=(HERE/"audit_finite_seed.py").read_text().split("newdir=Path(__file__).parent")[0]
NS={};exec(source,NS)
DOC=json.loads((HERE/"finite-seed-template.json").read_text())
CERT=json.loads((HERE/"exact-geometry/candidate-exact-geometry-certificate.json").read_text())
def check(d):return NS["certify_complete_roles"](d)
def test_exact_candidate_full_fixed_shell_and_conservation():
 r=check(DOC)
 assert r["FractionWholeVolumeAndFirstMomentExact"]and r["all69OutsideFractionShapesExact"]
def test_physical_patch_role_tamper_refused():
 d=copy.deepcopy(DOC);f=next(k for k,v in d["faces"].items()if v["patch"]is not None)
 d["faces"][f]["patch"]["type"]="cyclic"
 with pytest.raises(AssertionError):check(d)
def test_source_zone_tamper_refused():
 d=copy.deepcopy(DOC);f=str(NS["top"]["vertexStarExteriorFaces"][0]);d["faces"][f]["sourceZone"]=99
 with pytest.raises(AssertionError):check(d)
def test_source_base_tamper_refused():
 d=copy.deepcopy(DOC);f=str(NS["top"]["vertexStarExteriorFaces"][0]);d["faces"][f]["sourceBase"]=-234
 with pytest.raises(AssertionError):check(d)
def test_outside_identity_tamper_refused():
 d=copy.deepcopy(DOC);f=str(NS["top"]["vertexStarExteriorFaces"][0]);r=d["faces"][f]
 r["neighbour" if r["owner"]in set(d["children"])else "owner"]=123456789
 with pytest.raises(AssertionError):check(d)
def test_original_coordinate_tamper_refused():
 d=copy.deepcopy(DOC);q=str(next(iter(NS["old"]["points"])));d["points"][q][0]+=1e-8
 with pytest.raises(AssertionError):check(d)
def test_separator_wrong_role_refused():
 d=copy.deepcopy(DOC);d["faces"]["2251326"]["patch"]={"name":"fake","type":"wall"}
 with pytest.raises(AssertionError):check(d)
def test_cell_incomplete_shape_refused():
 d=copy.deepcopy(DOC);c=str(d["children"][0]);d["cells"][c]["faces"].pop()
 with pytest.raises(AssertionError):check(d)
def test_apex_outside_own_lobe_refused():
 d=copy.deepcopy(DOC);q=str(d["lobeApexWrittenPoints"][0]);d["points"][q][0]=2
 with pytest.raises(AssertionError):check(d)
def test_nonfinite_apex_refused():
 d=copy.deepcopy(DOC);q=str(d["lobeApexWrittenPoints"][0]);d["points"][q][0]=float("nan")
 with pytest.raises((ValueError,OverflowError,AssertionError)):check(d)
def prove_pairs(rows):
 expected=set(itertools.combinations(sorted(DOC["children"]),2))
 if {tuple(x["cells"])for x in rows}!=expected or len(rows)!=len(expected):return False
 points={int(k):[F(v)for v in xyz]for k,xyz in DOC["points"].items()}
 cells={r["cell"]:r["vertices"]for r in CERT["cellHalfspaceCertificates"]}
 for row in rows:
  axis=row["axis"]
  if len(axis)!=3 or not any(axis):return False
  projection=lambda c:[sum(a*p for a,p in zip(axis,points[q]))for q in cells[c]]
  if max(projection(row["lowerCone"]))>min(projection(row["upperCone"])):return False
 return True
def test_all_780_original_node_pairs_have_exact_separation():
 assert prove_pairs(CERT["pairSeparationCertificates"])
def test_missing_pair_certificate_refused():
 assert not prove_pairs(CERT["pairSeparationCertificates"][:-1])
def test_zero_axis_certificate_refused():
 rows=copy.deepcopy(CERT["pairSeparationCertificates"]);rows[0]["axis"]=[0,0,0]
 assert not prove_pairs(rows)
