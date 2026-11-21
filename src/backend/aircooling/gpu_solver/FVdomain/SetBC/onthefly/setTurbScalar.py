"""Shared boundary-condition implementation for transported turbulence scalars.

``setK.py`` and ``setOmega.py`` bind the concrete classes from the factory
below -- ONE implementation, two field bindings (house rule: no parallel
implementations).  The pattern is setT.py's: ghost-cell writes through the
existing machinery, keyed on the field's own ``<field>_cpd_cell`` /
``grad_<field>_flux_face`` fvfield entries.

The omega binding carries the ONE thing that distinguishes it (design doc
section 3.1): bc/omega.toml speaks OpenFOAM omega, and the
transform to the stored log variable omega-tilde = ln(omega) happens exactly
at the ghost write -- ``value_transform`` maps the toml value BEFORE the
value tensor is built, so what lands in the ghost row is ln(omega_toml).
Mirror-type conditions (zeroGradient, cyclic, empty) copy stored values and
need no transform: a mirror of omega-tilde IS a mirror of omega.

Phase-3 scope (design section 11 phase 3, wall-free): fixedValue,
zeroGradient, cyclic, empty, calculated.  Phase 4 adds the low-Re
(integrate-to-wall) ``wall`` type: a plain uniform Dirichlet at the field's
wall value (k = 0; Launder-Sharma's eps-tilde = 0 by the 1974 split), which
is all the existing machinery -- design section 2.4 -- with the value bound
by the field module so bc tomls can say ``type = "wall"`` without repeating
the number.  omega's geometry-dependent wall condition lives in setOmega.py.
"""

import math
from typing import Dict

from FVdomain.SetBC.onthefly.Parafunction import Functionbase, ParametricManager
from FVdomain.SetBC.onthefly.BCbase import BoundaryCondition, BCType
from FVdomain.SetBC.onthefly.setCyclic import apply_cyclic_face_values


def log_transform_scalar_value(value: Dict) -> Dict:
    """OpenFOAM-primal scalar value dict -> its log, for a log-stored field.

    Only uniform/constant scalars are supported this phase; a profile-typed
    boundary value would need the transform inside its evaluator, which no
    wall-free MMS case requires -- refused by name rather than silently
    mis-transformed.
    """
    if value.get("value_type") not in ("uniform", "constant"):
        raise ValueError(
            "[setOmega] only uniform/constant boundary values are supported "
            f"for the log-transformed omega field; got {value!r}")
    raw = float(value["val"])
    if raw <= 0.0:
        raise ValueError(
            f"[setOmega] omega boundary value must be > 0 (got {raw}); "
            "bc/omega.toml speaks OpenFOAM omega, whose log is stored")
    return {"value_type": value["value_type"], "val": math.log(raw)}


def make_scalar_bc_classes(field_key: str, var_type, value_transform=None,
                           wall_value=None):
    """Build the BC class set for one transported scalar.

    Args:
        field_key: fvfield key stem, e.g. "k" -> "k_cpd_cell",
            "grad_k_flux_face", "grad_k_cpd_cell".
        var_type: the field's VARType (drives the per-variable snGrad
            boundary treatment and Dirichlet face masks).
        value_transform: optional map applied to a Fixedvalue toml value
            dict before the value tensor is built (the omega -> ln omega
            ghost-write transform).  None = identity (k stays primal).
        wall_value: the field's low-Re wall value IN THE STORED VARIABLE
            (k: 0.0, epsilon-tilde: 0.0), enabling ``type = "wall"`` in the
            bc toml.  None = the field has no uniform wall value and "wall"
            is refused at instantiation (omega, whose wall condition is
            geometry-dependent and lives in setOmega.py).

    Returns:
        dict of class name -> class: Fixedvalue, Zerogradient, Cyclic,
        Empty, Calculated, Symmetry, Wall.
    """
    cell_key = f"{field_key}_cpd_cell"
    grad_flux_key = f"grad_{field_key}_flux_face"
    grad_cell_key = f"grad_{field_key}_cpd_cell"

    class Fixedvalue(BoundaryCondition):
        """Dirichlet ghost write; stored-variable value via value_transform."""

        def __init__(self, patch_name: str, patch_idx: int, boundary_info: Dict,
                     parametric_manager: ParametricManager = None):
            if parametric_manager is not None:
                value = parametric_manager.resolve_value(boundary_info["value"])
            else:
                value = boundary_info["value"]
            self.value = value if value_transform is None else value_transform(value)
            self.value_tensor = None
            super().__init__(patch_name, patch_idx, var_type, BCType.FIRST)

        def init_field(self, phi_cpd_cell, fvpatch, **kwargs):
            start = fvpatch.start_idx_cpd_cell[self.patch_idx]
            end = fvpatch.end_idx_cpd_cell[self.patch_idx]
            self.value_tensor = Functionbase(
                value_type=self.value["value_type"],
                value=self.value["val"],
                num_element=end - start,
                channel_dim=1,
            )
            phi_cpd_cell[..., start:end, 0:1] = self.value_tensor.eval(
                device=phi_cpd_cell.device)
            return phi_cpd_cell

        def eval(self, fvfield, integrator, patch_idx_offset: int, **kwargs):
            global_patch_idx = self.patch_idx + patch_idx_offset
            fvpatch_batch = integrator.fvpatch_batch
            phi = fvfield[cell_key]
            start = fvpatch_batch.start_idx_cpd_cell[global_patch_idx]
            end = fvpatch_batch.end_idx_cpd_cell[global_patch_idx]
            phi[..., start:end, 0:1] = self.value_tensor.eval(device=phi.device)
            fvfield[cell_key] = phi
            return fvfield

    class Zerogradient(BoundaryCondition):
        """Mirror ghost from owner AND zero the boundary gradient flux --
        the hard Neumann enforcement every mixed BC here uses (setT.py
        pattern; no loss penalty)."""

        def __init__(self, patch_name: str, patch_idx: int, boundary_info: Dict,
                     parametric_manager: ParametricManager = None):
            super().__init__(patch_name, patch_idx, var_type, BCType.MIXED)

        def init_field(self, phi_cpd_cell, fvpatch, **kwargs):
            start = fvpatch.start_idx_cpd_cell[self.patch_idx]
            end = fvpatch.end_idx_cpd_cell[self.patch_idx]
            start_f = fvpatch.start_idx_face[self.patch_idx]
            end_f = fvpatch.end_idx_face[self.patch_idx]
            neighbor_cpd_cell = kwargs.get('neighbor_cpd_cell', None)
            if neighbor_cpd_cell is not None:
                owner = neighbor_cpd_cell[0, start_f:end_f]
                phi_cpd_cell[start:end, 0:1] = phi_cpd_cell[owner, 0:1]
            else:
                phi_cpd_cell[start:end, 0:1] = 0.0
            return phi_cpd_cell

        def eval(self, fvfield, integrator, patch_idx_offset: int, **kwargs):
            global_patch_idx = self.patch_idx + patch_idx_offset
            fvpatch_batch = integrator.fvpatch_batch
            neighbor_cpd_cell = integrator.neighbor_cpd_cell
            start_f = fvpatch_batch.start_idx_face[global_patch_idx]
            end_f = fvpatch_batch.end_idx_face[global_patch_idx]

            if cell_key in fvfield:
                phi = fvfield[cell_key]
                owner = neighbor_cpd_cell[0, start_f:end_f]
                ghost = neighbor_cpd_cell[1, start_f:end_f]
                phi[..., ghost, :] = phi[..., owner, :]
                fvfield[cell_key] = phi

            if grad_flux_key in fvfield:
                grad_flux = fvfield[grad_flux_key]
                grad_flux[..., start_f:end_f, :] = 0.0
                fvfield[grad_flux_key] = grad_flux

            return fvfield

    class Cyclic(BoundaryCondition):
        def __init__(self, patch_name: str, patch_idx: int, boundary_info: Dict,
                     parametric_manager: ParametricManager = None):
            super().__init__(patch_name, patch_idx, var_type, BCType.CYCLIC)

        def init_field(self, phi_cpd_cell, fvpatch, **kwargs):
            neighbor_cpd_cell = kwargs.get("neighbor_cpd_cell", None)
            if neighbor_cpd_cell is None:
                raise ValueError("No neighbor_cpd_cell provided")
            return apply_cyclic_face_values(
                phi_cpd_cell, fvpatch, neighbor_cpd_cell, self.patch_idx)

        def eval(self, fvfield, integrator, patch_idx_offset: int, **kwargs):
            global_patch_idx = self.patch_idx + patch_idx_offset
            fvpatch_batch = integrator.fvpatch_batch
            neighbor_cpd_cell = integrator.neighbor_cpd_cell

            if cell_key in fvfield and fvfield[cell_key] is not None:
                fvfield[cell_key] = apply_cyclic_face_values(
                    fvfield[cell_key], fvpatch_batch, neighbor_cpd_cell,
                    global_patch_idx)
            if grad_cell_key in fvfield and fvfield[grad_cell_key] is not None:
                fvfield[grad_cell_key] = apply_cyclic_face_values(
                    fvfield[grad_cell_key], fvpatch_batch, neighbor_cpd_cell,
                    global_patch_idx, gradient=True)
            return fvfield

    class Empty(BoundaryCondition):
        """2D extrusion: copy owner values so GNN message passing stays sane;
        the faces never enter the residual scatter."""

        def __init__(self, patch_name: str, patch_idx: int, boundary_info: Dict,
                     parametric_manager: ParametricManager = None):
            super().__init__(patch_name, patch_idx, var_type, BCType.EMPTY)

        def init_field(self, phi_cpd_cell, fvpatch, **kwargs):
            start = fvpatch.start_idx_cpd_cell[self.patch_idx]
            end = fvpatch.end_idx_cpd_cell[self.patch_idx]
            start_f = fvpatch.start_idx_face[self.patch_idx]
            end_f = fvpatch.end_idx_face[self.patch_idx]
            neighbor_cpd_cell = kwargs.get('neighbor_cpd_cell', None)
            if neighbor_cpd_cell is not None:
                owner = neighbor_cpd_cell[0, start_f:end_f]
                phi_cpd_cell[start:end, :] = phi_cpd_cell[owner, :]
            return phi_cpd_cell

        def eval(self, fvfield, integrator, patch_idx_offset: int, **kwargs):
            global_patch_idx = self.patch_idx + patch_idx_offset
            fvpatch_batch = integrator.fvpatch_batch
            neighbor_cpd_cell = integrator.neighbor_cpd_cell
            start_f = fvpatch_batch.start_idx_face[global_patch_idx]
            end_f = fvpatch_batch.end_idx_face[global_patch_idx]
            if cell_key in fvfield:
                phi = fvfield[cell_key]
                owner = neighbor_cpd_cell[0, start_f:end_f]
                ghost = neighbor_cpd_cell[1, start_f:end_f]
                phi[..., ghost, :] = phi[..., owner, :]
                fvfield[cell_key] = phi
            return fvfield

    class Symmetry(Zerogradient):
        """``type = "symmetry"``: for a transported scalar a symmetry plane
        is exactly the homogeneous Neumann condition -- mirror ghost plus a
        zeroed boundary gradient flux -- so the implementation IS
        Zerogradient (setT.py's Symmetry makes the same identification).
        Named separately so bc tomls can keep OpenFOAM vocabulary."""

    class Wall(Fixedvalue):
        """``type = "wall"``: low-Re (integrate-to-wall) Dirichlet at the
        field's own wall value -- k = 0 exactly at a no-slip wall, and
        Launder-Sharma's eps-tilde = 0 (the 1974 split is constructed so the
        transported variable vanishes there; phase-3b verifier's readiness
        verdict, commit 114eafd).  The value is bound by the field module,
        NOT written per patch, so a bc toml cannot mistype it; the stored
        variable is written directly (no value_transform)."""

        def __init__(self, patch_name: str, patch_idx: int, boundary_info: Dict,
                     parametric_manager: ParametricManager = None):
            if wall_value is None:
                raise ValueError(
                    f"[set{field_key.capitalize()}] type = \"wall\" is not "
                    f"defined for field '{field_key}' -- no uniform low-Re "
                    "wall value exists (omega's wall condition is "
                    "geometry-dependent: setOmega.Wall)")
            self.value = {"value_type": "uniform", "val": float(wall_value)}
            self.value_tensor = None
            BoundaryCondition.__init__(
                self, patch_name, patch_idx, var_type, BCType.FIRST)

    class Calculated(BoundaryCondition):
        """Strict no-op (setT.py precedent): ghost rows keep whatever the
        state carries.  The MMS residual-truncation arm uses it to hold
        exact manufactured Dirichlet values at ghost positions."""

        def __init__(self, patch_name: str, patch_idx: int, boundary_info: Dict,
                     parametric_manager: ParametricManager = None):
            super().__init__(patch_name, patch_idx, var_type, BCType.MIXED)

        def init_field(self, phi_cpd_cell, fvpatch, **kwargs):
            start = fvpatch.start_idx_cpd_cell[self.patch_idx]
            end = fvpatch.end_idx_cpd_cell[self.patch_idx]
            start_f = fvpatch.start_idx_face[self.patch_idx]
            end_f = fvpatch.end_idx_face[self.patch_idx]
            neighbor_cpd_cell = kwargs.get("neighbor_cpd_cell", None)
            if neighbor_cpd_cell is not None:
                owner = neighbor_cpd_cell[0, start_f:end_f]
                phi_cpd_cell[start:end, 0:1] = phi_cpd_cell[owner, 0:1]
            return phi_cpd_cell

        def eval(self, fvfield, integrator, patch_idx_offset: int, **kwargs):
            return fvfield

    return {
        "Fixedvalue": Fixedvalue,
        "Zerogradient": Zerogradient,
        "Cyclic": Cyclic,
        "Empty": Empty,
        "Calculated": Calculated,
        "Symmetry": Symmetry,
        "Wall": Wall,
    }
