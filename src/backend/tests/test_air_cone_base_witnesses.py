from pathlib import Path
import sys
import numpy as np
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[3]/"tools"))
from audit_air_cone_base_witnesses import coordinate_key, split_guard

def test_cyclic_and_reverse_winding_preserve_exact_identity():
    x=np.array([[0,0,0],[1,0,0],[1,1,0],[0,1,0]],float)
    assert coordinate_key(x)==coordinate_key(np.roll(x,2,axis=0))
    assert coordinate_key(x)==coordinate_key(x[::-1])

def test_one_ulp_coordinate_difference_changes_identity():
    x=np.array([[0,0,0],[1,0,0],[1,1,0],[0,1,0]],float)
    y=x.copy();y[1,0]=np.nextafter(1.,2.)
    assert coordinate_key(x)!=coordinate_key(y)

def test_split_decision_detects_collinear_midpoint_and_concavity():
    assert not split_guard([[0,0,0],[1,0,0],[1,1,0],[0,1,0]])["splitRequired"]
    assert split_guard([[0,0,0],[.5,0,0],[1,0,0],[1,1,0],[0,1,0]])["collinearVertex"]
    assert not split_guard([[0,0,0],[1,0,0],[.4,.4,0],[1,1,0],[0,1,0]])["projectedConvex"]

def test_nonfinite_coordinate_cannot_be_identity():
    with pytest.raises(ValueError):
        coordinate_key([[0,0,0],[float("nan"),0,0],[0,1,0]])
