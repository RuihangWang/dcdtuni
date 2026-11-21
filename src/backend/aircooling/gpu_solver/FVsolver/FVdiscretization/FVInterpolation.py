import sys
import os
file_dir = os.path.dirname(__file__)
sys.path.append(file_dir)

import logging
import torch
from Utils.utilities import FaceType
from torch_geometric.utils import scatter
from FVsolver.FVdiscretization.FVbase import FV_base
from FVsolver.FVdiscretization.tensor_layout import (
    pack_field_rhs,
    pack_gradient_rhs,
    unpack_field_rhs,
    unpack_gradient_rhs,
)
from FVsolver.FVdiscretization.FVConvection.face_interpolation import (
    batched_matvec,
    limited_linear_face_value,
    limited_linear_v_face_value,
    linear_corrected_face_value,
    linear_face_value,
    linear_grad_face_value,
    linear_upwind_face_value,
    quick_face_value,
    second_order_upwind_face_value,
    upwind_face_value,
)
from FVsolver.FVdiscretization.FVDiffusion import corrected_sn_grad, orthogonal_sn_grad

# Get module-level logger that inherits configuration from the main script
logger = logging.getLogger(__name__)


class FV_Interpolation(FV_base):
    """
    Comprehensive interpolation module for finite volume methods.
    
    This class provides various interpolation schemes for transferring values between
    different locations in a computational mesh (nodes, faces, and cell centers).
    It supports high-order interpolation schemes and RBF-based interpolation.
    
    The interpolation methods are based on finite volume discretization techniques
    described in Moukalled et al., "The Finite Volume Method in Computational Fluid 
    Dynamics" (2016).
    
    Implements caching mechanisms to avoid repeated computation of geometric coefficients
    such as interpolation weights (gC, gF) and geometric vectors (CF, eCF).
    """
    
    def __init__(self):
        """
        Initialize the interpolation module.
        
        Sets up caching structures for geometric coefficients to optimize
        repeated interpolation operations.
        """
        super().__init__()
        
        # Cached interpolation coefficients (computed once in calc_geometry_coeff)
        self.gC = None              # Interpolation weight for C (sender) cells
        self.gF = None              # Interpolation weight for F (receiver) cells  
        self.CF = None              # Vector from C to F cell centers
        self.dCF = None             # Distance between C and F cell centers
        self.eCF = None             # Unit vector from C to F
        self.eCf = None             # Vector from C center to face center
        self.eFf = None             # Vector from F center to face center
        
        # Cached cell indices (computed once to avoid repeated index operations)
        self.C_senders = None       # Sender cell indices for interior faces
        self.F_receivers = None     # Receiver cell indices for interior faces
        self.registered = False
        
    def register_geometrics(self, fv_graph, force_register=False):
        """
        Register and cache geometric quantities and PDE coefficients.
        
        Extracts geometry information from graph structures and caches frequently
        used quantities to avoid repeated computation during interpolation.
        
        Args:
            force_register: Whether to force register even if already registered
            
        Returns:
            bool: True if registration was successful, False otherwise
        """
        
        if self.registered and not force_register:
            return True

        graph_node = fv_graph.graph_node
        graph_face = fv_graph.graph_face
        graph_cell = fv_graph.graph_cell
        graph_cell_x = fv_graph.graph_cell_x
        graph_Index = fv_graph.graph_Index

        # BC patch objects vary PER CASE (different inlet/Re/flow-rate even when the
        # mesh geometry is shared across cases), so they must be rebound on EVERY call,
        # i.e. before the static-geometry early-out below.
        self.fvpatch_batch = fv_graph.fvpatch_batch
        self.bcpatch_batch = fv_graph.bcpatch_batch

        # --- Static-geometry idempotency (performance) ---
        # Everything from here to the end of register_geometrics (geometry views, the
        # ~15 boolean masks, surface/directed-area vectors, WLSQ matrix binds, the 2D
        # z-thickness normalization, calc_geometry_coeff, and the wide-stencil reset)
        # derives ONLY from static mesh topology/geometry. It is identical
        # across timesteps AND across cases that share a mesh. Yet the training forward
        # calls this with force_register=True every step (to refresh per-step *physics*
        # coefficients, which the solver's register_properties handles separately), which
        # forced this entire static build -- plus several GPU->CPU syncs (the .nonzero()
        # building the interior-cell index, the .any() EMPTY-face probe, and the
        # .max()/.min() extent reductions below) -- to re-run every step.
        # We skip it when the mesh is unchanged. The signature is built from tensor
        # SHAPES only (host-side, zero GPU sync); a genuine mesh change (mixed-mesh
        # training with different cell/face/edge counts) changes the signature and
        # triggers a correct full rebuild. Skipping leaves self.* geometry bound to the
        # first-registered mesh's tensors (kept alive by self), whose values are
        # identical to the current step's fresh clone.
        # Shape counts (host-side, zero GPU sync) ...
        _shape_sig = (
            int(graph_node._num_nodes), int(graph_face._num_faces),
            int(graph_cell._num_cells), int(graph_cell._num_cpd_cells),
            int(graph_cell.neighbor_cpd_cell.shape[-1]),
            int(graph_cell_x.neighbor_cpd_cell_x.shape[-1]),
        )
        # ... PLUS a cheap EXACT content fingerprint, so two DIFFERENT meshes that
        # happen to share the shape signature (a remeshed/mirrored variant, or a
        # 2D-extruded mesh of different z-thickness -> same counts, different geometry)
        # cannot produce a silent false cache hit serving stale geometry. Integer
        # connectivity sums catch topology differences; a few sampled raw positions
        # catch same-topology/different-geometry (incl. z-thickness). All values are
        # bit-identical across timesteps for a given mesh (static, re-cloned from the
        # read-only mmap) so there is no false rebuild; a handful of scalar
        # reductions/gathers (~0.2 ms) is negligible vs the ~270 ms forward. The full
        # tensor-equality verifier remains available via GEOM_CACHE_SELFCHECK=1.
        _cpd = graph_cell.cpd_cell_pos.reshape(-1)
        _nc = _cpd.numel()
        _content_sig = (
            int(graph_cell.neighbor_cpd_cell.sum()),
            int(graph_cell_x.neighbor_cpd_cell_x.sum()),
            float(_cpd[0]), float(_cpd[_nc // 3]),
            float(_cpd[2 * _nc // 3]), float(_cpd[-1]),
        )
        _mesh_sig = (_shape_sig, _content_sig)
        if self.registered and getattr(self, "_geom_mesh_sig", None) == _mesh_sig:
            # Optional deterministic correctness self-check (GEOM_CACHE_SELFCHECK=1):
            # prove the raw mesh tensors fed this step are bit-identical to the
            # first-built mesh, so skipping recomputation is provably exact. Off by
            # default (zero cost in production).
            if os.getenv("GEOM_CACHE_SELFCHECK") == "1":
                _chk = (
                    ("cpd_cell_pos", graph_cell.cpd_cell_pos.view(-1, 3), self.cpd_cell_pos),
                    ("face_pos", graph_face.pos.view(-1, 3), self.face_pos),
                    ("unv_face", graph_face.face_normal.view(-1, 3), self.unv_face),
                    ("neighbor_cpd_cell", graph_cell.neighbor_cpd_cell, self.neighbor_cpd_cell),
                    ("neighbor_cpd_cell_x", graph_cell_x.neighbor_cpd_cell_x, self.neighbor_cpd_cell_x),
                    ("cell_type", graph_cell.cell_type.view(-1), self.cell_type),
                    ("face_type", graph_face.face_type.view(-1), self.face_type),
                    ("A_cell_to_cell", graph_cell_x.A_cell_to_cell, self.A_cell_to_cell),
                    ("single_B_cell_to_cell", graph_cell_x.single_B_cell_to_cell, self.single_B_cell_to_cell),
                )
                for _name, _cur, _cached in _chk:
                    if not torch.equal(_cur, _cached):
                        raise AssertionError(
                            f"[GEOM_CACHE_SELFCHECK] mesh-cache mismatch on '{_name}': "
                            f"static-geometry skip would be INCORRECT for this graph"
                        )
            return True
        self._geom_mesh_sig = _mesh_sig
        # Connectivity-derived caches keyed by nothing but this geometry.  The
        # dtype promotion a Newton solve runs (casestate.promote_case_to_double)
        # re-registers with force_register=True and would otherwise leave index
        # tensors built against the previous registration in place.
        self._constrained_rows_cache = {}

        self.num_nodes = graph_node._num_nodes
        self.num_faces = graph_face._num_faces
        self.num_cells = graph_cell._num_cells
        self.num_cpd_cells = graph_cell._num_cpd_cells
        self.num_graphs = graph_cell._num_graphs
        
        # Geometry quantities - extract and reshape for efficient computation
        self.node_pos = graph_node.pos
        self.cells_node = graph_node.cells_node.view(-1)
        self.cells_node_ptr = graph_cell.cells_node_ptr.view(-1)
        self.cells_face = graph_face.cells_face.view(-1)
        self.cells_face_ptr = graph_cell.cells_face_ptr.view(-1)
        self.cell_volume = graph_cell.cell_volume.view(-1, 1)
        self.cells_face_unv = graph_cell.cells_face_unv.view(-1, 3)
        self.face_type = graph_face.face_type.view(-1)
        self.face_pos = graph_face.pos.view(-1, 3)
        self.face_area = graph_face.face_area.view(-1, 1)
        self.face_node = graph_node.face_node.view(-1)
        self.face_node_ptr = graph_face.face_node_ptr.view(-1)
        self.unv_face = graph_face.face_normal.view(-1,3)
        self.cell_type = graph_cell.cell_type.view(-1)
        self.cpd_cell_pos = graph_cell.cpd_cell_pos.view(-1, 3)
        
        # WLSQ precomputed matrices
        self.neighbor_cpd_cell_x = graph_cell_x.neighbor_cpd_cell_x
        self.A_cell_to_cell = graph_cell_x.A_cell_to_cell
        self.single_B_cell_to_cell = graph_cell_x.single_B_cell_to_cell
        
        # only consider interior face for interpolation
        self.neighbor_cpd_cell = graph_cell.neighbor_cpd_cell
        self.cpd_owner = self.C_senders = self.neighbor_cpd_cell[0]
        self.cpd_neighbor = self.F_receivers = self.neighbor_cpd_cell[1]
        
        # Generate valid mask for cpd_cell
        self.mask_valid_cyclic_cpd_cell = (self.cell_type == FaceType.CYCLIC)
        self.mask_non_cyclic_cpd_cell = ~self.mask_valid_cyclic_cpd_cell
        self.mask_interior_cell = (self.cell_type == FaceType.NORMAL)  # NORMAL
        self.mask_boundary_cell = ~self.mask_interior_cell  # NORMAL
        # Pre-computed interior cell indices for torch.compile compatibility
        # Avoids nonzero() in forward pass which causes dynamic shapes and recompilation
        self._interior_cell_indices = self.mask_interior_cell.nonzero(as_tuple=False).squeeze(-1)  # [N_interior]
        self.mask_interior_face = (self.face_type==FaceType.NORMAL)  # NORMAL
        self.mask_boundary_face = ~self.mask_interior_face  # NORMAL
        # Non-cyclic boundary masks (fix operator precedence and type)
        # Previous code used bitwise NOT on integer tensors: (~self.face_type == FaceType.CYCLIC),
        # which is incorrect and breaks the mask logic. Use explicit inequality instead.
        self.mask_boundary_non_cyclic_face = self.mask_boundary_face & (self.face_type != FaceType.CYCLIC)
        self.mask_boundary_non_cyclic_cpd_cell = self.mask_boundary_cell & (self.cell_type != FaceType.CYCLIC)
        # These masks depend only on registered mesh topology. Cache their
        # Python control-flow value and integer gather indices once so a
        # compiled forward does not introduce data-dependent branches or
        # dynamic-shape nonzero operators.
        self._all_boundary_non_cyclic_faces = bool(
            torch.all(self.mask_boundary_non_cyclic_face).item()
        )
        self._boundary_non_cyclic_cpd_indices = (
            self.mask_boundary_non_cyclic_cpd_cell.nonzero(
                as_tuple=False
            ).squeeze(-1)
        )

        # Compute surface vectors for flux calculations
        self.cells_face_surface_vec = self.cells_face_unv * self.face_area[self.cells_face]
        undirected_unv_cells_face = self.unv_face[self.cells_face]
        direction_cells_face = torch.sign((self.cells_face_unv * undirected_unv_cells_face).sum(dim=1,keepdim=True))
        self.directed_area_cells_face = (direction_cells_face * (self.face_area[self.cells_face])).view(-1,1)
        
        # Batch information for scatter operations
        self.batch_face = graph_face.batch
        self.batch_cell = graph_cell.batch[self.mask_interior_cell]
        self.batch_cpd_cell = graph_cell.batch

        # Wall-adjacent distance, per face [N_face, 1]: the normal projection
        # |(x_owner - x_f) . n_f| of the owner-cell centre onto the face
        # normal.  On BOUNDARY faces the owner is the wall-adjacent interior
        # cell and this is exactly the y1 OpenFOAM's low-Re omega condition
        # reads (omegaWallFunctionFvPatchScalarField.C:218-223 evaluates
        # 6 nu / (beta1 y^2) with y = first-cell wall distance); on interior
        # faces it is the owner-side half of dCF, carried for uniformity and
        # never read as a wall distance.  Static mesh geometry, built at
        # registration (turbulence design phase 4: no Eikonal field -- the
        # wall BCs need only the face-to-adjacent-cell-centre distance the
        # mesh already implies).  Consumed by SetBC/onthefly/setOmega.py Wall.
        self.wall_distance_face = (
            (self.cpd_cell_pos[self.cpd_owner] - self.face_pos)
            * self.unv_face
        ).sum(dim=-1, keepdim=True).abs()
        
        # EMPTY cpd_cell mask
        self.mask_empty_cpd_cell = (self.cell_type == FaceType.EMPTY)
        self.mask_non_empty_cpd_cell = ~self.mask_empty_cpd_cell
        # mask_neighbor_cpd_cell_x = ~(self.mask_empty_cpd_cell[self.neighbor_cpd_cell_x[0]] | self.mask_empty_cpd_cell[self.neighbor_cpd_cell_x[1]])
        # self.neighbor_cpd_cell_x = self.neighbor_cpd_cell_x[:,mask_neighbor_cpd_cell_x]

        # Source stencil for the C_cb checkerboard loss: the solver derives the
        # pure 2-hop pairs from it by subtracting the 1-hop face adjacency.
        # Built lazily by the solver when loss_checker_weight > 0 to avoid
        # polluting the torch.compile graph when the checker loss is disabled.
        # NOTE: at the default khops=1 the extended stencil IS the 1-hop face
        # adjacency, so the derived wide stencil comes out empty and the
        # checker loss is skipped; it only has an effect with khops>=2.
        self._neighbor_cpd_cell_x_for_wide = self.neighbor_cpd_cell_x
        self._wide_stencil_built = False

        # EMPTY face mask
        self.mask_empty_face = (self.face_type == FaceType.EMPTY)
        self.mask_non_empty_cells_face = ~self.mask_empty_face[self.cells_face]
        self._non_empty_cells_face_indices = self.mask_non_empty_cells_face.nonzero(
            as_tuple=False
        ).squeeze(-1)

        # Normalize geometric quantities for 2D problems (OpenFOAM-style approach)
        # For 2D simulations on z-extruded 3D meshes, both face_area and cell_volume need normalization
        # - cell_volume_3D = 2D_area × z_thickness → cell_volume_2D = cell_volume_3D / z_thickness
        # - face_area_3D = 2D_length × z_thickness → face_area_2D = face_area_3D / z_thickness
        # 2D cases are identified by the presence of EMPTY boundaries (OpenFOAM convention)
        if self.mask_empty_face.any():
            # For 2D cases, detect z-direction thickness and normalize
            z_coords = fv_graph.graph_node.pos[:, 2]  # Extract z-coordinates
            z_thickness = torch.abs(z_coords.max() - z_coords.min())

            if z_thickness > 1e-12:  # Ensure z_thickness is not zero
                # Normalize face areas, cell volumes, and directed_area_cells_face by z-thickness
                # This converts 3D geometric quantities to proper 2D quantities per unit depth
                self.face_area = self.face_area / z_thickness
                self.cell_volume = self.cell_volume / z_thickness
                self.directed_area_cells_face = self.directed_area_cells_face / z_thickness
                self.cells_face_surface_vec = self.cells_face_surface_vec / z_thickness

                logger.debug(f"[2D Case] Z-thickness: {z_thickness:.6e}")
                logger.debug(f"[2D Case] Normalized geometric quantities (face_area, cell_volume, directed_area) by z-thickness")

                # Log domain extents for debugging
                x_coords = fv_graph.graph_node.pos[:, 0]
                y_coords = fv_graph.graph_node.pos[:, 1]
                x_range = torch.abs(x_coords.max() - x_coords.min())
                y_range = torch.abs(y_coords.max() - y_coords.min())
                logger.debug(f"[2D Case] Domain extents - X: {x_range:.6e}, Y: {y_range:.6e}, Z: {z_thickness:.6e}")
            else:
                logger.warning("[2D Case] Z-thickness is nearly zero, skipping normalization")
        else:
            logger.debug("[3D Case] No EMPTY faces detected, no geometric normalization applied")

        # Compute and cache interpolation geometric coefficients
        self.calc_geometry_coeff()
        self.registered = True

    def calc_geometry_coeff(self):
        """
        Compute and cache geometric coefficients for face interpolation.
        
        Calculates interpolation weights (gC, gF) and geometric vectors (CF, eCF)
        based on the method described in Moukalled et al. (2016), Section 9.2.
        These coefficients are cached to avoid repeated computation during
        multiple interpolation operations.

        The coefficients are computed for every face; non-cyclic boundary faces
        then get the OpenFOAM boundary treatment applied at the end (zeroed
        correction vectors, orthogonal deltaCoeffs).
        """

        # Compute geometric vectors and distances
        # CF: vector from cell C center to cell F center
        self.CF = (self.cpd_cell_pos[self.F_receivers] - 
                    self.cpd_cell_pos[self.C_senders])[:, :, None]
        self.dCF = self.CF.norm(dim=1, keepdim=True)
        self.eCF = self.CF / self.dCF
        
        # Compute interpolation weights using distance-based weighting
        # gC: weight for upstream cell C, gF: weight for downstream cell F
        dfF = torch.norm(
            self.cpd_cell_pos[self.F_receivers] - self.face_pos,
            dim=1, keepdim=True
        )[:, :, None]
        self.gC = (dfF / self.dCF)
        if os.environ.get("LPCNS_FACE_WEIGHTS", "distance") == "normal":
            # OpenFOAM's linear weights (surfaceInterpolation::makeWeights): the two centre-to-face distances
            # projected on the face normal.  They equal the distance ratio above when the face centre lies on the
            # line of centres and stay inside [0, 1] when it does not (skewed faces, 2:1 interfaces, merged cells),
            # where the ratio above can favour the wrong cell or exceed 1.
            nC = ((self.face_pos - self.cpd_cell_pos[self.C_senders]) * self.unv_face).sum(dim=1, keepdim=True).abs()
            nF = ((self.cpd_cell_pos[self.F_receivers] - self.face_pos) * self.unv_face).sum(dim=1, keepdim=True).abs()
            self.gC = (nF / (nC + nF).clamp_min(1e-300))[:, :, None]
        self.gF = 1.0 - self.gC

        # Vectors from cell centers to face center (for gradient correction)
        self.Cf = (self.face_pos - self.cpd_cell_pos[self.C_senders])[:, :, None]
        self.Ff = (self.face_pos - self.cpd_cell_pos[self.F_receivers])[:, :, None]

        # === snGrad geometric coefficients (OpenFOAM-style) ===
        # CF vector (full, not unit): [N_face, 3]
        CF_vec = self.CF.squeeze(2)  # [N_face, 3]

        # n · d: projection of cell-center vector onto face normal
        # This is the "orthogonal distance" — the component of CF along the face normal
        n_dot_d = torch.sum(CF_vec * self.unv_face, dim=1, keepdim=True)  # [N_face, 1]

        # nonOrthDeltaCoeffs = 1 / max(n·d, 0.05*|d|)
        # Stabilized form from OpenFOAM (basicFvGeometryScheme.C:253)
        # Prevents division by zero on highly non-orthogonal meshes
        dCF_mag = self.dCF.squeeze(2)  # [N_face, 1]
        self.nonOrthDeltaCoeffs = 1.0 / torch.clamp(n_dot_d, min=0.05 * dCF_mag)  # [N_face, 1]

        # Non-orthogonal correction vector: k = n - d * nonOrthDeltaCoeffs
        # On orthogonal meshes: d ∥ n → k = 0 (no correction needed)
        # On non-orthogonal meshes: k captures the transverse component
        self.correctionVectors = self.unv_face - CF_vec * self.nonOrthDeltaCoeffs  # [N_face, 3]

        # Orthogonal deltaCoeffs: 1/|d| (used by "orthogonal" snGrad scheme)
        self.deltaCoeffs = 1.0 / dCF_mag  # [N_face, 1]

        # === Non-cyclic boundary faces ===
        # Do not overwrite the two arrays above on every non-cyclic
        # boundary face with ``nonOrthDeltaCoeffs = 1/|d|`` and
        # ``correctionVectors = 0``, citing OpenFOAM.  OpenFOAM does zero the
        # correction vectors there (basicFvGeometryScheme.C, nonOrthCorrectionVectors,
        # "Boundary correction vectors set to zero for boundary patches"), but it does
        # NOT store 1/|d|: its patch nonOrthDeltaCoeffs is the same
        # ``1/max(unitArea & delta, 0.05*mag(delta))`` as the interior (same file,
        # L287), and its patch ``delta`` is ``fvPatch::delta() = n (n & (Cf - Cn))``
        # (fvPatch.C L156, "Use patch-normal delta for all non-coupled BCs"), i.e. the
        # NORMAL PROJECTION of the owner-to-face-centre vector.  With that delta the
        # zeroed correction vector is not an extra modelling choice at all --
        # ``n - delta/(n.delta)`` is identically zero -- so OpenFOAM's boundary
        # treatment is one self-consistent statement: read the flux along n over the
        # distance n.d, and DROP the tangential offset between the face centre and the
        # point n(n.d) reaches.  1/|d| is a third value that appears nowhere in it, and
        # it made the wall flux carry a ``grad_t . d_t / (n.d)`` error that does not
        # vanish under refinement on a mesh whose skewness does not (measured
        # over BOUNDARY faces: 1/(n.d) differs from the stored 1/|d| by up to
        # 7.94e-2 relative on the 30%-perturbed MMS box at n = 32 and 1.52e-5 on the
        # Cylinder O-grid, is bitwise identical on every orthogonal fixture in this
        # tree, and does NOT refine away -- that box's boundary non-orthogonality goes
        # 17.2 -> 19.7 -> 22.1 degrees from n = 8 to 32).
        #
        # Both arrays now keep the general-mesh form on every face.  The tangential
        # offset OpenFOAM drops is instead CORRECTED, on the faces where the correction
        # is meaningful -- see _snGrad_operands: the ghost of a first-type patch holds a
        # value AT the face centre, so ``grad_owner . (n - d/(n.d))`` completes the
        # face-centre normal derivative, while a mirrored (zero-gradient / symmetry)
        # ghost states snGrad = 0 outright and must not be corrected at all.

    def _invalidate_boundary_caches(self) -> None:
        """Drop the per-variable boundary masks when either input to them moves.

        BOTH sources, not either: ``bcpatch_batch`` is rebound on every register
        call because patch VALUES are per case, while the geometry (and with it
        ``correctionVectors``) is deliberately cached across steps and across
        cases that share a mesh.  Keying on one alone leaves the other able to
        change underneath a cached mask.
        """
        source = (id(self.bcpatch_batch), id(getattr(self, "correctionVectors", None)))
        if self.__dict__.get("_boundary_cache_source") != source:
            self._boundary_cache_source = source
            self._dirichlet_face_keepalive = (self.bcpatch_batch,
                                              getattr(self, "correctionVectors", None))
            self._dirichlet_face_cache = {}
            self._sngrad_operand_cache = {}

    def dirichlet_boundary_face_mask(self, var_type) -> torch.Tensor:
        """``[N_face]`` bool: non-cyclic boundary faces whose ghost holds a value
        PRESCRIBED AT THE FACE CENTRE, for this variable.

        Read off the case's own first-type (``BCType.FIRST``) conditions rather
        than off the face type, because the answer is per VARIABLE and not per
        face: a lid-driven cavity wall is Dirichlet in U and zero-gradient in p on
        the same faces.  A mirrored ghost carries no face-centre value, so the
        boundary snGrad correction below has nothing to complete there and would
        instead overwrite the hard-enforced ``snGrad = 0``.

        Rebound whenever ``bcpatch_batch`` is (it is rebound every register call,
        since patch VALUES vary per case even when the mesh does not).
        """
        self._invalidate_boundary_caches()
        cache = self._dirichlet_face_cache
        key = int(var_type)
        if key in cache:
            return cache[key]

        mask = torch.zeros_like(self.mask_boundary_non_cyclic_face)
        batch, patches = self.bcpatch_batch, self.fvpatch_batch
        if batch is not None and patches is not None:
            for case_i, conditions in enumerate(batch.first):
                offset = int(batch.first_patch_idx_offset[case_i])
                for bc in conditions:
                    if int(bc.varType) != key:
                        continue
                    patch = int(bc.patch_idx) + offset
                    mask[int(patches.start_idx_face[patch]):
                         int(patches.end_idx_face[patch])] = True
        mask = mask & self.mask_boundary_non_cyclic_face
        cache[key] = mask
        return mask

    def facecv_active_face_mask(self, face_interior, pressure_var_type, boundary_rows):
        """``[N_face, 1]`` bool: which faces carry a face-CV momentum equation.

        ``boundary_rows`` is the ``[fvSchemes] faceCvBoundaryRows`` setting:

        "none"               interior faces only.  This is what the tree shipped
                             earlier, and it
                             has NO pressure-level anchor: on the Cylinder the
                             face snGrad(p_rgh) responds to a uniform interior
                             pressure shift by 2.07e+04 on the discarded rows
                             and 0.000000e+00 on the kept ones.  A face_cv-only
                             arm then cannot see the outlet Dirichlet value at
                             all, and a cell+face_cv arm sees it through one
                             layer of owner cells only (1,280 of 147,200).
        "pressureDirichlet"  interior faces plus the boundary faces whose
                             pressure ghost holds a face-centre value -- the
                             only rows that carry the level (the validated
                             good run's own comment named that outlet row "the
                             vortex-vs-anchor conflict equation itself").
        "all"  (default)     every non-empty face, the validated
                             objective: zero-gradient and symmetry faces
                             contribute n.R_C with no pressure term; cyclic
                             faces are TWO-SIDED (ghost filled
                             from the periodic partner, 0.5*(V_C+V_Q)), i.e.
                             interior rows that happen to cross the seam.

        Empty faces never carry a row.  Physical-boundary rows use the owner's
        residual density with V_C, matching the good run (a half cell would be
        0.5*V_C -- a 2x weight, not a different operator; left as validated).
        """
        interior_only = (
            face_interior
            & self.mask_interior_face.view(-1, 1).bool()
            & (~self.mask_empty_face.view(-1, 1).bool())
        )
        if boundary_rows == "none":
            return interior_only
        if boundary_rows == "pressureDirichlet":
            return interior_only | self.dirichlet_boundary_face_mask(pressure_var_type).view(-1, 1)
        if boundary_rows == "all":
            return ~self.mask_empty_face.view(-1, 1).bool()
        raise ValueError(f"faceCvBoundaryRows={boundary_rows!r}: expected none | pressureDirichlet | all")

    def _snGrad_operands(self, var_type):
        """``(owner_weight, neighbour_weight, correction_vectors)`` for the
        corrected snGrad, with the boundary treatment this variable earns.

        Two departures from the interior spelling, both confined to non-cyclic
        boundary faces:

        * The linear weights put ALL of a boundary face on its ghost (``gC = 0``
          exactly, because the ghost centroid IS the face centre), and ghost
          compound cells carry no WLSQ gradient -- so the
          interpolated correction gradient there is identically zero and the
          correction would be inert whatever the correction vector says.  The
          owner's gradient is the only one that exists at a wall, and it is the
          one the correction wants: it completes ``grad . n`` AT the face centre.
        * On a boundary face that is NOT first-type for this variable the
          correction vector is zeroed, restoring OpenFOAM's treatment exactly.
          A zero-gradient or symmetry ghost mirrors its owner, so the two-point
          difference is identically zero and that IS the boundary condition;
          adding ``grad_owner . k`` to it would silently replace a hard-enforced
          zero flux with a mesh-dependent one.

        ``var_type = None`` means the caller has no case boundary conditions to
        consult (a hand-built fixture, a probe).  Nothing is then treated as
        first-type, which reproduces OpenFOAM's boundary treatment on every face.
        """
        self._invalidate_boundary_caches()
        cache = self._sngrad_operand_cache
        key = None if var_type is None else int(var_type)
        if key in cache:
            return cache[key]

        boundary = self.mask_boundary_non_cyclic_face
        dirichlet = (torch.zeros_like(boundary) if var_type is None
                     else self.dirichlet_boundary_face_mask(var_type))
        owner = torch.where(dirichlet.view(-1, 1, 1), torch.ones_like(self.gC), self.gC)
        neighbour = torch.where(dirichlet.view(-1, 1, 1), torch.zeros_like(self.gF), self.gF)
        vectors = torch.where((boundary & ~dirichlet).view(-1, 1),
                              torch.zeros_like(self.correctionVectors),
                              self.correctionVectors)
        cache[key] = (owner, neighbour, vectors)
        return cache[key]

    def compute_snGrad(self, phi_cpd_cell, grad_phi_cpd_cell=None, scheme="corrected",
                       var_type=None, operands=None):
        """
        Compute surface normal gradient at faces (OpenFOAM-style).

        Delegates to FVDiffusion.orthogonal_sn_grad / corrected_sn_grad (see
        those docstrings for the per-scheme OpenFOAM-parity description).

        Args:
            phi_cpd_cell: [N_cpd, N_var] cell-centered field values
            grad_phi_cpd_cell: [N_cpd, N_var, 3] cell-centered gradients (for correction)
            scheme: "corrected", "uncorrected", or "orthogonal"
            var_type: VARType of the field being differenced, which decides which
                boundary faces carry a face-centre Dirichlet value and therefore
                take the boundary non-orthogonal correction (see _snGrad_operands).
                None reproduces OpenFOAM's boundary treatment on every face.
            operands: ``(owner_weight, neighbour_weight, correction_vectors)`` in place of
                ``_snGrad_operands(var_type)`` (the flux bracket's own weights, see
                BaseSolver._mwi_bracket_gradients).

        Returns:
            [N_face, N_var] face-normal gradient (scalar per face per variable)
        """
        if phi_cpd_cell.ndim > 2:
            phi_packed, layout = pack_field_rhs(phi_cpd_cell)
            grad_packed = None
            if grad_phi_cpd_cell is not None:
                grad_packed, grad_layout = pack_gradient_rhs(grad_phi_cpd_cell)
                if grad_layout != layout:
                    raise ValueError("phi and gradient layouts do not match")
            result = self.compute_snGrad(phi_packed, grad_packed, scheme=scheme,
                                         var_type=var_type, operands=operands)
            return unpack_field_rhs(result, layout)

        if scheme == "orthogonal":
            return orthogonal_sn_grad(
                phi_cpd_cell,
                face_owner=self.C_senders,
                face_neighbor=self.F_receivers,
                delta_coeffs=self.deltaCoeffs,
            )
        # "uncorrected" and "corrected" both use nonOrthDeltaCoeffs; the
        # correction applies only for "corrected" with a gradient provided.
        owner_weight, neighbor_weight, correction_vectors = (
            self._snGrad_operands(var_type) if operands is None else operands)
        return corrected_sn_grad(
            phi_cpd_cell,
            face_owner=self.C_senders,
            face_neighbor=self.F_receivers,
            non_orth_delta_coeffs=self.nonOrthDeltaCoeffs,
            grad_cell=grad_phi_cpd_cell if scheme == "corrected" else None,
            owner_weight=owner_weight,
            neighbor_weight=neighbor_weight,
            correction_vectors=correction_vectors,
        )

    def interpolating_with_grad(
        self,
        src:torch.Tensor=None,
        dst_index:torch.Tensor=None,
        dst_size:int=None,
        src_grad:torch.Tensor=None,
        src_pos:torch.Tensor=None,
        dst_pos:torch.Tensor=None,
    ):
        """
        Interpolate values from source points to destination points with optional gradient correction.

        This function performs interpolation using scatter_mean operation. When gradients are provided,
        it applies first-order correction to improve interpolation accuracy by considering the spatial
        displacement between source and destination positions.

        Args:
            src (torch.Tensor): Source values to be interpolated. Shape: (N, ...) where N is the number of source points.
            dst_index (torch.Tensor): Indices mapping source points to destination points. Shape: (N,).
            dst_size (int): Number of destination points.
            src_grad (torch.Tensor, optional): Gradients at source points for first-order correction.
                                             Shape should be compatible with src for gradient computation.
            src_pos (torch.Tensor, optional): Positions of source points. Required when src_grad is provided.
                                            Shape: (N, spatial_dim).
            dst_pos (torch.Tensor, optional): Positions of destination points. Required when src_grad is provided.
                                            Shape: (dst_size, spatial_dim).

        Returns:
            torch.Tensor: Interpolated values at destination points. Shape: (dst_size, ...).

        Raises:
            ValueError: If dimension 0 of input tensors are inconsistent.
            ValueError: If src_grad is provided but src_pos or dst_pos is None.
        """
        if src.ndim > 2:
            src_packed, layout = pack_field_rhs(src)
            grad_packed = None
            if src_grad is not None:
                grad_packed, grad_layout = pack_gradient_rhs(src_grad)
                if grad_layout != layout:
                    raise ValueError("src and src_grad layouts do not match")
            result = self.interpolating_with_grad(
                src=src_packed,
                dst_index=dst_index,
                dst_size=dst_size,
                src_grad=grad_packed,
                src_pos=src_pos,
                dst_pos=dst_pos,
            )
            return unpack_field_rhs(result, layout)

        # Check dimension consistency
        if src.dim() <2:
            raise ValueError("src and dst_index must have at least 2 dimension: [N_elem, Channel]")

        if src.size(0) != dst_index.size(0):
            raise ValueError(f"src and dst_index must have the same size in dimension 0. "
                           f"Got src.size(0)={src.size(0)}, dst_index.size(0)={dst_index.size(0)}")

        if src_grad is None:
            return scatter(src, dst_index, dim=0, dim_size=dst_size, reduce='mean')
        else:
            r_n_2_c = (dst_pos - src_pos).unsqueeze(1)

            if len(src_grad.size()) > 3: # scr_grad: [N_element, Channel, spatial_dim]
                first_order_correction = (
                    torch.matmul(
                        r_n_2_c.unsqueeze(1).unsqueeze(1),
                        src_grad.unsqueeze(-1),
                    )
                    .squeeze()
                )
            else: # scr_grad: [N_element, spatial_dim]
                first_order_correction = (
                    torch.matmul(r_n_2_c.unsqueeze(1),src_grad.unsqueeze(-1))
                    .squeeze()
                )
                
            if len(first_order_correction.shape)<2:
                first_order_correction=first_order_correction[:,None]

            dst_value = scatter(
                src=(src + first_order_correction), 
                index=dst_index, 
                dim=0, 
                dim_size=dst_size,
                reduce='mean',
            )

            return dst_value
        
    def interpolating_gradients_to_faces(
        self,
        phi_cpd_cell: torch.Tensor,
        grad_phi_cpd_cell: torch.Tensor,
        scheme: str = "Linear corrected",
    ) -> torch.Tensor:
        """
        Interpolate gradients from cell centers to face centers.

        Supports multiple interpolation schemes:
        - "Linear": plain distance-weighted average of the cell gradients.
        - "Linear corrected": adds the non-orthogonality adaptive correction
          (Moukalled Section 9.4). The correction is enabled only when the
          non-orthogonality angle exceeds a small tolerance, so it's zero on
          orthogonal grids and active otherwise.
        - "Linear overRelaxed": applies the same correction unconditionally,
          which makes the face-normal component reduce to the compact snGrad
          (machine-precision OpenFOAM parity on orthogonal meshes in the MMS
          suite). Opt-in via the gradInterpolationSchemes key.

        Args:
            phi_cpd_cell (torch.Tensor): Cell-centered scalar values [N_cells, N_variables]
            grad_phi_cpd_cell (torch.Tensor): Cell-centered gradients [N_cells, N_variables, 3]
            scheme (str): Interpolation scheme. Options: "Linear",
                "Linear corrected", "Linear overRelaxed"

        Returns:
            torch.Tensor: Face-centered gradients [N_faces, N_variables, 3]

        Note:
            Requires register_geometrics() to be called first.

        References:
            Moukalled, F., Mangani, L., & Darwish, M. (2016). The finite volume method
            in computational fluid dynamics. Springer, Section 9.4.
        """

        if phi_cpd_cell.ndim > 2:
            phi_packed, layout = pack_field_rhs(phi_cpd_cell)
            grad_packed, grad_layout = pack_gradient_rhs(grad_phi_cpd_cell)
            if grad_layout != layout:
                raise ValueError("phi and gradient layouts do not match")
            result = self.interpolating_gradients_to_faces(
                phi_cpd_cell=phi_packed,
                grad_phi_cpd_cell=grad_packed,
                scheme=scheme,
            )
            return unpack_gradient_rhs(result, layout)

        # Expand phi_cell for broadcasting with geometric coefficients
        phi_cpd_cell_expanded = phi_cpd_cell[:, :, None]

        # Compute interpolated gradient using distance-weighted averaging
        grad_f_hat = (grad_phi_cpd_cell[self.C_senders] * self.gC +
                     grad_phi_cpd_cell[self.F_receivers] * self.gF)  # [N_face, N_variables, 3]

        if scheme == "Linear":
            return grad_f_hat  # [N_face, N_variables, 3]
        
        elif scheme == "Linear corrected":
            # Non-orthogonality adaptive correction (Moukalled Section 9.4)
            # The correction is enabled when the non-orthogonality angle is non-zero:
            #   - Orthogonal grids: angle ≈ 0 → no correction
            #   - Non-orthogonal grids: angle > 0 → full correction

            # Compute non-orthogonality factor: sin(theta), theta = angle between eCF and face normal
            # Use normalized face normal to avoid dependence on face area scaling.
            eCF_hat = self.eCF.squeeze(2)  # [N_face, 3]
            cos_theta = torch.sum(self.unv_face * eCF_hat, dim=1, keepdim=True).abs().clamp(min=0.0, max=1.0)
            non_orth_factor = torch.sqrt((1.0 - cos_theta ** 2).clamp_min(0.0))  # [N_face, 1]
            non_orth_tol = 1e-5
            non_orth_weight = (non_orth_factor > non_orth_tol).to(non_orth_factor.dtype)

            # Compute correction term (Moukalled Eq. 9.46-9.48)
            gradient_dot_eCF = batched_matvec(grad_f_hat, self.eCF)  # [N_face, N_var, 1]
            phi_difference_normalized = ((phi_cpd_cell_expanded[self.F_receivers] -
                                        phi_cpd_cell_expanded[self.C_senders]) / self.dCF)  # [N_face, N_var, 1]

            correction = ((phi_difference_normalized - gradient_dot_eCF) *
                         (self.eCF.transpose(1, 2)))  # [N_face, N_var, 3]

            # Apply non-orthogonality correction using a binary switch
            # non_orth_weight: [N_face, 1] -> broadcast to [N_face, 1, 1] for correction
            weighted_correction = correction * non_orth_weight.unsqueeze(2)
            grad_f_hat += weighted_correction

        elif scheme == "Linear overRelaxed":
            # Over-relaxed face gradient (Moukalled Eq. 9.46-9.48 WITHOUT the
            # non-orthogonality gate): the eCF-directional component of the
            # interpolated gradient is UNCONDITIONALLY replaced by the compact
            # two-point difference (phi_F - phi_C)/dCF; the tangential part
            # keeps the interpolated (WLSQ) gradient.
            #
            # Why unconditional: "Linear corrected" above switches the same
            # correction OFF on orthogonal meshes (non_orth_factor ~ 0), which
            # leaves the face-normal gradient to the wide interpolated stencil.
            # MMS (experiments/MMS_SchemeAccuracy) shows that wide-stencil
            # normal gradient is ~10-50x less accurate than the compact
            # difference on hex meshes and carries a multi-layer low-order
            # boundary halo. With this scheme the diffusion operator's normal
            # flux reduces exactly to OpenFOAM's snGrad on orthogonal meshes
            # (machine-precision parity in the MMS suite) while remaining a
            # full gradient vector and fully autograd-attached.
            #
            # Boundary (non-cyclic) faces: phi_F is the ghost value at the
            # face centre, so the replacement reproduces OpenFOAM's one-sided
            # fixedValue snGrad. Cyclic and EMPTY faces keep the pure
            # interpolated gradient (their cpd positions are face-centred;
            # the compact difference across the seam is handled by the
            # dedicated cyclic machinery instead).
            gradient_dot_eCF = batched_matvec(grad_f_hat, self.eCF)  # [N_face, N_var, 1]
            phi_difference_normalized = ((phi_cpd_cell_expanded[self.F_receivers] -
                                        phi_cpd_cell_expanded[self.C_senders]) / self.dCF)  # [N_face, N_var, 1]
            correction = ((phi_difference_normalized - gradient_dot_eCF) *
                         (self.eCF.transpose(1, 2)))  # [N_face, N_var, 3]

            apply_mask = (self.face_type != FaceType.CYCLIC) & \
                         (self.face_type != FaceType.EMPTY)
            grad_f_hat = grad_f_hat + correction * \
                apply_mask.view(-1, 1, 1).to(correction.dtype)
        else:
            raise ValueError(f"No support for this scheme '{scheme}' at interpolating_gradients_to_faces, supported: 'Linear', 'Linear corrected', 'Linear overRelaxed'...")

        return grad_f_hat  # [N_face, N_variables, 3]

    def interpolating_phic_to_faces(
        self,
        phi_cpd_cell: torch.Tensor,
        grad_phi_cpd_cell: torch.Tensor = None,
        scheme: str = "Linear",
    ) -> torch.Tensor:
        """
        Interpolate scalar values from cell centers to face centers using high-order interpolation.
        
        Implements the scalar interpolation scheme from Moukalled et al.,
        "The Finite Volume Method in Computational Fluid Dynamics", Section 9.2, page 276.
        
        Uses distance-weighted linear interpolation with gradient-based correction
        to achieve high-order accuracy. Pre-computed geometric coefficients are used
        to optimize performance.
        
        Args:   
            phi_cpd_cell (torch.Tensor): Cell-centered and boundary face scalar values [N_cells, N_variables]
            grad_phi_cpd_cell (torch.Tensor): Cell-centered gradients [N_cells, N_variables, 3]
            scheme (str): Interpolation scheme to use. Options: "Linear",
                "Linear corrected", "Linear grad". The upwind-biased schemes
                ("Upwind", "LinearUpwind", "SOU", "Quick", "LimitedLinear k")
                live in div_phic_to_faces, which delegates the central ones here.

        Returns:    
            torch.Tensor: Face-centered scalar values [N_faces, N_variables]
                         High-order accurate values at all face centers
            
        Note:
            Requires register_geometrics() to be called first to compute geometric coefficients.
            
        References:
            Moukalled, F., Mangani, L., & Darwish, M. (2016). The finite volume method 
            in computational fluid dynamics. Springer, Section 9.2.
        """
        if phi_cpd_cell.ndim > 2:
            phi_packed, layout = pack_field_rhs(phi_cpd_cell)
            grad_packed = None
            if grad_phi_cpd_cell is not None:
                grad_packed, grad_layout = pack_gradient_rhs(grad_phi_cpd_cell)
                if grad_layout != layout:
                    raise ValueError("phi and gradient layouts do not match")
            result = self.interpolating_phic_to_faces(
                phi_cpd_cell=phi_packed,
                grad_phi_cpd_cell=grad_packed,
                scheme=scheme,
            )
            return unpack_field_rhs(result, layout)

        if scheme == "Linear":
            return linear_face_value(
                phi_cpd_cell,
                face_owner=self.C_senders,
                face_neighbor=self.F_receivers,
                owner_weight=self.gC,
                neighbor_weight=self.gF,
            )
        elif scheme == "Linear corrected":
            return linear_corrected_face_value(
                phi_cpd_cell, grad_phi_cpd_cell,
                cell_pos=self.cpd_cell_pos,
                face_pos=self.face_pos,
                face_owner=self.C_senders,
                face_neighbor=self.F_receivers,
                owner_weight=self.gC,
                neighbor_weight=self.gF,
            )
        elif scheme == "Linear grad":
            return linear_grad_face_value(
                phi_cpd_cell, grad_phi_cpd_cell,
                face_owner=self.C_senders,
                face_neighbor=self.F_receivers,
                owner_weight=self.gC,
                neighbor_weight=self.gF,
                owner_to_face=self.Cf,
                neighbor_to_face=self.Ff,
                interior_face_mask=self.mask_interior_face,
            )
        else:
            raise ValueError("No support for this scheme at interpolating_phic_to_faces, supported: '1st corrected', '2nd corrected'...")

    def rhie_chow_interpolation(self, *args, **kwargs):
        """GONE: this implementation was deleted; use ``momentumInterpolation``.

        Do not read this as "Rhie-Chow is banned".  That ban was lifted on
        request, and the supported momentum-weighted (Rhie-Chow) face flux
        lives in ``BaseSolver.momentum_weighted_flux``, selected per case with
        ``[fvSchemes] momentumInterpolation = "mwi"``.  What is gone is THIS
        implementation, whose body was removed long before that and which
        nothing has called since; it survives only as a name that raises, so a
        caller written against the old API fails loudly instead of silently
        interpolating nothing.

        The docstring pointed at "CLAUDE.md FVM Loss Rules" for a prohibition
        that does not exist there, which is worse than no pointer at all.
        """
        raise NotImplementedError(
            "This Rhie-Chow implementation was removed. The supported "
            "momentum-weighted flux is BaseSolver.momentum_weighted_flux, "
            'enabled with [fvSchemes] momentumInterpolation = "mwi".')

    def compute_rAU_face(self, *args, **kwargs):
        """GONE: helper for the removed implementation above.

        Its replacement is ``BaseSolver.face_pressure_coefficient``, which
        returns the same d_f = 1/2 (V_P/a_P + V_Q/a_Q) the momentum-weighted
        flux multiplies its bracket by.
        """
        raise NotImplementedError(
            "compute_rAU_face was removed; use "
            "BaseSolver.face_pressure_coefficient.")
        
    def rbf_interpolate(
        self,
        phi_values: torch.Tensor,
        source_pos: torch.Tensor,
        target_pos: torch.Tensor,
        source_indices: torch.Tensor,
        target_indices: torch.Tensor,
        k: int = 4,
        shape_param: float = 0.23
    ) -> torch.Tensor:
        """
        High-performance Radial Basis Function (RBF) interpolation for arbitrary source-to-target mapping.
        
        Implements an optimized RBF interpolation scheme using vectorized operations
        for maximum computational efficiency. Suitable for complex interpolation
        scenarios not covered by standard finite volume interpolation methods.
        
        Args:
            phi_values (torch.Tensor): Source point values [N_source, N_variables]
            source_pos (torch.Tensor): Source point positions [N_source, N_spatial_dims]
            target_pos (torch.Tensor): Target point positions [N_target, N_spatial_dims]
            source_indices (torch.Tensor): Source point indices [N_connections]
            target_indices (torch.Tensor): Target point indices [N_connections]
            k (int, optional): Number of neighbors for each target point. Default: 4
            shape_param (float, optional): RBF shape parameter. Default: 0.23
            
        Returns:
            torch.Tensor: Interpolated values at target points [N_target, N_variables]
            
        Examples:
            Node-to-cell interpolation:
            >>> cell_values = self.rbf_interpolate(
            ...     phi_values=node_phi, source_pos=node_pos, target_pos=cell_pos,
            ...     source_indices=cells_node, target_indices=cells_index
            ... )
            
            Cell-to-node interpolation:
            >>> node_values = self.rbf_interpolate(
            ...     phi_values=cell_phi, source_pos=cell_pos, target_pos=node_pos,
            ...     source_indices=cells_index, target_indices=cells_node
            ... )
            
        Note:
            Uses multiquadric RBF kernel: φ(r) = √(r² + c²) where c is shape_param.
            Optimized for batch processing with vectorized distance calculations.
        """
        if phi_values.ndim > 2:
            phi_packed, layout = pack_field_rhs(phi_values)
            result = self.rbf_interpolate(
                phi_values=phi_packed,
                source_pos=source_pos,
                target_pos=target_pos,
                source_indices=source_indices,
                target_indices=target_indices,
                k=k,
                shape_param=shape_param,
            )
            return unpack_field_rhs(result, layout)

        n_target = target_pos.size(0)
        n_features = phi_values.size(1)
        
        # Reorganize data for efficient batch processing
        source_pos_neighbors = source_pos[source_indices].view(n_target, k, -1)
        source_phi_neighbors = phi_values[source_indices].view(n_target, k, n_features)
        
        # Compute inter-source distance matrix using optimized vectorization
        neighbors_diff = (source_pos_neighbors.unsqueeze(2) - 
                         source_pos_neighbors.unsqueeze(1))
        distances_squared = torch.sum(neighbors_diff * neighbors_diff, dim=-1)
        
        # Compute RBF kernel matrix using multiquadric basis function
        shape_param_sq = shape_param * shape_param
        kernel = torch.sqrt(distances_squared + shape_param_sq)
        
        # Solve RBF system using batch linear solver
        coeffs = torch.linalg.solve(kernel, source_phi_neighbors)
        
        # Compute target-to-source distances in single operation
        target_pos_expanded = target_pos[target_indices].view(n_target, k, -1)
        target_diff = target_pos_expanded - source_pos_neighbors
        target_distances_squared = torch.sum(target_diff * target_diff, dim=-1)
        
        # Evaluate RBF at target points
        kernel_target = torch.sqrt(target_distances_squared + shape_param_sq).unsqueeze(-1)
        
        # Compute final interpolated values using optimized matrix operations
        result = torch.sum(kernel_target * coeffs, dim=1)
        
        return result

    def phi_jump(
        self,
        phi_cpd_cell: torch.Tensor,
        grad_phi_cpd_cell: torch.Tensor,
    ):
        """
        Compute the jump in scalar values across cell faces.

        DEPRECATED: the associated loss weight (loss_phi_jump) is kept at 0 in
        every production configuration, because penalizing the jump masks real
        numerical oscillation instead of curing it.

        Args:
            phi_cpd_cell (torch.Tensor): Cell-centered scalar values [N_cells, N_variables]
            grad_phi_cpd_cell (torch.Tensor): Cell-centered gradients [N_cells, N_variables, 3]

        Returns:
            torch.Tensor: Per-cell mean of the face jumps [N_cells, N_variables]
                         (non-cyclic boundary faces contribute zero).
        """

        if phi_cpd_cell.ndim > 2:
            phi_packed, layout = pack_field_rhs(phi_cpd_cell)
            grad_packed, grad_layout = pack_gradient_rhs(grad_phi_cpd_cell)
            if grad_layout != layout:
                raise ValueError("phi and gradient layouts do not match")
            result = self.phi_jump(phi_packed, grad_packed)
            return unpack_field_rhs(result, layout)

        # Expand phi_cell for broadcasting with geometric coefficients
        phi_cpd_cell_expanded = phi_cpd_cell[:, :, None]
        
        phi_jump_loss_face = (
            (phi_cpd_cell_expanded[self.C_senders]).squeeze(2) + \
            batched_matvec(grad_phi_cpd_cell[self.C_senders], self.Cf).squeeze(2)
            -
            (phi_cpd_cell_expanded[self.F_receivers]).squeeze(2) - \
            batched_matvec(grad_phi_cpd_cell[self.F_receivers], self.Ff).squeeze(2)
        ) # [N_faces, N_variables]
        
        # Exclude only non-cyclic physical boundaries. Cyclic faces now behave like
        # coupled/internal faces with face-centered CPD positions and paired-owner averages.
        # Use torch.where for torch.compile compatibility (avoid in-place indexing)
        boundary_mask_expanded = self.mask_boundary_non_cyclic_face.unsqueeze(1).expand_as(phi_jump_loss_face)
        phi_jump_loss_face = torch.where(boundary_mask_expanded, torch.zeros_like(phi_jump_loss_face), phi_jump_loss_face)
        
        # Use torch_geometric.utils.scatter for unified API
        phi_jump_loss_cell = scatter(
            phi_jump_loss_face[self.cells_face],
            self.cells_face_ptr,
            dim=0,
            dim_size=self.num_cells,
            reduce='mean',
        )
        
        return phi_jump_loss_cell
    
    def div_phic_to_faces(
        self,
        uvw_flux_face: torch.Tensor,
        phi_cpd_cell: torch.Tensor,
        grad_phi_cpd_cell: torch.Tensor,
        grad_phi_face: torch.Tensor,
        scheme: str = "Linear",
    ) -> torch.Tensor:
        """
        Interpolate scalar/vector field from cell centers to face centers for convection term.

        Unified implementation supporting both scalar fields (e.g., temperature [N, 1])
        and vector fields (e.g., velocity [N, 3]) with consistent tensor operations.

        All upwind-based schemes follow the unified formula:
            phi_face = phi_upwind + weight * (grad · r)
        where:
            - phi_upwind: upstream cell value based on flux direction
            - grad: gradient used for reconstruction (scheme-dependent)
            - r: vector from upwind cell center to face center
            - weight: blending factor (0 for pure upwind, 1 for full reconstruction)

        Args:
            uvw_flux_face: Normal velocity flux at faces [N_faces, 1]
            phi_cpd_cell: Cell-centered field values [N_cpd_cells, N_vars]
            grad_phi_cpd_cell: Cell-centered gradients [N_cpd_cells, N_vars, 3]
            grad_phi_face: Face-centered gradients [N_faces, N_vars, 3]
            scheme: Interpolation scheme. Options:
                - "Linear", "Linear corrected", "Linear grad": central schemes
                - "Upwind": first-order upwind
                - "LinearUpwind": second-order upwind (OpenFOAM style)
                - "SOU": Second Order Upwind
                - "Quick": Quadratic Upstream Interpolation
                - "LimitedLinear k": TVD limited scheme (works for scalar and vector)

        Returns:
            Face-centered field values [N_faces, N_vars]
        """
        # === Central schemes: delegate to interpolating_phic_to_faces ===
        if scheme in ("Linear", "Linear corrected", "Linear grad"):
            return self.interpolating_phic_to_faces(
                phi_cpd_cell=phi_cpd_cell,
                grad_phi_cpd_cell=grad_phi_cpd_cell,
                scheme=scheme,
            )

        # === Common setup for all upwind-based schemes ===
        if hasattr(self, "_all_boundary_non_cyclic_faces"):
            all_boundary_non_cyclic_faces = self._all_boundary_non_cyclic_faces
            boundary_non_cyclic_cpd_indices = (
                self._boundary_non_cyclic_cpd_indices
            )
        else:
            # Lightweight unit fixtures may construct an interpolation object
            # without calling register_geometrics(). Production solvers always
            # take the cached branch above.
            all_boundary_non_cyclic_faces = bool(
                torch.all(self.mask_boundary_non_cyclic_face).item()
            )
            boundary_non_cyclic_cpd_indices = (
                self.mask_boundary_non_cyclic_cpd_cell.nonzero(
                    as_tuple=False
                ).squeeze(-1)
            )
        kernel_kwargs = dict(
            face_flux=uvw_flux_face,
            face_owner=self.C_senders,
            face_neighbor=self.F_receivers,
            owner_to_face=self.Cf,
            neighbor_to_face=self.Ff,
            all_boundary_non_cyclic=all_boundary_non_cyclic_faces,
            boundary_cpd_indices=boundary_non_cyclic_cpd_indices,
            boundary_face_mask=self.mask_boundary_non_cyclic_face,
        )

        if scheme == "Upwind":
            return upwind_face_value(phi_cpd_cell, grad_phi_cpd_cell, **kernel_kwargs)
        elif scheme == "LinearUpwind":
            return linear_upwind_face_value(phi_cpd_cell, grad_phi_cpd_cell, **kernel_kwargs)
        elif scheme == "SOU":
            return second_order_upwind_face_value(phi_cpd_cell, grad_phi_cpd_cell, grad_phi_face, **kernel_kwargs)
        elif scheme == "Quick":
            return quick_face_value(phi_cpd_cell, grad_phi_cpd_cell, grad_phi_face, **kernel_kwargs)

        elif scheme.startswith("LimitedLinear"):
            parts = scheme.split()
            if len(parts) < 2:
                raise ValueError(f"LimitedLinear/LimitedLinearV requires parameter k, e.g., 'LimitedLinear 1'")
            kernel = (limited_linear_v_face_value if parts[0] == "LimitedLinearV"
                      else limited_linear_face_value)
            smooth = getattr(self, "limiter_smoothing", None)
            if smooth is not None and kernel is limited_linear_face_value:
                kernel_kwargs["smooth"] = smooth   # [fvSchemes] limiterSmoothing (scalar fields only)
            return kernel(
                phi_cpd_cell, grad_phi_cpd_cell,
                k=float(parts[1]),
                owner_weight=self.gC,
                neighbor_weight=self.gF,
                cell_pos=self.cpd_cell_pos,
                **kernel_kwargs,
            )

        else:
            raise ValueError(f"Unsupported scheme: {scheme}")

    def cfc_smooth(
        self,
        uvwp_face: torch.Tensor=None,
        uvwp_cpd_cell: torch.Tensor=None,
    ) -> torch.Tensor:
        """
        Cell-face-cell (CFC) smoothing to improve solution accuracy.

        Rebuilds each cell value as the arithmetic mean of its surrounding face
        values (EMPTY and CYCLIC faces excluded). This helps to reduce
        oscillations and improve stability.

        Args:
            uvwp_face (torch.Tensor): Face-centered solution values [N_faces, N_variables]
            uvwp_cpd_cell (torch.Tensor): Unused; kept for call-site compatibility.

        Returns:
            torch.Tensor: Smoothed values on the cpd-cell layout
                         [N_cpd_cells, N_variables], zero on boundary ghost
                         cells (boundary conditions are re-applied afterwards).
        """
        
        if uvwp_face.ndim > 2:
            face_packed, layout = pack_field_rhs(uvwp_face)
            cpd_packed = None
            if uvwp_cpd_cell is not None:
                cpd_packed, cpd_layout = pack_field_rhs(uvwp_cpd_cell)
                if cpd_layout != layout:
                    raise ValueError("face and cpd layouts do not match")
            result = self.cfc_smooth(face_packed, cpd_packed)
            return unpack_field_rhs(result, layout)

        # twoway_cpd_neigbor_cell = torch.cat((self.neighbor_cpd_cell,self.neighbor_cpd_cell.flip(0)),dim=1)
        # uvwp_new_smoothed_cpd_cell = (uvwp_cpd_cell + scatter_add(
        #     src = uvwp_cpd_cell[twoway_cpd_neigbor_cell[1]],
        #     index = twoway_cpd_neigbor_cell[0],
        #     dim = 0,
        # ))/(1+scatter_add(
        #     src = torch.ones_like(twoway_cpd_neigbor_cell[1]),
        #     index = twoway_cpd_neigbor_cell[0],
        #     dim = 0,
        # )[:,None])
        mask_valid_face = ~self.mask_empty_face & (self.face_type != FaceType.CYCLIC)
        twoway_cpd_neigbor_cell = torch.cat((self.neighbor_cpd_cell[:,mask_valid_face],self.neighbor_cpd_cell.flip(0)[:,mask_valid_face]),dim=1)
        uvwp_new_smoothed_cpd_cell = scatter(
            src = torch.cat((uvwp_face[mask_valid_face],uvwp_face[mask_valid_face]),dim=0),
            index = twoway_cpd_neigbor_cell[0],
            dim = 0,
            dim_size = self.num_cpd_cells,
            reduce='mean',
        )
        # Rebuild CPD field strictly from interior values; boundary will be reapplied later
        uvwp_clean = uvwp_new_smoothed_cpd_cell.new_zeros((self.num_cpd_cells, uvwp_new_smoothed_cpd_cell.shape[1]))
        uvwp_clean[self.mask_interior_cell] = uvwp_new_smoothed_cpd_cell[self.mask_interior_cell]
        return uvwp_clean
