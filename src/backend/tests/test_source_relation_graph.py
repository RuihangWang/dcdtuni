"""Explicit source relations are deterministic declarations, never geometry proof."""
from copy import deepcopy

import pytest

from backend.aircooling.runner.source_relation_graph import validate_declared_source_relations


FACES = {
    'a:wall_a': {'sourceFaceUid': 'project/wall_a/front', 'representation': 'zeroThicknessSheet'},
    'b:wall_b': {'sourceFaceUid': 'project/wall_b/back', 'representation': 'zeroThicknessSheet'},
    'c:floor': {'sourceFaceUid': 'project/floor/source-sheet', 'representation': 'zeroThicknessSheet'},
}
CONTOURS = {'project/wall_a/front/edge/bottom': 'project/wall_a/front'}


def topology():
    return {
        'version': 1, 'mode': 'strict', 'coverage': 'airInternalSheets',
        'wallPairs': {'wall_one': {
            'sides': ['project/wall_a/front', 'project/wall_b/back'],
            'thickness_m': 0.02, 'meshReferenceFaceUid': 'project/wall_a/front',
            'meshTreatment': 'collapsedAirBarrier',
        }},
        'independentPairs': {},
        'edgeContacts': {'seam': {
            'kind': 'edgeFace',
            'edge': {'sourceFaceUid': 'project/wall_a/front',
                     'sourceEdgeUid': 'project/wall_a/front/edge/bottom'},
            'face': {'sourceFaceUid': 'project/floor/source-sheet'},
        }},
    }


def test_relation_digest_ignores_object_order_but_remains_unverified():
    first = validate_declared_source_relations(topology(), FACES,
        contours=CONTOURS)
    reordered = topology()
    reordered['wallPairs']['wall_one']['sides'].reverse()
    assert first.declaration_sha256 == validate_declared_source_relations(reordered, dict(reversed(list(FACES.items()))),
        contours=CONTOURS).declaration_sha256
    assert first.status == 'unverified_geometry'
    assert first.face_uids == ('project/floor/source-sheet', 'project/wall_a/front', 'project/wall_b/back')


@pytest.mark.parametrize('change,pattern', [
    (lambda x: x['wallPairs']['wall_one']['sides'].__setitem__(1, 'missing/face'), 'missing/face'),
    (lambda x: x['wallPairs']['wall_one'].__setitem__('thickness_m', 0), 'thickness_m'),
    (lambda x: x['wallPairs']['wall_one'].__setitem__('meshReferenceFaceUid', 'project/floor/source-sheet'), 'meshReferenceFaceUid'),
    (lambda x: x.__setitem__('invented', True), 'unknown'),
])
def test_rejects_unresolved_or_invalid_wall_declaration(change, pattern):
    data = topology()
    change(data)
    with pytest.raises(ValueError, match=pattern):
        validate_declared_source_relations(data, FACES, contours=CONTOURS)


def test_rejects_duplicate_wall_side_and_conflicting_gap():
    data = topology()
    data['wallPairs']['wall_two'] = deepcopy(data['wallPairs']['wall_one'])
    with pytest.raises(ValueError, match='already.*wallPairs|duplicate'):
        validate_declared_source_relations(data, FACES, contours=CONTOURS)
    data = topology()
    data['independentPairs']['gap'] = {
        'faces': ['project/wall_a/front', 'project/wall_b/back'],
        'relation': 'fluidGap', 'minGap_m': 0.01,
    }
    with pytest.raises(ValueError, match='conflict|wallPairs'):
        validate_declared_source_relations(data, FACES, contours=CONTOURS)
    data['independentPairs']['gap']['faces'] = ['project/wall_a/front', 'project/floor/source-sheet']
    assert validate_declared_source_relations(data, FACES, contours=CONTOURS).status == 'unverified_geometry'


def test_contact_needs_a_known_contour_and_geometry_is_never_certified():
    with pytest.raises(ValueError, match='sourceEdgeUid|contour'):
        validate_declared_source_relations(topology(), FACES, contours={})
    with pytest.raises(ValueError, match='belongs to another'):
        validate_declared_source_relations(topology(), FACES,
            contours={'project/wall_a/front/edge/bottom': 'project/floor/source-sheet'})
    self_contact = topology()
    self_contact['edgeContacts']['seam']['face']['sourceFaceUid'] = 'project/wall_a/front'
    with pytest.raises(ValueError, match='distinct source faces'):
        validate_declared_source_relations(self_contact, FACES, contours=CONTOURS)
    unindexed = validate_declared_source_relations(topology(), FACES)
    assert unindexed.status == 'unverified_geometry'
    assert unindexed.missing_contour_registry is True


def test_absent_topology_creates_no_explicit_graph():
    assert validate_declared_source_relations(None, FACES) is None
