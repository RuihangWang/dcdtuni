
"""
Abstract base classes for boundary condition implementations.

This module defines the common interface that all boundary condition classes
must implement, including both single-patch and batch-processing variants.
"""

import enum
from abc import ABC, abstractmethod
from typing import List
import torch
from FVdomain.SetBC.FvPatch import fvPatch

# Global variable configuration: name -> number of channels
# This defines the standard channel count for each physical variable and name for boundary files
VAR_CONFIG = {
    "rho": 1,
    "U": 3,
    "p": 1,
    "p_rgh": 1,
    "T": 1,
    "phi": 1,
    # Transported turbulence scalars (turbulence design doc section 7):
    # bc/k.toml and bc/omega.toml speak OpenFOAM values; for a log-omega
    # model the BC layer applies the omega -> ln(omega) transform at the
    # ghost write (setOmega.py) and at internalField init (Load_mesh).
    "k": 1,
    "omega": 1,
    "epsilon": 1,  # Launder-Sharma homogeneous dissipation; primal (A.4)
}

# Solver-specific variable requirements
# Maps solver type to list of required variables in Boundary file names
SOLVER_VARS = {
    "picoext": ["U", "p"],
    "picocmpt": ["U", "p"],
    "pico_old": ["U", "p"],
    "buoyantboussinesqpicoext": ["U", "p_rgh", "T"],
    "buoyantboussinesqpicocmpt": ["U", "p_rgh", "T"],
    "rhocoext": ["rho", "U", "p"],  # Compressible Euler/NS: Output [rho, u, v, w, p], T is derived
    "rhocoinviscompt": ["rho", "U", "p"],
    "rhocoviscompt": ["rho", "U", "p"],
    "diffusionext": ["phi"],  # Diffusion/Poisson equation
}

class BCType(enum.IntEnum):
    """Enumeration of boundary condition types"""
    FIRST = 0   # Dirichlet boundary condition
    # SECOND = 1  # Neumann boundary condition, 不单独区分第二类边界条件，直接放入mixed中处理
    MIXED = 1   # Neumann/Mixed/Robin/Cauchy boundary condition
    CYCLIC = 2  # Cyclic boundary condition (special treatment)
    COUPLED = 3 # Coupled boundary condition (e.g., exprFixedValue where outlet T depends on inlet T)
    EMPTY = 99  # Empty boundary condition (no treatment)

class VARType(enum.IntEnum):
    """Enumeration of physical variable types"""
    RHO = 0
    VELOCITY = 1
    PRESSURE = 2
    PRESSURE_RGH = 2
    TEMPERATURE = 3
    PHI = 4  # Diffusion scalar field
    TURB_K = 5      # Turbulent kinetic energy (transported turbulence scalar)
    TURB_OMEGA = 6  # Specific dissipation rate; stored variable is ln(omega)
    TURB_EPSILON = 7  # Homogeneous dissipation rate (Launder-Sharma); primal


# BC-file field name -> VARType, for consumers that hold only the field name
# (e.g. a host solver picking the snGrad boundary treatment for a transported
# scalar declared by TurbulenceModel.TRANSPORTED).
VAR_TYPE_BY_FIELD = {
    "rho": VARType.RHO,
    "U": VARType.VELOCITY,
    "p": VARType.PRESSURE,
    "p_rgh": VARType.PRESSURE_RGH,
    "T": VARType.TEMPERATURE,
    "phi": VARType.PHI,
    "k": VARType.TURB_K,
    "omega": VARType.TURB_OMEGA,
    "epsilon": VARType.TURB_EPSILON,
}

class BoundaryCondition(ABC):
    """
    Abstract base class defining the common interface for all boundary conditions.

    All concrete boundary condition classes (e.g., Fixedvalue, Zerogradient) must
    inherit from this class and implement the abstract methods init_field and eval.

    Attributes:
        patch_name: Name of the boundary patch
        patch_idx: Index of the boundary patch
        varType: Type of physical variable (temperature, pressure, velocity, etc.)
        bcType: Type of boundary condition (first/second/mixed/cyclic)

    Runtime tensor contract:
        Cell/face fields may be either ``[N, C]`` or ``[..., N, C]``;
        compound-cell gradients may be ``[N, C, D]`` or
        ``[..., N, C, D]``.  Boundary implementations must only index the
        entity/channel axes, leaving all leading (notably time) axes intact.
    """

    # Capability marker consumed by transient callers.  A spatial-only BC can
    # be projected independently onto every time slab.  History-dependent BCs
    # override this flag and must receive correctly shifted old/new states.
    depends_on_old_state = False

    # Capability marker consumed by the residual assembly.  A BC that sets it
    # constrains the WALL-ADJACENT INTERIOR CELL, not only the ghost row: the
    # cell's transport equation is DISCARDED and replaced by
    # ``phi_cell - phi_ghost``, which is what OpenFOAM's wall-function
    # machinery does through ``fvMatrix::setValues`` (fvMatrix.C:310-317 sets
    # ``psi[celli] = value`` and ``source_[celli] = value*Diag[celli]``, i.e.
    # the row becomes the constraint).  Only ``setOmega.Wall`` sets it: a plain
    # Dirichlet such as k = 0 at a wall does NOT manipulate the matrix in
    # OpenFOAM either, and constraining its cell would delete a real equation.
    #
    # Ghost-write BCs are the norm and this is the exception, so the default is
    # False and the assembly skips the whole mechanism when nothing declares it.
    constrains_adjacent_cell = False

    def __init__(self, patch_name:str, patch_idx:int, varType:VARType, bcType:BCType):
        self.patch_name = str(patch_name)  # Ensure native Python str (not tomlkit.String)
        self.patch_idx = int(patch_idx)    # Ensure native Python int (not tomlkit.Integer)
        self.varType = varType
        self.bcType = bcType
        self.is_coupled = False

    @abstractmethod
    def init_field(self, fvpatch_batch):
        """
        Initialize boundary field values.

        Args:
            fvpatch_batch: Batch data of boundary patches

        Returns:
            Initialized field data

        Note:
            Subclasses must implement this method
        """
        pass

    @abstractmethod
    def eval(self, integrator, fvpatch_batch):
        """
        Evaluate/update boundary condition during the solution process.

        Args:
            integrator: Integrator object
            fvpatch_batch: Batch data of boundary patches

        Returns:
            Updated field data

        Note:
            Subclasses must implement this method
        """
        pass

class BoundaryConditionBatch:
    """
    Container class for batch boundary condition processing.

    Used for processing boundary conditions across multiple patches simultaneously.

    Attributes:
        first: List of first-type boundary conditions
        mixed: List of mixed-type boundary conditions
        cyclic: List of cyclic-type boundary conditions
        empty: List of empty-type boundary conditions
        coupled: List of coupled-type boundary conditions
    """

    def __init__(self):
        self.first = []
        self.mixed = []
        self.cyclic = []
        self.empty = []
        self.coupled = []

        self.first_patch_idx_offset = [0] # 默认第一个case的offset为0
        self.mixed_patch_idx_offset = [0]
        self.cyclic_patch_idx_offset = [0]
        self.empty_patch_idx_offset = [0]
        self.coupled_patch_idx_offset = [0]

    def iter_conditions(self):
        """Yield every concrete BC in deterministic application-group order."""
        for groups in (self.first, self.cyclic, self.mixed, self.coupled, self.empty):
            for case_conditions in groups:
                yield from case_conditions

    @property
    def depends_on_old_state(self) -> bool:
        """Whether any contained BC requires a correctly paired old state."""
        return any(bc.depends_on_old_state for bc in self.iter_conditions())

    def old_state_dependent_conditions(self):
        """Return history-dependent BCs for diagnostics and validation errors."""
        return [bc for bc in self.iter_conditions() if bc.depends_on_old_state]

    @classmethod
    def from_bcpatch_list(cls, bcpatch_dict_list: List[dict], fvpatch_list: List):
        """
        Create a batch from a list of bcpatch dictionaries.

        Args:
            bcpatch_dict_list: List of bcpatch dictionaries, one per case
            fvpatch_list: List of fvPatch objects or fvPatchBatch

        Returns:
            BoundaryConditionBatch instance
        """
        # Create a new instance
        batch = cls()

        # Track cumulative patch offset across cases
        cumulative_num_patches = 0

        for case_i, bcpatch_dict in enumerate(bcpatch_dict_list):
            # Each bcpatch_dict has keys: BCType.FIRST, BCType.MIXED, BCType.CYCLIC, BCType.EMPTY
            # and also variable names like "rho", "U", etc.

            # Extract BC instances by type
            if BCType.FIRST in bcpatch_dict:
                batch.first.append(bcpatch_dict[BCType.FIRST])
                if case_i > 0:
                    # Append cumulative offset from previous cases
                    batch.first_patch_idx_offset.append(cumulative_num_patches)

            if BCType.MIXED in bcpatch_dict:
                batch.mixed.append(bcpatch_dict[BCType.MIXED])
                if case_i > 0:
                    batch.mixed_patch_idx_offset.append(cumulative_num_patches)

            if BCType.CYCLIC in bcpatch_dict:
                batch.cyclic.append(bcpatch_dict[BCType.CYCLIC])
                if case_i > 0:
                    batch.cyclic_patch_idx_offset.append(cumulative_num_patches)

            if BCType.EMPTY in bcpatch_dict:
                batch.empty.append(bcpatch_dict[BCType.EMPTY])
                if case_i > 0:
                    batch.empty_patch_idx_offset.append(cumulative_num_patches)

            if BCType.COUPLED in bcpatch_dict:
                batch.coupled.append(bcpatch_dict[BCType.COUPLED])
                if case_i > 0:
                    batch.coupled_patch_idx_offset.append(cumulative_num_patches)

            # Update cumulative offset for next case
            cumulative_num_patches += fvpatch_list[case_i].num_patches

        # Validation: ensure offset lists match BC lists in length
        assert len(batch.first) == len(batch.first_patch_idx_offset), \
            f"Offset list length mismatch: first={len(batch.first)}, offset={len(batch.first_patch_idx_offset)}"
        assert len(batch.mixed) == len(batch.mixed_patch_idx_offset), \
            f"Offset list length mismatch: mixed={len(batch.mixed)}, offset={len(batch.mixed_patch_idx_offset)}"
        assert len(batch.cyclic) == len(batch.cyclic_patch_idx_offset), \
            f"Offset list length mismatch: cyclic={len(batch.cyclic)}, offset={len(batch.cyclic_patch_idx_offset)}"
        assert len(batch.empty) == len(batch.empty_patch_idx_offset), \
            f"Offset list length mismatch: empty={len(batch.empty)}, offset={len(batch.empty_patch_idx_offset)}"
        assert len(batch.coupled) == len(batch.coupled_patch_idx_offset), \
            f"Offset list length mismatch: coupled={len(batch.coupled)}, offset={len(batch.coupled_patch_idx_offset)}"

        return batch
    
