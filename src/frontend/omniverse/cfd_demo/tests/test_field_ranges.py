import os, json, sys
import pytest

HERE = os.path.dirname(__file__)
VTU = os.path.abspath(os.path.join(HERE, "..", "data", "internal.vtu"))
MOD = os.path.abspath(os.path.join(HERE, "..", "scripts"))

@pytest.mark.skipif(not os.path.exists(VTU), reason="internal.vtu not generated yet")
def test_field_ranges_T_and_Umag():
    sys.path.insert(0, MOD)
    from field_ranges import field_ranges
    r = field_ranges(VTU)
    assert set(r) == {"T", "Umag"}
    tmin, tmax = r["T"];   assert tmin < tmax
    umin, umax = r["Umag"]; assert 0.0 <= umin < umax
    # temperature is in Kelvin for a data-center solve
    assert 270.0 < tmin < 330.0 and 270.0 < tmax < 360.0
