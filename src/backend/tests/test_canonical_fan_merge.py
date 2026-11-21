"""Pure-math contracts; no native solver or case mutation."""
import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location("canonical_fan", ROOT / "tools/audit_air_canonical_fan_merge.py")
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)
EVIDENCE = ROOT / "docs/benchmarks/shinaide-product-2026-10-05/evidence/target-liquid-steady/conforming-cone-skinny-unions-independent"


def shape_signature(result, shift=(0., 0., 0.)):
    shift = np.asarray(shift)
    selected = result["selected"]
    return sorted(tuple(tuple(np.asarray(q) - shift) for q in
                        [result["_points"][i] for i in MOD.canonical_cycle(base, result["_points"])])
                  for base in selected["bases"])


@pytest.mark.parametrize("parent", [25147, 25148])
def test_real_front_back_unique_necessary_candidate_matches_preserved_partition(parent):
    result = MOD.audit_context(EVIDENCE / f"parent{parent}-refanFiveToFour-context.json")
    assert result["triangleNecessaryCandidateCount"] == 4
    assert result["withinBaseQualityEligibleCount"] == 1
    assert result["selectedMatchesPriorConstructivePartition"]
    selected = result["selected"]
    assert sorted(selected["groupSizes"]) == [1, 1, 1, 2]
    assert selected["minimumWithinBaseWeight"] == pytest.approx(.09985622435399312, abs=2e-12)
    assert selected["minimumWithinBaseVolumeRatio"] > .1
    assert selected["areaError"] < 1e-15
    assert selected["firstMomentError"] < 1e-15
    assert result["noFullMeshAcceptanceClaim"]


def test_canonical_shape_and_selected_partition_ignore_rotation_reversal_and_rank_labels():
    doc = json.loads((EVIDENCE / "parent25147-refanFiveToFour-context.json").read_text())
    points = {int(k): v for k, v in doc["points"].items()}
    outline = MOD.outline_from_partition(doc["repartition"]["physicalBases"], points)
    baseline = MOD.enumerate_partitions(outline, points)
    baseline["_points"] = points
    expected = shape_signature(baseline)
    for loop in (outline[3:] + outline[:3], outline[::-1]):
        remap = {old: 10000 + 17 * i for i, old in enumerate(reversed(outline))}
        altered_points = {remap[old]: points[old] for old in outline}
        result = MOD.enumerate_partitions([remap[x] for x in loop], altered_points)
        result["_points"] = altered_points
        assert shape_signature(result) == expected
        assert result["withinBaseQualityEligibleCount"] == 1


def test_triangle_bound_rejects_impossible_slender_base_without_claiming_other_shapes_pass():
    bound = MOD.triangle_bound([[0., 0., 0.], [1., 0., 0.], [.5, 1e-4, 0.]])
    assert bound < MOD.DET_MIN
    result = MOD.enumerate_partitions([0, 1, 2], {0: [0., 0., 0.], 1: [1., 0., 0.], 2: [.5, 1e-4, 0.]})
    assert result["selected"] is None
    assert result["noFullMeshAcceptanceClaim"]


@pytest.mark.parametrize("points", [
    {0: [0., 0., 0.], 1: [1., 0., 0.], 2: [1., 1., .01], 3: [0., 1., 0.]},
    {0: [0., 0., 0.], 1: [1., 0., 0.], 2: [.25, .25, 0.], 3: [0., 1., 0.]},
])
def test_nonplanar_or_concave_original_outline_refused(points):
    with pytest.raises(ValueError):
        MOD.enumerate_partitions([0, 1, 2, 3], points)

def test_same_base_weight_and_volume_ratio_are_apex_independent_even_with_outside_projection():
    doc = json.loads((EVIDENCE / "parent25147-refanFiveToFour-context.json").read_text())
    points = {int(k): np.asarray(v) for k, v in doc["points"].items()}
    result = MOD.enumerate_partitions(MOD.outline_from_partition(doc["repartition"]["physicalBases"], points), points)
    chosen = result["selected"]
    for apex in (np.array([2.16, .87, 4.]), np.array([3., 2., 3.]), np.array([-2., -1., 6.])):
        for edge in chosen["interfaces"]:
            a, b = [points[k] for k in edge["edge"]]
            normal = np.cross(b-a, apex-a)
            normal /= np.linalg.norm(normal)
            piece_a, piece_b = edge["pieces"]
            fca, fcb = [np.asarray(chosen["pieceProfiles"][k]["centre"]) for k in (piece_a, piece_b)]
            cca, ccb = (3*fca+apex)/4, (3*fcb+apex)/4
            da, db = abs(float(np.dot(cca-a, normal))), abs(float(np.dot(ccb-a, normal)))
            assert min(da,db)/(da+db) == pytest.approx(edge["weight"], abs=1e-12)
            base_normal = np.cross(points[chosen["bases"][piece_a][1]]-points[chosen["bases"][piece_a][0]],
                                   points[chosen["bases"][piece_a][2]]-points[chosen["bases"][piece_a][0]])
            base_normal /= np.linalg.norm(base_normal)
            h = abs(float(np.dot(apex-a, base_normal)))
            va, vb = [chosen["pieceProfiles"][k]["area"]*h/3 for k in (piece_a, piece_b)]
            assert min(va,vb)/max(va,vb) == pytest.approx(edge["volumeRatio"], abs=1e-12)
