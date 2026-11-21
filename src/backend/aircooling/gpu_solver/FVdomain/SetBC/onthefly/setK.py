"""Boundary conditions for turbulent kinetic energy k (primal variable).

Implementation lives in setTurbScalar.py (one implementation, two field
bindings); k carries no value transform -- turbulence design section 2.1
keeps k primal.
"""

from FVdomain.SetBC.onthefly.BCbase import VARType
from FVdomain.SetBC.onthefly.setTurbScalar import make_scalar_bc_classes

# wall_value 0.0: k = 0 exactly at a no-slip wall (low-Re walls, design
# section 2.4) -- bc tomls say ``type = "wall"``.
_classes = make_scalar_bc_classes("k", VARType.TURB_K, value_transform=None,
                                  wall_value=0.0)

Fixedvalue = _classes["Fixedvalue"]
Zerogradient = _classes["Zerogradient"]
Cyclic = _classes["Cyclic"]
Empty = _classes["Empty"]
Calculated = _classes["Calculated"]
Symmetry = _classes["Symmetry"]
Wall = _classes["Wall"]


class Kqrwallfunction(Zerogradient):
    """``type = "kqRWallFunction"`` -- the HIGH-Re k wall condition.

    Not a wall function despite the name, and v2412 says so itself:
    ``kqRWallFunctionFvPatchField`` derives from
    ``zeroGradientFvPatchField<Type>``
    (kqRWallFunctionFvPatchField.H:81-85), forwards ``evaluate`` straight to
    it (kqRWallFunctionFvPatchField.C:92-99), overrides neither
    ``updateCoeffs`` nor ``manipulateMatrix``, and its own documentation reads
    "a simple wrapper around the zero-gradient condition ... It is not a
    wall-function condition" (ibid.H:34-38).

    The distinction that matters is against ``Wall`` above: the low-Re route
    pins ``k = 0`` at the wall, the high-Re route leaves the wall-adjacent k
    equation standing with NO diffusive flux through the wall face (snGrad k =
    0) and no convective flux (phi = 0 at a no-slip wall, so its budget is the
    wall-function production against dissipation plus interior diffusion --
    the log-layer equilibrium assumption).  Aliasing the two would delete a
    real equation's boundary term, so this exists as a distinct spelling and
    is implemented as ``Zerogradient``.
    """
