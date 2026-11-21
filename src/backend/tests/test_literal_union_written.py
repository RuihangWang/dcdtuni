"""Semantic regression for frozen literal-union evidence; no native solver."""
import copy
import gzip
import importlib.util
import json
from pathlib import Path
import sys

import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/"tools"))
import audit_air_literal_union_written as audit
import audit_air_cone_small_union_independent as union
E=ROOT/"docs/benchmarks/shinaide-product-2026-10-05/evidence/target-liquid-steady/literal-union-written-independent"

def frozen(rank):
    return json.loads(gzip.decompress((E/f"rank{rank}-baseline-native-stencil.json.gz").read_bytes()))

def test_two_original_bad_roles_survive_accounting():
    # A literal replacement can remove two bad cells. Raw cardinality and the
    # set of surviving projected roles are different measurements.
    result=json.loads((E/"result.json").read_text())
    assert result["rawStockSetCounts"]["D"]=={
      "baselineRawCount":1147,"baselineBadRolesMappedToSurvivorCount":1146,"finalCount":1111}
    rank=json.loads((E/"rank3-audit.json").read_text())
    record=next(x for x in rank["sequentialAcceptedReconstruction"]["steps"]
                if x["acceptedRecord"]["keep"]==661502)
    assert record["acceptedRecord"]["drop"]==661513
    assert [x["D"]<.001 for x in record["oldCompleteAffectedCells"] if x["cell"] in (661502,661513)]==[True,True]
    assert record["mergedShellActualFiveMetrics"]["D"]>=.001
    assert not record["passingRoleFailures"]

def test_original_two_tets_recovered_without_recorded_determinants():
    doc=frozen(3);group=[661502,661513]
    merged,removed,outer=union.union_document(doc,group)
    solutions=audit.unique_tets(doc,outer)
    assert len(solutions)==1
    shared=set(doc["faces"][str(removed[0])]["pointIDs"])
    assert set(solutions[0]["sharedTriangle"])==shared
    originals={frozenset(p for f in doc["cells"][str(c)]["faces"]
                          for p in doc["faces"][str(f)]["pointIDs"]) for c in group}
    assert originals=={frozenset(solutions[0]["tetA"]),frozenset(solutions[0]["tetB"])}

def test_broken_outline_does_not_reconstruct_two_tets():
    doc=frozen(3);_,_,outer=union.union_document(doc,[661502,661513])
    broken=copy.deepcopy(doc)
    face=str(outer[0]);ids=broken["faces"][face]["pointIDs"]
    ids[1]=ids[0]
    assert audit.unique_tets(broken,outer)==[]

def test_cyclic_start_is_geometry_preserving_but_not_point_zero_identity():
    doc=frozen(3);merged,_,outer=union.union_document(doc,[661502,661513])
    merged,_=union.canonicalize_internal_roles(merged)
    before=union.WrittenStencil(merged,merged["originalApex"])
    rotated=copy.deepcopy(merged)
    for face in outer:
        row=rotated["faces"][str(face)];ids=row["pointIDs"]
        row["pointIDs"]=ids[1:]+ids[:1]
    after=union.WrittenStencil(rotated,rotated["originalApex"])
    assert merged["faces"]!=rotated["faces"]
    a=audit.strict_cell(before,661502);b=audit.strict_cell(after,661502)
    assert a["strictGeometryPass"] and b["strictGeometryPass"]
    np.testing.assert_allclose(a["CC"],b["CC"],rtol=0,atol=2e-15)
    assert abs(a["V"]-b["V"])/a["V"]<1e-12
    assert abs(a["D"]-b["D"])<1e-12

def test_missing_oriented_face_is_rejected():
    doc=frozen(3);merged,_,outer=union.union_document(doc,[661502,661513])
    merged,_=union.canonicalize_internal_roles(merged)
    merged["cells"]["661502"]["faces"].remove(outer[0])
    row=audit.strict_cell(union.WrittenStencil(merged,merged["originalApex"]),661502)
    assert not row["strictGeometryPass"]

@pytest.mark.parametrize("metric",list(audit.LIMITS))
def test_nonfinite_quality_never_passes(metric):
    assert not audit.metric_pass(float("nan"),metric)
    assert not audit.metric_pass(float("inf"),metric)

def test_local_success_does_not_replace_whole_stock_gate():
    result=json.loads((E/"result.json").read_text())
    assert result["acceptedCount"]==35
    assert result["allAcceptedGuardsIndependentlyPass"]
    assert result["allStockSetsHaveNoNewNativeFailedRoles"]
    assert result["rawStockSetCounts"]["D"]["finalCount"]>0
    assert result["rawStockSetCounts"]["W"]["finalCount"]>0
    assert result["rawStockSetCounts"]["R"]["finalCount"]>0

def _fake_boundary_geo(path,name="port",kind="patch",extra=""):
    from types import SimpleNamespace
    path.mkdir()
    (path/"boundary").write_text(
      "FoamFile\n{\n version 2.0;\n format ascii;\n}\n1\n(\n"+name+
      "\n{\n type "+kind+";\n nFaces 1;\n startFace 0;\n "+extra+"\n}\n)\n")
    return SimpleNamespace(poly_dir=path,n_faces=1,neighbour=np.empty(0,dtype=np.int64))

@pytest.mark.parametrize("name,kind",[("wrongPhysicalPort","patch"),("port","wall")])
def test_actual_written_patch_name_and_type_tamper_rejected(tmp_path,name,kind):
    old=_fake_boundary_geo(tmp_path/"old")
    new=_fake_boundary_geo(tmp_path/"new",name,kind)
    with pytest.raises(ValueError,match="name/type/reference"):
        audit.validate_patch_roles(old,new,np.array([0]))

def test_coupled_reference_semantics_tamper_rejected(tmp_path):
    old=_fake_boundary_geo(tmp_path/"old","side","cyclic","neighbourPatch opposite; transform translational;")
    new=_fake_boundary_geo(tmp_path/"new","side","cyclic","neighbourPatch wrong; transform translational;")
    with pytest.raises(ValueError,match="name/type/reference"):
        audit.validate_patch_roles(old,new,np.array([0]))

def test_nonempty_actual_zone_requires_native_membership_proof(tmp_path):
    p=tmp_path/"faceZones"
    p.write_text("FoamFile\n{ version 2.0; format ascii; }\n1\n(\nprotected\n{\n type faceZone;\n faceLabels List<label> 1; flipMap List<bool> 0;\n}\n)\n")
    with pytest.raises(ValueError,match="Nonempty/unsupported"):
        audit.empty_zone_memberships(p)

def test_protected_source_zone_cannot_enter_literal_shell():
    doc=frozen(3);_,removed,outer=union.union_document(doc,[661502,661513])
    doc["faces"][str(outer[0])]["sourceZone"]=0
    with pytest.raises(ValueError,match="source-zone protection"):
        audit.source_role_protection(doc,removed,outer,{661502,661513})

@pytest.mark.parametrize("ids",[np.array([1,1]),np.array([-1,2]),np.array([1.,2.])])
def test_native_alias_negative_and_noninteger_maps_rejected(ids):
    with pytest.raises(ValueError,match="unique integers"):
        audit.inverse(ids)

def test_declared_stock_set_length_mismatch_rejected(tmp_path):
    p=tmp_path/"underdeterminedCells"
    p.write_text("FoamFile\n{ format binary; class cellSet; }\n2\n(\n1\n)\n")
    with pytest.raises(ValueError,match="Set size mismatch"):
        audit.g.cell_set(p)
