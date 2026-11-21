"""Conservation accounting must never mistake a local pressure solve for global balance."""
import importlib.util
from pathlib import Path
import sys

import pytest
import numpy as np

TOOLS=Path(__file__).resolve().parents[3]/"tools"
sys.path.insert(0,str(TOOLS))
import audit_air_written_flux_conservation as audit


def test_zero_cell_residual_can_hide_processor_mass_creation():
    # Two locally conservative rows, but both copies of their shared face sum
    # to -.25, balanced by a spurious physical outflow excess of +.25.
    row=audit.balance_summary([-1,1.25],[.75,-1],[0,0])
    assert row["allOwnedCellsNetM3s"]==0
    assert row["physicalBoundaryNetM3s"]==.25
    assert row["allCoupledCopiesNetM3s"]==-.25
    assert row["globalAccountingIdentityErrorM3s"]==0


def test_conservative_shared_flux_cancels_without_hiding_cell_errors():
    row=audit.balance_summary([-1,1.00001],[.75,-.75],[.00001,0])
    assert row["allCoupledCopiesNetM3s"]==0
    assert row["physicalBoundaryNetM3s"]==pytest.approx(.00001)
    assert row["allOwnedCellsNetM3s"]==.00001


def test_surface_flux_scalar_payloads_require_exact_count_and_finite_values():
    with pytest.raises(ValueError,match="size does not match mesh"):
        audit.fields.values("value nonuniform List<scalar> 1 ( 1 );", "value",2)
    with pytest.raises(ValueError,match="nonfinite"):
        audit.fields.values("value uniform nan;", "value",1)
