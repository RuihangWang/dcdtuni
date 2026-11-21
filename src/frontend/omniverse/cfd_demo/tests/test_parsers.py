import os, sys, json
HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.abspath(os.path.join(
    HERE, "..", "kit", "extensions", "dc.cfd_bridge")))

from dc.cfd_bridge import parsers as p

def test_parse_json_bad():
    assert p.parse_json("") == {}
    assert p.parse_json("not json") == {}
    assert p.parse_json('{"a":1}') == {"a": 1}

def test_parse_set_volume():
    assert p.parse_set_volume(json.dumps({"field": "U", "visible": False})) == ("U", False)
    assert p.parse_set_volume(json.dumps({})) == ("T", True)
    # unknown field falls back to T
    assert p.parse_set_volume(json.dumps({"field": "x"})) == ("T", True)

def test_parse_toggles():
    assert p.parse_set_roi_visible(json.dumps({"visible": True})) is True
    assert p.parse_set_roi_visible(json.dumps({"visible": False})) is False
    assert p.parse_set_roi_clip(json.dumps({"enabled": True})) is True

def test_parse_range():
    assert p.parse_set_volume_range(json.dumps({"min": 290.0, "max": 305.5})) == (290.0, 305.5)

def test_parse_roi_box():
    msg = json.dumps({"min": [0, 1, 2], "max": [3, 4, 5]})
    assert p.parse_set_roi_box(msg) == ([0.0, 1.0, 2.0], [3.0, 4.0, 5.0])
