"""The grid-parity (checkerboard) content of a pressure field.

Not part of the solve: nothing in this package calls it.  It is the instrument
the entry and the tests report the pressure with, and it is kept in the package
because what it measures -- the mode a bare continuity row is blind to -- is the
reason the momentum-weighted flux is mandatory here.
"""
from __future__ import annotations

import torch

# A mesh this instrument can read fills at least this fraction of the lattice its
# own axis planes imply.  Measured over every mesh in the repo, the
# ratio (planes_x * planes_y * planes_z) / n_cell separates the two families by a
# factor of 620 with nothing in between: axis-aligned lattices read 1.000 (LDC
# cubes, natural-convection square, TMR flat plate 136x96x2), 1.067 (DataCenter,
# 33,320 rack cells cut from 87x63x97) and 1.212 (TMR backward step, its step
# block cut out), while body-fitted meshes read 751.7 (heated cylinder O-grid),
# 1111.5 and 4011.2 (periodic hills).  The cut at 8 admits a lattice up to 7/8
# void, i.e. seven times DataCenter's hole, and still sits 94x below the nearest
# mesh that must be refused.
MAX_LATTICE_SITES_PER_CELL = 8.0


class NotAParityLattice(ValueError):
    """``(-1)^(i+j+k)`` is not recoverable from cell POSITIONS on this mesh.

    Raised, and carrying the reason, because the failure it replaced was a bare
    ``float("nan")``.  That NaN reached the log as ``grid-parity pressure mode
    amplitude nan`` on a DataCenter run whose field was healthy, and
    nothing in the line said whether the field, the solver or the instrument was
    at fault; the checkerboard reading is one leg of this repo's three-leg
    convergence standard, and a leg that fails silently is worse than one that is
    absent.  Callers that must not abort should catch this and print ``str(exc)``.
    """


def grid_parity_amplitude(position: torch.Tensor,
                          pressure: torch.Tensor) -> float:
    """How much of ``pressure`` sits in the grid-parity (checkerboard) mode.

    The mode is ``(-1)^(i+j+k)`` over the lattice, and the lattice index comes
    from a SEARCH into the axis' own distinct coordinates rather than from
    ``floor(x/h)`` or from ``sign(cos(pi x / h))``.  Both shortcuts have already
    produced a corrupted instrument here: the cosine is exactly zero at a cell
    centre ``(i + 1/2) h``, so its sign reads floating-point noise, and dividing
    by a spacing recovered from a float32 mesh puts cells on the wrong side of an
    integer boundary.

    Reported as a FRACTION of the pressure's deviation from its mean, so a smooth
    field reads near zero and a pure checkerboard reads one at any pressure scale.

    **Cell SPACING is irrelevant; only the plane INDEX matters.**  Until
    the plane-index rule, this refused any mesh whose axis gaps were not uniform to 1e-4
    relative, on the stated ground that the mode "is not defined by position" on
    a graded mesh.  That ground is wrong: checkerboard is a property of cell
    ADJACENCY, not of cell size, and the search above already returns the lattice
    index on a graded axis.  The claim was tested directly rather than argued --
    on the DataCenter mesh the parity built here disagrees across all 1,473,020
    interior faces, i.e. it is an exact 2-colouring of the cell graph, and so it
    is on the TMR backward step (557,792 faces) and flat plate (90,928) that the
    uniformity test also refused.  Grading and voids both leave it intact: a hole
    removes cells from the lattice without moving any surviving cell off its
    plane, which is why DataCenter's 33,320 cut cells cost nothing here.

    The uniformity test was therefore blind, not strict.  It returned NaN on five
    of the ten meshes in this repo -- every non-uniform one, including all three
    turbulence benchmarks -- and read a number only on the LDC and
    natural-convection cubes.

    Raises ``NotAParityLattice`` on a mesh where position genuinely cannot carry
    the mode.  Two ways that happens, both named in the message:

    * The cell centres do not lie on axis-aligned planes at all.  On the heated
      cylinder O-grid and the periodic hills the parity built here agrees across
      109,312 and 166,848 interior faces respectively, i.e. it is not a
      2-colouring and the number would be meaningless.  Only connectivity can
      give the mode on those, and this function is not given any.
    * Two cells land on the same lattice site, which means the 1e-6 relative
      dedup below merged two real planes.  The mode is then aliased, and a
      reading would understate the checkerboard by an unknown factor.

    **A uniformity tolerance here would be 1e-4 relative because
    the mesh arrives in float32.**  That constant is gone with the test, but the
    lesson that chose it is why the dedup below is relative: on the 48^3 cavity
    the axis spacing 1/48 is not representable in binary and its stored gaps
    spread by 2.9e-6 relative, so a 1e-9 tolerance declared that uniform mesh
    non-uniform and blanked the campaign's only checkerboard instrument at the
    one size that mattered, while reading fine at 32^3 where 1/32 is a power of
    two and exact.
    """
    # The lattice arithmetic runs in float64 REGARDLESS of what the mesh arrived
    # as.  Measured by probing the instrument rather than a run: with
    # float32 positions this returned NaN on uniform 100-, 129- and 200-cell
    # axes while reading 1.000000 on 128 and 256, because 1/128 and 1/256 are
    # powers of two and exact.  That is the SAME accident that hid the previous
    # version's blindness -- the production 128^2 cavity was passing on the
    # exactness of its mesh size, not on the fix.  Promoting here costs one copy
    # and makes the reading independent of the mesh's storage dtype; the caller's
    # own float64 promotion is then a convenience, not a load-bearing dependency.
    position = position.double()
    n_cell = position.shape[0]
    plane_of_cell: list[torch.Tensor] = []
    plane_count: list[int] = []
    for axis in range(position.shape[-1]):
        column = position[:, axis]
        # Deduplicate at 1e-6 of the axis' OWN size, not at 1e-6 absolute.  Two
        # cells in one lattice plane hold the same coordinate only to float32,
        # i.e. to ~1e-7 relative, so the quantum has to scale with the mesh or it
        # stops separating planes on a fine one.  Measured: the TMR
        # flat plate's smallest wall-normal gap is 2.02e-6, only 2.0x the old
        # ABSOLUTE 1e-6 quantum, so that mesh was two refinements from having its
        # first two wall layers silently merged into one plane.  The uniqueness
        # check below is the backstop that turns such a merge into a refusal
        # instead of a quiet under-reading.
        extent = float(column.max() - column.min())
        magnitude = float(column.abs().max())
        scale = max(extent, magnitude)
        quantum = 1.0e-6 * scale if scale > 0.0 else 1.0
        rounded = torch.round(column / quantum) * quantum
        coordinate, inverse = torch.unique(rounded, return_inverse=True)
        if coordinate.numel() < 2:
            continue                                  # a single-cell extrusion
        plane_of_cell.append(inverse)
        plane_count.append(int(coordinate.numel()))

    sites = 1
    for count in plane_count:
        sites *= count
    if sites > MAX_LATTICE_SITES_PER_CELL * n_cell:
        raise NotAParityLattice(
            f"cell centres do not lie on axis-aligned planes -- the "
            f"{'x'.join(str(c) for c in plane_count)} planes they span imply "
            f"{sites} lattice sites for {n_cell} cells "
            f"({sites / n_cell:.1f} per cell, cut at "
            f"{MAX_LATTICE_SITES_PER_CELL:g}).  This is a body-fitted or "
            f"unstructured mesh, where the grid-parity mode is a property of the "
            f"cell graph and cannot be recovered from position alone.")

    # ``site`` is the flat (i, j, k) address.  It cannot overflow int64: the
    # occupancy refusal above already bounds ``sites`` at eight cells' worth.
    site = torch.zeros(n_cell, dtype=torch.long, device=position.device)
    for inverse, count in zip(plane_of_cell, plane_count):
        site = site * count + inverse
    if int(torch.unique(site).numel()) != n_cell:
        raise NotAParityLattice(
            f"{n_cell - int(torch.unique(site).numel())} of {n_cell} cells share "
            f"a lattice site with another, so the plane search merged two real "
            f"planes and the parity is aliased.  Cell centres closer than 1e-6 of "
            f"an axis' extent are indistinguishable to this instrument.")

    index = torch.zeros(n_cell, dtype=torch.long, device=position.device)
    for inverse in plane_of_cell:
        index = index + inverse
    ones = torch.ones(n_cell, dtype=torch.float64, device=position.device)
    mode = torch.where(index.remainder(2) == 0, ones, -ones)
    pressure = pressure.double()
    pressure = pressure - pressure.mean()
    scale = float(pressure.norm())
    if scale == 0.0:
        return 0.0
    return abs(float(torch.dot(mode, pressure))) / (float(mode.norm()) * scale)
