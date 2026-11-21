import functools
import torch
from torch.utils.weak import WeakTensorKeyDictionary
from torch_geometric.utils import scatter
from FVsolver.FVdiscretization.FVorder import moments_order
from FVsolver.FVdiscretization.tensor_layout import (
    pack_field_rhs,
    pack_gradient_rhs,
    unpack_field_rhs,
    unpack_gradient_rhs,
)

SMALL = 1e-8  # Small number to avoid division by zero


# ==============================================================================
# Cell Limited Gradient - Limiter Functions
# ==============================================================================
# Reference: OpenFOAM cellLimitedGrad implementation
# These functions prevent gradient reconstruction from creating new extrema


def limiter_minmod(r: torch.Tensor) -> torch.Tensor:
    """
    Minmod gradient limiter - most conservative, TVD.
    
    Formula: limiter = min(r, 1)
    
    Properties:
    - Strictly TVD (Total Variation Diminishing)
    - Reduces to first-order accuracy at extrema
    - Most robust for discontinuities/shocks
    
    Args:
        r: Ratio of maxDelta/extrapolate or minDelta/extrapolate
        
    Returns:
        Limiter value in [0, 1]
    """
    return torch.clamp(r, max=1.0)


def limiter_venkatakrishnan(r: torch.Tensor) -> torch.Tensor:
    """
    Venkatakrishnan gradient limiter - smooth but may exceed 1.
    
    Formula: limiter = (r² + 2r) / (r² + r + 2)
    
    Reference:
        Venkatakrishnan, V. (1993). On the accuracy of limiters and 
        convergence to steady state solutions. 31st Aerospace Sciences Meeting.
    
    Properties:
    - Smooth and differentiable
    - May slightly exceed 1 (clipped here for safety)
    - Better convergence for steady-state problems
    
    Args:
        r: Ratio of maxDelta/extrapolate or minDelta/extrapolate
        
    Returns:
        Limiter value in [0, 1]
    """
    r2 = r * r
    limiter = (r2 + 2.0 * r) / (r2 + r + 2.0)
    return torch.clamp(limiter, max=1.0)


def limiter_cubic(r: torch.Tensor, rt: float = 1.5) -> torch.Tensor:
    """
    Cubic gradient limiter - smooth, bounded, and recommended.
    
    Formula: 
        if r < rt: limiter = ((a*r + b)*r + 1)*r
        else: limiter = 1
        
    where a = (rt - 2) / rt³, b = -(3*a*rt² + 1) / (2*rt)
    
    Reference:
        Michalak, K., & Ollivier-Gooch, C. (2008). Limiters for unstructured 
        higher-order accurate solutions of the Euler equations. 
        46th AIAA Aerospace Sciences Meeting. DOI:10.2514/6.2008-776
    
    Properties:
    - Smooth and differentiable
    - Strictly bounded by 1
    - Better accuracy than minmod
    - OpenFOAM recommended over Venkatakrishnan
    
    Args:
        r: Ratio of maxDelta/extrapolate or minDelta/extrapolate
        rt: Transition point where limiter reaches 1 (default 1.5, must be > 1)
        
    Returns:
        Limiter value in [0, 1]
    """
    # Compute coefficients
    rt3 = rt * rt * rt
    a = (rt - 2.0) / rt3
    b = -(3.0 * a * rt * rt + 1.0) / (2.0 * rt)
    
    # Apply cubic formula where r < rt, else 1
    cubic_val = ((a * r + b) * r + 1.0) * r
    limiter = torch.where(r < rt, cubic_val, torch.ones_like(r))
    return torch.clamp(limiter, min=0.0, max=1.0)


def cell_limited_gradient(
    grad_cell: torch.Tensor,
    phi_cell: torch.Tensor,
    neighbor_cell: torch.Tensor,
    cell_pos: torch.Tensor,
    face_pos: torch.Tensor,
    mask_interior_face: torch.Tensor = None,
    mask_interior_cell: torch.Tensor = None,
    k: float = 1.0,
    limiter_type: str = 'minmod',
    rt: float = 1.5,
) -> torch.Tensor:
    """
    Apply cell-based gradient limiting to prevent non-physical extrema.
    
    This is a post-processing step applied to gradients computed by WLSQ or 
    Green-Gauss methods. It limits the gradient so that face-extrapolated 
    values do not exceed the min/max of neighboring cell values.
    
    IMPORTANT: This function is designed for cpd_cell-based systems where 
    boundary faces are treated as pseudo-cells. The limiting is ONLY applied 
    to interior cells (real cells), NOT to boundary faces (pseudo-cells).
    
    In the cpd_cell system:
    - Interior cells: real cell centers where gradient is computed via WLSQ
    - Boundary faces: treated as pseudo-cells with face-center positions
      Their gradients are directly computed by WLSQ, not extrapolated from cells.
    
    Reference: OpenFOAM cellLimitedGrad (src/finiteVolume/finiteVolume/gradSchemes/
               limitedGradSchemes/cellLimitedGrad/)
    
    Algorithm:
    1. For each INTERIOR cell, find max/min values among its neighbors
    2. Compute maxDelta = max(neighbors) - cell_value
    3. Compute minDelta = min(neighbors) - cell_value
    4. For each face, compute extrapolated value: extrap = (face_pos - cell_pos) · grad
    5. Compute limiter ratio r = delta/extrap and apply limiter function
    6. Take minimum limiter across all faces of each cell
    7. Apply: grad_limited = grad * limiter (only for interior cells)
    
    Args:
        grad_cell: [N_cpd, C, 3] Gradient tensor from WLSQ (for all cpd_cells)
        phi_cell: [N_cpd, C] cpd_cell-centered field values
        neighbor_cell: [2, E] Face connectivity (owner_cpd, neighbor_cpd)
        cell_pos: [N_cpd, 3] cpd_cell center positions (includes boundary face centers)
        face_pos: [E, 3] Face center positions
        mask_interior_face: [E] Boolean mask for interior faces (optional)
        mask_interior_cell: [N_cpd] Boolean mask for interior cells (REQUIRED for cpd_cell system)
                           True = real interior cell, False = boundary face pseudo-cell
        k: Limiter coefficient (0=no limiting, 1=full limiting). 
           Values < 1 allow some overshoot. Default 1.0.
        limiter_type: 'minmod', 'venkatakrishnan', or 'cubic'. Default 'minmod'.
        rt: Transition point for cubic limiter. Default 1.5.
        
    Returns:
        grad_limited: [N_cpd, C, 3] Limited gradient tensor
                     (only interior cell gradients are modified, boundary face gradients unchanged)
        
    Example:
        >>> grad = weighted_lstsq(phi_node=phi, edge_index=edge_index,
        ...                       node_pos=cell_pos, order="1st")
        >>> grad_limited = cell_limited_gradient(
        ...     grad, phi, neighbor_cell, cell_pos, face_pos,
        ...     mask_interior_cell=mask_interior_cell,
        ...     k=1.0, limiter_type='cubic'
        ... )
    """
    # Limiting is spatially independent for every leading (time/batch) item.
    # Pack those items into multiple RHS channels and reuse the exact legacy
    # rank-2 implementation below; static topology/geometry is not repeated.
    if phi_cell.ndim > 2:
        phi_packed, layout = pack_field_rhs(phi_cell)
        grad_packed, grad_layout = pack_gradient_rhs(grad_cell)
        if grad_layout != layout:
            raise ValueError(
                "phi_cell and grad_cell leading/channel layouts differ: "
                f"phi={tuple(phi_cell.shape)}, grad={tuple(grad_cell.shape)}"
            )
        limited = cell_limited_gradient(
            grad_cell=grad_packed,
            phi_cell=phi_packed,
            neighbor_cell=neighbor_cell,
            cell_pos=cell_pos,
            face_pos=face_pos,
            mask_interior_face=mask_interior_face,
            mask_interior_cell=mask_interior_cell,
            k=k,
            limiter_type=limiter_type,
            rt=rt,
        )
        return unpack_gradient_rhs(limited, layout)

    if k < SMALL:
        # No limiting
        return grad_cell
    
    device = grad_cell.device
    num_cells = phi_cell.shape[0]
    num_channels = phi_cell.shape[1]
    num_faces = neighbor_cell.shape[1]
    
    owner = neighbor_cell[0]      # [E]
    neighbor = neighbor_cell[1]   # [E]
    
    # ===========================================================================
    # Step 1: Compute max/min values among neighbors for each cell
    # ===========================================================================
    # Initialize with cell's own value
    maxVsf = phi_cell.clone()  # [N, C]
    minVsf = phi_cell.clone()  # [N, C]
    
    # Get neighbor values
    phi_owner = phi_cell[owner]      # [E, C]
    phi_neighbor = phi_cell[neighbor]  # [E, C]
    
    # For interior faces, update max/min from both directions
    # Using native PyTorch scatter_reduce for torch.compile compatibility
    if mask_interior_face is not None:
        # Only consider interior faces for neighbor comparison
        interior_owner = owner[mask_interior_face]
        interior_neighbor = neighbor[mask_interior_face]
        phi_int_owner = phi_owner[mask_interior_face]
        phi_int_neighbor = phi_neighbor[mask_interior_face]
        
        # Owner side: update with neighbor values
        max_from_nei = scatter(phi_int_neighbor, interior_owner, dim=0, dim_size=num_cells, reduce='max')
        min_from_nei = scatter(phi_int_neighbor, interior_owner, dim=0, dim_size=num_cells, reduce='min')
        maxVsf = torch.max(maxVsf, max_from_nei)
        minVsf = torch.min(minVsf, min_from_nei)
        
        # Neighbor side: update with owner values
        max_from_own = scatter(phi_int_owner, interior_neighbor, dim=0, dim_size=num_cells, reduce='max')
        min_from_own = scatter(phi_int_owner, interior_neighbor, dim=0, dim_size=num_cells, reduce='min')
        maxVsf = torch.max(maxVsf, max_from_own)
        minVsf = torch.min(minVsf, min_from_own)
    else:
        # All faces contribute
        max_from_nei = scatter(phi_neighbor, owner, dim=0, dim_size=num_cells, reduce='max')
        min_from_nei = scatter(phi_neighbor, owner, dim=0, dim_size=num_cells, reduce='min')
        maxVsf = torch.max(maxVsf, max_from_nei)
        minVsf = torch.min(minVsf, min_from_nei)
        
        max_from_own = scatter(phi_owner, neighbor, dim=0, dim_size=num_cells, reduce='max')
        min_from_own = scatter(phi_owner, neighbor, dim=0, dim_size=num_cells, reduce='min')
        maxVsf = torch.max(maxVsf, max_from_own)
        minVsf = torch.min(minVsf, min_from_own)
    
    # ===========================================================================
    # Step 2: Compute delta = max/min - cell_value
    # ===========================================================================
    maxDelta = maxVsf - phi_cell  # [N, C]
    minDelta = minVsf - phi_cell  # [N, C]
    
    # Optional: relax limits with k < 1
    if k < 1.0:
        relaxation = (1.0 / k - 1.0) * (maxDelta - minDelta)
        maxDelta = maxDelta + relaxation
        minDelta = minDelta - relaxation
    
    # ===========================================================================
    # Step 3: Initialize limiter to 1 for all cells
    # ===========================================================================
    limiter = torch.ones((num_cells, num_channels), device=device, dtype=grad_cell.dtype)
    
    # ===========================================================================
    # Step 4: For each face, compute extrapolated value and update limiter
    # ===========================================================================
    # d_CF = face_pos - cell_pos[owner]  -> [E, 3]
    d_CF = face_pos - cell_pos[owner]
    
    # extrapolate = d_CF · grad[owner]  -> [E, C]
    # grad_cell[owner] is [E, C, 3], d_CF is [E, 3]
    # We need (d_CF * grad[owner]).sum(dim=-1) -> [E, C]
    extrapolate_owner = (d_CF.unsqueeze(1) * grad_cell[owner]).sum(dim=-1)  # [E, C]
    
    # Get max/min delta for owner cells
    maxDelta_owner = maxDelta[owner]  # [E, C]
    minDelta_owner = minDelta[owner]  # [E, C]
    
    # Compute r ratio for each component
    # When extrapolate > 0, we need maxDelta/extrapolate
    # When extrapolate < 0, we need minDelta/extrapolate
    r_owner = torch.ones_like(extrapolate_owner)
    
    # Positive extrapolation case
    pos_mask = extrapolate_owner > SMALL
    r_owner = torch.where(pos_mask, maxDelta_owner / (extrapolate_owner + SMALL), r_owner)
    
    # Negative extrapolation case
    neg_mask = extrapolate_owner < -SMALL
    r_owner = torch.where(neg_mask, minDelta_owner / (extrapolate_owner - SMALL), r_owner)
    
    # Ensure r is non-negative
    r_owner = torch.clamp(r_owner, min=0.0)
    
    # Apply limiter function
    if limiter_type == 'minmod':
        face_limiter_owner = limiter_minmod(r_owner)
    elif limiter_type == 'venkatakrishnan':
        face_limiter_owner = limiter_venkatakrishnan(r_owner)
    elif limiter_type == 'cubic':
        face_limiter_owner = limiter_cubic(r_owner, rt)
    else:
        raise ValueError(f"Unknown limiter type: {limiter_type}. "
                        f"Choose from 'minmod', 'venkatakrishnan', 'cubic'")
    
    # Scatter minimum to cells (owner side)
    cell_limiter_from_owner = scatter(face_limiter_owner, owner, dim=0, dim_size=num_cells, reduce='min')
    limiter = torch.min(limiter, cell_limiter_from_owner)
    
    # Also process neighbor side for internal faces
    if mask_interior_face is not None:
        # d_FC = face_pos - cell_pos[neighbor] for interior faces
        int_face_pos = face_pos[mask_interior_face]
        int_neighbor = neighbor[mask_interior_face]
        d_FC = int_face_pos - cell_pos[int_neighbor]
        
        extrapolate_neighbor = (d_FC.unsqueeze(1) * grad_cell[int_neighbor]).sum(dim=-1)
        maxDelta_neighbor = maxDelta[int_neighbor]
        minDelta_neighbor = minDelta[int_neighbor]
        
        r_neighbor = torch.ones_like(extrapolate_neighbor)
        pos_mask_n = extrapolate_neighbor > SMALL
        r_neighbor = torch.where(pos_mask_n, maxDelta_neighbor / (extrapolate_neighbor + SMALL), r_neighbor)
        neg_mask_n = extrapolate_neighbor < -SMALL
        r_neighbor = torch.where(neg_mask_n, minDelta_neighbor / (extrapolate_neighbor - SMALL), r_neighbor)
        r_neighbor = torch.clamp(r_neighbor, min=0.0)
        
        if limiter_type == 'minmod':
            face_limiter_neighbor = limiter_minmod(r_neighbor)
        elif limiter_type == 'venkatakrishnan':
            face_limiter_neighbor = limiter_venkatakrishnan(r_neighbor)
        elif limiter_type == 'cubic':
            face_limiter_neighbor = limiter_cubic(r_neighbor, rt)
        
        cell_limiter_from_neighbor = scatter(face_limiter_neighbor, int_neighbor, dim=0, dim_size=num_cells, reduce='min')
        limiter = torch.min(limiter, cell_limiter_from_neighbor)
    
    # ===========================================================================
    # Step 5: Apply limiter to gradient (ONLY for interior cells)
    # ===========================================================================
    # limiter is [N, C], grad_cell is [N, C, 3]
    # 
    # IMPORTANT: In cpd_cell systems, boundary faces are pseudo-cells.
    # Their gradients are computed directly by WLSQ at face positions,
    # NOT extrapolated from cell centers. Therefore, cell limiting should
    # ONLY be applied to real interior cells, not to boundary face gradients.
    
    if mask_interior_cell is not None:
        # Only modify interior cell gradients; boundary face gradients unchanged
        # Use torch.where for torch.compile compatibility (avoids nonzero graph break)
        mask_expanded = mask_interior_cell.unsqueeze(-1).unsqueeze(-1)  # [N, 1, 1]
        limited_grad = grad_cell * limiter.unsqueeze(-1)  # Apply limiter to all
        grad_limited = torch.where(mask_expanded, limited_grad, grad_cell)
    else:
        # No mask provided: apply to all (original behavior, for backward compatibility)
        grad_limited = grad_cell * limiter.unsqueeze(-1)
    
    return grad_limited


# ==============================================================================
# Main Gradient Reconstruction API
# ==============================================================================

def _lazy_compile(**opts):
    """``torch.compile(**opts)`` applied at the first call, not at import: decorating at import loads dynamo and
    inductor (~3 s per process) even for the runs that never call the function (the Warp backends)."""
    def deco(fn):
        state = {}

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            if "f" not in state:
                state["f"] = torch.compile(fn, **opts)
            return state["f"](*args, **kwargs)
        return wrapper
    return deco


@_lazy_compile(mode="default", dynamic=True)
def gradient_reconstruction(order="2nd", *args, **kwargs):
    """
    Unified gradient computation API with adaptive parameter handling.
    
    Supports optional cell-based gradient limiting to prevent non-physical extrema.
    
    Parameters:
    -----------
    order : str
        Gradient computation method and accuracy:
        - "1st": WLSQ with 1st order accuracy
        - "2nd": WLSQ with 2nd order accuracy  
        - "3rd": WLSQ with 3rd order accuracy
        - "4th": WLSQ with 4th order accuracy
        - "GG": Green-Gauss gradient method
    *args : tuple
        Positional arguments passed to the underlying gradient function
    **kwargs : dict
        Keyword arguments passed to the underlying gradient function
        
        For cell limiting (GATED OFF: passing cell_limited=True raises
        NotImplementedError; the implementation is kept in this file but is
        not production-ready, keep [fvSchemes.gradLimiter].enabled = false):
        - cell_limited: bool, whether to apply cell limiting (default False)
        - limiter_type: str, 'minmod'/'venkatakrishnan'/'cubic' (default 'minmod')
        - limiter_k: float, limiting coefficient 0-1 (default 1.0)
        - limiter_rt: float, cubic limiter transition point (default 1.5)
        - neighbor_cell: [2, E] face connectivity for limiting
        - face_pos: [E, 3] face center positions for limiting
        - mask_interior_face: [E] bool mask for interior faces
        - mask_interior_cell: [N] bool mask for interior cells (IMPORTANT for cpd_cell systems)
          In cpd_cell systems, boundary faces are treated as pseudo-cells.
          Their gradients are computed directly by WLSQ at face positions,
          NOT extrapolated from cell centers. Therefore, cell limiting should
          ONLY be applied to interior cells. If provided, only interior cell
          gradients will be limited; boundary face gradients remain unchanged.
        
    Returns:
    --------
    gradients : array
        WLSQ orders ("1st".."4th"): [N_cpd_cells, C, 3], zero on the cells
        excluded by mask_valid_node. "GG": [N_interior_cells, C, 3] --
        Green-Gauss only produces interior-cell gradients.

    Examples:
    ---------
    Basic WLSQ gradient:
    >>> grad = gradient_reconstruction("1st", phi_node=field, edge_index=edges, 
    ...                                node_pos=positions)
    
    Cell-limited gradient (currently disabled -- raises NotImplementedError):
    >>> grad = gradient_reconstruction("1st",
    ...     phi_node=field, edge_index=edges, node_pos=positions,
    ...     cell_limited=True, limiter_type='cubic',
    ...     neighbor_cell=neighbor, face_pos=face_pos,
    ...     mask_interior_face=mask_interior
    ... )
    
    The function automatically routes to the appropriate gradient computation method
    and forwards all arguments without modification.
    """
    
    # Extract cell limiting parameters (if provided)
    cell_limited = kwargs.pop('cell_limited', False)
    if cell_limited:
        raise NotImplementedError(
            "Cell-limited gradient is not production-ready in this framework. "
            "Set [fvSchemes.gradLimiter].enabled = false in fvconfig.toml. "
            "The limiter implementation (cell_limited_gradient) is preserved in "
            "this file but gated off pending validation."
        )
    limiter_type = kwargs.pop('limiter_type', 'minmod')
    limiter_k = kwargs.pop('limiter_k', 1.0)
    limiter_rt = kwargs.pop('limiter_rt', 1.5)
    neighbor_cell = kwargs.pop('neighbor_cell', None)
    face_pos = kwargs.pop('face_pos', None)
    mask_interior_face = kwargs.pop('mask_interior_face', None)
    mask_interior_cell = kwargs.pop('mask_interior_cell', None)  # For cpd_cell systems
    
    # Validate input parameters
    valid_orders = ["1st", "2nd", "3rd", "4th", "GG"]
    if order not in valid_orders:
        raise ValueError(f"Invalid order '{order}'. Must be one of {valid_orders}")
    
    # Route to appropriate gradient computation method
    if order == "GG":
        # Use Green-Gauss gradient method - forward all arguments
        grad = green_gauss_gradient(*args, **kwargs)
    else:
        kwargs['order'] = order
        grad = weighted_lstsq(*args, **kwargs)
    
    # Apply cell limiting if requested
    if cell_limited:
        if neighbor_cell is None or face_pos is None:
            raise ValueError("cell_limited=True requires 'neighbor_cell' and 'face_pos' arguments")
        
        # Get phi_node and node_pos from kwargs for limiting
        phi_node = kwargs.get('phi_node')
        node_pos = kwargs.get('node_pos')
        
        if phi_node is None or node_pos is None:
            raise ValueError("cell_limited=True requires 'phi_node' and 'node_pos' arguments")
        
        grad = cell_limited_gradient(
            grad_cell=grad,
            phi_cell=phi_node,
            neighbor_cell=neighbor_cell,
            cell_pos=node_pos,
            face_pos=face_pos,
            mask_interior_face=mask_interior_face,
            mask_interior_cell=mask_interior_cell,  # For cpd_cell systems: only limit interior cell gradients
            k=limiter_k,
            limiter_type=limiter_type,
            rt=limiter_rt,
        )
    
    return grad


def compute_normal_matrix(
    order="1st",
    node_pos=None,
    edge_index=None, # khops=1(默认)时为1-hop面邻接; khops>=2 时为H5里解析期烘焙的宽模板
):
    """
    Compute the normal matrices A and B for node-based weighted least squares (WLSQ) gradient reconstruction.
    Args:
        order (str): Order of reconstruction ('1st', '2nd', '3rd', '4th').
        node_pos (Tensor): [N, D] Node positions.
        edge_index (Tensor): [2, E] Edge indices (one-way; both directions are
            assembled internally).
    Returns:
        Tuple of (A_cell_to_cell, B_cell_to_cell), where B is the two-way
        [2*E, x] block (forward edges first, flipped edges second).
    """
    
    twoway_edge_index = torch.cat((edge_index,edge_index.flip(0)),dim=1)

    outdegree_node_index, indegree_node_index = twoway_edge_index[0], twoway_edge_index[1]   
    
    node_pos_diff_on_edge = node_pos[outdegree_node_index] - node_pos[indegree_node_index]
    
    # L_local = scatter_mean(src=node_pos_diff_on_edge**2,index=indegree_node_index,dim=0).sqrt()
    
    # normaled_node_pos_diff_on_edge = node_pos_diff_on_edge/(L_local[indegree_node_index])
    
    (A_cell_to_cell, B_cell_to_cell) = moments_order(
        order=order,
        num_nodes=node_pos.shape[0],
        node_pos_diff_on_edge=node_pos_diff_on_edge,
        indegree_node_index=indegree_node_index,
    )

    return (A_cell_to_cell, B_cell_to_cell)

class _WlsqSolve(torch.autograd.Function):
    """``A^-1 B`` against a cached explicit inverse of the WLSQ normal matrix.

    ``A`` is a mesh constant -- ``moments_order`` builds it from
    ``node_pos_diff_on_edge`` and inverse-distance weights, the state enters
    only through ``B``, and ``mask_valid_node`` substitutes a fixed identity
    block.  Verified, not assumed: captured at the cold state, after four
    Newton steps, and at a randomly perturbed state, the operand is bitwise
    identical all three times (max diff 0.0) while its RHS moves by 7.7
    (heated cylinder).  A constant ``A`` makes ``B -> A^-1 B``
    linear, so every derivative order the Jacobian assembly walks is this class
    or its transpose, and both are one cached multiply.

    WHY THIS AND NOT A SOLVE.  The shipped ``torch.linalg.solve_ex`` already
    reuses its factorisation -- autograd saves the LU from the forward, so the
    backward and the double-backward are triangular solves, not refactorisations.
    Caching the factorisation therefore buys nothing (measured: bitwise
    identical matrices, assembly 5.204 s against 5.339 s, inside the noise).
    The cost is cuBLAS batched ``getrs`` itself on 172,640 systems of rank 3:
    2.35 ms per probe, 15% of a probe's device time.  Applying a cached inverse
    instead costs 0.044 ms.

    NOT BITWISE, and that is the price.  Assembled entries move by 1e-16
    relative, and 16 entries of F and J that were structurally zero become
    ~4e-20, so the sparsity pattern gains 16 of 61,098,440 nonzeros.  Measured
    consequence on the run: none detectable -- 34 Newton steps and 11506 GMRES
    iterations either way, final max|r| 4.5841e-13 against 4.5841e-13.

    Cholesky was measured and rejected despite the SPD structure the normal
    equations have in exact arithmetic.  The assembled matrix is symmetric only
    to 1.9e-9 (scatter-accumulated with atomics, in float32 before the double
    promotion), so ``cholesky_solve`` discards that part and lands 3.6e-8
    relative away -- and cuSOLVER's batched ``potrs`` costs 2.970 ms against
    ``lu_solve``'s 0.029 ms.  Wrong AND 100x slower.
    """

    @staticmethod
    def forward(ctx, rhs, inverse, inverse_t):
        ctx.save_for_backward(inverse, inverse_t)
        return _apply_small(inverse, rhs)

    @staticmethod
    def backward(ctx, grad):
        inverse, inverse_t = ctx.saved_tensors
        return _WlsqSolveTransposed.apply(grad, inverse_t, inverse), None, None


class _WlsqSolveTransposed(torch.autograd.Function):
    """The adjoint of :class:`_WlsqSolve`; the two are each other's derivative."""

    @staticmethod
    def forward(ctx, rhs, inverse, inverse_t):
        ctx.save_for_backward(inverse, inverse_t)
        return _apply_small(inverse, rhs)

    @staticmethod
    def backward(ctx, grad):
        inverse, inverse_t = ctx.saved_tensors
        return _WlsqSolve.apply(grad, inverse_t, inverse), None, None


def _apply_small(matrix, rhs):
    """``[N,K,K] x [N,K,C]`` as K broadcast column products.

    K is 2 or 3, so unrolling the contraction beats every batched-GEMM route:
    0.0439 ms on ``[172640,3,5]`` float64 against ``bmm`` / ``matmul`` /
    ``einsum`` all at 0.284 ms and cuBLAS batched ``getrs`` at 1.091 ms
    (measured).  That is 68% of the pure memory-traffic floor
    for the operands, so there is no third option worth trying.  The loop runs
    over the STATIC rank, so it unrolls at trace time.
    """
    out = matrix[:, :, 0:1] * rhs[:, 0:1, :]
    for k in range(1, matrix.shape[-1]):
        out = out + matrix[:, :, k:k + 1] * rhs[:, k:k + 1, :]
    return out


_WLSQ_INVERSE_CACHE = WeakTensorKeyDictionary()

# Forward-mode AD through the residual (the JFNK operator's ``jacobian.linearise_forward``).  While
# it is > 0, ``weighted_lstsq`` applies the cached inverse with the plain ops of ``_apply_small`` -- which forward
# AD differentiates, ``A^-1`` a constant as in ``_WlsqSolve.backward`` -- instead of through ``_WlsqSolve``, a
# custom Function without a ``jvp``.  Giving it one was the alternative and was not taken: torch.compile does not
# trace a user-defined ``jvp`` and would graph-break on every path that compiles this module.  The values are the
# same to the bit (``_WlsqSolve.forward`` IS ``_apply_small``); at 0 -- every caller but that one -- the code is
# the code that ran before.  Set only through ``forward_mode_wlsq``.
_FORWARD_MODE_DEPTH = 0


class forward_mode_wlsq:
    """``with forward_mode_wlsq():`` -- the WLSQ solve in forward-mode-differentiable ops (see above)."""

    def __enter__(self):
        global _FORWARD_MODE_DEPTH
        _FORWARD_MODE_DEPTH += 1
        return self

    def __exit__(self, *exc):
        global _FORWARD_MODE_DEPTH
        _FORWARD_MODE_DEPTH -= 1
        return False


def _wlsq_inverse(a_solve, source, mask_valid_node, dim):
    """The inverse of the WLSQ normal matrix, built ONCE per mesh.

    Keyed on the source moment tensor, with the mask and both version counters
    carried in the entry, so a rebuilt mesh, a 2D slice, or a different
    valid-cell mask rebuilds instead of reusing.  Skipped when the matrix
    carries a gradient, which would make the inversion part of the tape rather
    than a constant.

    ``inv_ex`` rather than ``inv`` because ``inv`` reads its LAPACK ``info`` on
    the HOST, which is a device synchronisation; the ``_ex`` form keeps the
    residual capturable as a CUDA graph.  Dropping the eager exception is safe
    for the same reason it is safe for the solve this replaces:
    ``mask_valid_node`` substitutes an identity system for every cell we do not
    solve for, and the rest are normal equations from mesh geometry (measured
    eigenvalues 5.3e-3 to 1.0, cond2 median 4.3 and max 18.9, so an explicit
    inverse loses about one digit against a factorisation, on a float64 field).
    A degenerate stencil is a mesh setup error and still surfaces as NaN in the
    first residual.

    CAUTION for float32 callers.  The one-digit figure above is this mesh's.
    An explicit inverse costs roughly ``log10(cond)`` digits, so a badly
    conditioned WLSQ block (high aspect ratio, tets) is a real hazard in
    float32 where a factorisation would not be.  No float32 arm has been
    measured.
    """
    def build():
        inverse = torch.linalg.inv_ex(a_solve)[0].contiguous()
        return inverse, inverse.transpose(1, 2).contiguous()

    if source is None or a_solve.requires_grad:
        return build()
    stamp = (dim, source._version,
             -1 if mask_valid_node is None else mask_valid_node._version)
    entry = _WLSQ_INVERSE_CACHE.get(source)
    if entry is not None and entry[0] == stamp and entry[1] is mask_valid_node:
        return entry[2], entry[3]
    inverse, inverse_t = build()
    _WLSQ_INVERSE_CACHE[source] = (stamp, mask_valid_node, inverse, inverse_t)
    return inverse, inverse_t


def weighted_lstsq(
    phi_node=None,
    edge_index=None, # 输入的edge_index是否是双向的,注意对于edge_index一定是0-1
    mask_valid_node=None, # 只在需要求梯度的点求梯度
    node_pos=None,
    order=None,
    precompute_Moments: list = None, # 应包含2个元素，[A, 单向的B]（另一半B在内部由翻转边补齐）
    rt_cond=False,
    dim:int=3, # 2D or 3D
):
    '''
    Weighted Least Squares (WLSQ) gradient reconstruction.
    Args:
        phi_node (Tensor): [N, C] Node values.
        edge_index (Tensor): [2, E] Edge indices (one-way).
        mask_valid_node (Tensor, optional): [N] Boolean mask selecting the
            nodes/cells the gradient is actually solved for. Excluded entries
            get an identity system and therefore a zero gradient. The NS
            solvers pass mask_interior_cell, so ghost (boundary) cpd cells
            receive NO WLSQ gradient; cyclic ghosts are refilled afterwards by
            the solver's enforce_cyclic pass.
        node_pos (Tensor): [N, D] Node positions.
        order (str): Order of reconstruction ('1st', '2nd', '3rd', '4th').
        precompute_Moments (list, optional): Precomputed [A, one-way B] moments.
        rt_cond (bool): If True, also return condition number.
    Returns:
        nabla_phi_node_lst (Tensor): [N, C, ...] Node gradients (shape depends on order).
        If rt_cond is True, also returns condition number.
    '''
    # The spatial WLSQ matrix is shared by every leading time/batch item.
    # Pack ``[..., N, C]`` to ``[N, multiple_rhs]`` so torch.linalg.solve
    # handles all time levels in one call without duplicating geometry.
    phi_node, rhs_layout = pack_field_rhs(phi_node)

    # edge_index = knn_graph(node_pos, k=9, loop=False)
    if (order is None) or (order not in ["1st", "2nd", "3rd", "4th"]):
        raise ValueError("order must be specified in [\"1st\", \"2nd\", \"3rd\", \"4th\"]")
    
    twoway_edge_index = torch.cat((edge_index,edge_index.flip(0)),dim=1)

    # Kept before the default fill below: that fill allocates a fresh all-true
    # tensor on every call, which would miss the factorisation cache forever.
    # An all-true mask leaves A_solve equal to A, so None is the right key.
    mask_argument = mask_valid_node

    if mask_valid_node is None:
        mask_valid_node = torch.ones(node_pos.shape[0], dtype=torch.bool, device=node_pos.device)

    outdegree_node_index, indegree_node_index = twoway_edge_index[0], twoway_edge_index[1]

    if precompute_Moments is None:

        """node to node contribution"""
        (A_cell_to_cell, two_way_B_cell_to_cell) = compute_normal_matrix(
            order=order,
            node_pos=node_pos,
            edge_index=edge_index, # 默认应该是仅包含1阶邻居点+构成共点的单元的所有点
        )
        B_cell_to_cell = two_way_B_cell_to_cell
        """node to node contribution"""
        
        phi_diff_on_edge = B_cell_to_cell * (
            (phi_node[outdegree_node_index] - phi_node[indegree_node_index]).unsqueeze(1)
        )

        B_phi_cell_to_cell = scatter(
            phi_diff_on_edge, indegree_node_index, dim=0, dim_size=node_pos.shape[0], reduce='sum'
        )

    else:
        """use precomputed moments"""
        A_cell_to_cell, Oneway_B_cell_to_cell = precompute_Moments

        half_dim = Oneway_B_cell_to_cell.shape[0]
        
        two_way_B_cell_to_cell = torch.cat(
            (Oneway_B_cell_to_cell, Oneway_B_cell_to_cell), dim=0
        )
        
        # 大于1阶的奇数阶项需要取负
        # For 3D: first 3 terms are linear [dx, dy, dz]
        two_way_B_cell_to_cell[half_dim:, 0:3] *= -1
        od = int(order[0])
        
        if (od >= 3) & (od < 5):
            # For 3D: cubic terms start at index 9 and go to 12 [dx^3/6, dy^3/6, dz^3/6]
            two_way_B_cell_to_cell[half_dim:, 9:12] *= -1
        elif od >= 5:
            raise ValueError("order must be in [\"1st\", \"2nd\", \"3rd\", \"4th\"], not support higher order yet")
        
        B_cell_to_cell = two_way_B_cell_to_cell
        
        phi_diff_on_edge = B_cell_to_cell * (
            (phi_node[outdegree_node_index] - phi_node[indegree_node_index]).unsqueeze(1)
        )

        B_phi_cell_to_cell = scatter(
            phi_diff_on_edge,
            indegree_node_index,
            dim=0,
            dim_size=node_pos.shape[0],
            reduce='sum',
        ) 
   
    # 行归一化
    # row_norms = torch.norm(A_cell_to_cell, p=2, dim=2, keepdim=True)
    # A_cell_to_cell = A_cell_to_cell / (row_norms)
    # B_phi_cell_to_cell = B_phi_cell_to_cell / (row_norms)
    
    # lambda_reg = 1e-5  # 正则化参数
    # I = torch.eye(A_normalized.shape[-1], device=A_normalized.device)
    # A_normalized = A_normalized + lambda_reg * I
    
    # # 列归一化
    # col_norms = torch.norm(A_normalized, p=2, dim=1, keepdim=True)
    # A_normalized = A_normalized / (col_norms)
    # B_normalized = B_normalized * col_norms
    
    # The identity the factorisation cache is keyed on, taken before the 2D
    # slice makes a fresh view of it every call.
    A_source = A_cell_to_cell

    if dim==2:
        A_cell_to_cell = A_cell_to_cell[:,:2,:2]
        B_phi_cell_to_cell = B_phi_cell_to_cell[:,:2,:]

    if mask_valid_node is not None:
        mask = mask_valid_node[:, None, None]
        eye = torch.eye(
            A_cell_to_cell.shape[1],
            device=A_cell_to_cell.device,
            dtype=A_cell_to_cell.dtype,
        ).expand(A_cell_to_cell.shape[0], -1, -1)
        A_solve = torch.where(mask, A_cell_to_cell, eye)
        B_solve = torch.where(mask, B_phi_cell_to_cell, torch.zeros_like(B_phi_cell_to_cell))
    else:
        A_solve = A_cell_to_cell
        B_solve = B_phi_cell_to_cell

    # De-alias from the solve output: ``.transpose(1, 2)`` is a VIEW that shares
    # storage (and the autograd version counter) with the solve result.
    # Downstream FVM consumers mutate this gradient in-place (cyclic-BC eval in
    # ``enforce_cyclic``; BC gradient-flux zeroing in the per-field BC modules),
    # which corrupts the saved solve output and breaks its backward
    # (``... modified by an inplace operation``).  ``.clone()`` forces fresh
    # storage so the in-place edits land on a copy.  ``.contiguous()`` alone is
    # NOT a substitute: whether it copies depends on the layout the solve
    # returned, and a no-op keeps the alias.  ``contiguous_format`` is asked for
    # explicitly because ``clone`` otherwise PRESERVES strides, and the [N,C,3]
    # this hands downstream was contiguous under the former
    # ``torch.linalg.solve_ex`` spelling (which returned its solution
    # transposed); keeping that layout keeps every consumer on the kernel it was
    # measured with.  Values are unchanged either way (clone is differentiable).
    #
    # FLOAT64 ONLY.  An explicit inverse costs about log10(cond) digits against
    # a factorisation; here that is one digit (cond2 median 4.27, p99 13.9, max
    # 18.9 over 172,640 blocks, measured) which float64 absorbs.
    # This is SHARED code -- the GNN+FVM paradigm runs it in float32 on tet
    # meshes, where WLSQ blocks are far worse conditioned (khops=1 was
    # rank-deficient there without the interior-only mask) and a
    # block a factorisation handles can lose its leading digits through an
    # inverse.  No float32 arm has been measured, so float32 keeps solve_ex.
    if A_solve.dtype is torch.float64:
        inverse, inverse_t = _wlsq_inverse(A_solve, A_source, mask_argument, dim)
        # Forward-mode AD of the JFNK operator takes the same product in plain ops
        solved = (_apply_small(inverse, B_solve) if _FORWARD_MODE_DEPTH
                  else _WlsqSolve.apply(B_solve, inverse, inverse_t))
        nabla_phi_node_lst = solved.transpose(1, 2).clone(memory_format=torch.contiguous_format)
        del solved          # the temporary it was before the switch existed
    else:
        nabla_phi_node_lst = torch.linalg.solve_ex(
            A_solve, B_solve
        )[0].transpose(1, 2).clone()

    if dim==2:
        nabla_phi_node_lst = torch.cat((nabla_phi_node_lst, torch.zeros_like(nabla_phi_node_lst[...,0:1])),dim=-1)
               
    if rt_cond:
        cond_num = torch.zeros((A_cell_to_cell.shape[0],),device=A_cell_to_cell.device)
        cond_num[mask_valid_node] = torch.linalg.cond(A_cell_to_cell[mask_valid_node]) 
        
        return unpack_gradient_rhs(nabla_phi_node_lst, rhs_layout), cond_num
    
    else:
        return unpack_gradient_rhs(nabla_phi_node_lst, rhs_layout)

    """ first method"""
    # nabla_phi_node_lst = torch.linalg.lstsq(
    #     A_normalized, B_normalized
    # ).solution.transpose(1, 2)

    """ second method"""
    # nabla_phi_node_lst = torch.matmul(A_inv_cell_to_cell_x,B_phi_cell_to_cell_x)

    """ third method"""
    nabla_phi_node_lst[mask_valid_node] = torch.linalg.solve(
        A_cell_to_cell[mask_valid_node], B_phi_cell_to_cell[mask_valid_node]
    ).transpose(1, 2)

    """ fourth method"""
    # nabla_phi_node_lst = torch.matmul(R_inv_Q_t,B_phi_cell_to_cell_x)

    if rt_cond:
        return nabla_phi_node_lst, cond_num
    else:
        return nabla_phi_node_lst


# Note: torch.compile disabled for this function due to PyG scatter operations
def Moving_LSQ(
    phi_node=None,
    edge_index=None,
    node_pos=None,
    dual_edge=True,
    order=None,
    precompute_Moments: list = None,
    mask_boundary=None,
):
 
    """
    Moving Least Squares (MLS) gradient reconstruction - DEPRECATED for 2D only.
    
    WARNING: This function is deprecated and only works for 2D meshes with hardcoded polynomial terms.
    Consider using weighted_lstsq with appropriate order instead for 3D compatibility.
    
    Args:
        phi_node (Tensor): [N, C] Node values.
        edge_index (Tensor): [2, E] Edge indices.
        node_pos (Tensor): [N, 2] Node positions (2D only).
        dual_edge (bool): If True, use bidirectional edges.
        order (str): Order of reconstruction.
        precompute_Moments (list, optional): Precomputed moments.
        mask_boundary (Tensor, optional): Boundary mask.
    Returns:
        nabla_phi_node_lst (Tensor): [N, C, ...] Node gradients (shape depends on order).
    """
    phi_node, rhs_layout = pack_field_rhs(phi_node)

    import warnings
    warnings.warn(
        "Moving_LSQ is deprecated and only works for 2D with hardcoded polynomial terms. "
        "Consider using weighted_lstsq instead for 3D compatibility.",
        DeprecationWarning,
        stacklevel=2
    )
    if dual_edge:
        outdegree_node_index, indegree_node_index = edge_index[0], edge_index[1]
    else:
        outdegree_node_index = torch.cat((edge_index[0], edge_index[1]), dim=0)
        indegree_node_index = torch.cat((edge_index[1], edge_index[0]), dim=0)

    node_pos_diff_on_edge = (
        node_pos[outdegree_node_index] - node_pos[indegree_node_index]
    )
    # In MLS, first calculate the weight matrix
    radius = torch.norm(node_pos_diff_on_edge, dim=1, keepdim=True)
    max_node_radius = scatter(
        radius, indegree_node_index, dim=0, dim_size=node_pos.shape[0], reduce='max'
    )
    weight_cell_to_cell = torch.exp(-torch.pow(radius / max_node_radius[outdegree_node_index], 2))

    displacement = torch.cat(
        (
            weight_cell_to_cell,
            node_pos_diff_on_edge*weight_cell_to_cell,
            0.5 * (node_pos_diff_on_edge**2)*weight_cell_to_cell,
            node_pos_diff_on_edge[:, 0:1] * node_pos_diff_on_edge[:, 1:2]*weight_cell_to_cell,
        ),
        dim=-1,
    ).unsqueeze(2)

    displacement_T = displacement.transpose(1, 2)

    left_on_edge = torch.matmul(
        displacement,
        displacement_T,
    )

    A_cell_to_cell = scatter(
        left_on_edge, indegree_node_index, dim=0, dim_size=node_pos.shape[0], reduce='sum'
    )

    phi_on_edge = (
        (
            (
                phi_node[outdegree_node_index]*weight_cell_to_cell
            ).unsqueeze(1)
        )
        * displacement
    )

    B_phi_cell_to_cell = scatter(
        phi_on_edge, indegree_node_index, dim=0, dim_size=node_pos.shape[0], reduce='sum'
    )

    """ first method"""
    # nabla_phi_node_lst = torch.linalg.lstsq(
    #     A_cell_to_cell_x, B_phi_cell_to_cell_x
    # ).solution

    """ second method"""
    # nabla_phi_node_lst = torch.matmul(A_inv_cell_to_cell_x,B_phi_cell_to_cell_x)

    """ third method"""
    nabla_phi_node_lst = torch.linalg.solve(
        A_cell_to_cell, B_phi_cell_to_cell
    ).transpose(1, 2)
    # [N,C,[ux, uy, uxx, uyy, uxy]]
    
    """ fourth method"""
    # nabla_phi_node_lst = torch.matmul(R_inv_Q_t,B_phi_cell_to_cell_x)
    
    return unpack_gradient_rhs(nabla_phi_node_lst[:, :, 1:], rhs_layout)


# ==============================================================================
# Moving Least Squares 3D — cell-center to vertex reconstruction
# ==============================================================================

def build_vertex_to_cell_edge(
    cells_node: torch.Tensor,
    cells_node_ptr: torch.Tensor,
    num_nodes: int,
) -> torch.Tensor:
    """Build vertex-to-cell connectivity from cell-to-vertex data.

    Given the ragged cell-to-node arrays (CSR-style with a pointer array),
    produce an edge index ``[2, E]`` where ``edge[0]`` = vertex (target) and
    ``edge[1]`` = cell (source).  This is the ``source_to_target_edge``
    expected by :func:`moving_lsq_3d`.

    Args:
        cells_node:     [N_total_cells_node]  node indices for every cell
        cells_node_ptr: [N_total_cells_node]  the cell index for each entry
        num_nodes:      total number of mesh vertices

    Returns:
        edge: [2, E]  with edge[0] = node index, edge[1] = cell index
    """
    # cells_node_ptr[i] gives the cell that owns cells_node[i]
    # So simply stacking gives us  (node, cell) pairs.
    target = cells_node.long()       # vertex indices
    source = cells_node_ptr.long()   # cell indices
    edge = torch.stack([target, source], dim=0)  # [2, E]
    # Remove duplicates (shouldn't exist in well-formed meshes, but be safe)
    edge = torch.unique(edge, dim=1)
    return edge


def _mlsq_poly_basis(dr: torch.Tensor, order: str) -> torch.Tensor:
    """Return the polynomial basis row vector **including** the constant term.

    Unlike :func:`moments_order` in ``FVorder.py`` (which omits the constant
    because WLSQ only reconstructs derivatives), Moving-LSQ needs the
    constant term so that ``coeffs[0]`` gives the reconstructed function
    value at the evaluation point.

    Args:
        dr: [E, 3]  position difference ``source_pos - target_pos``
        order: ``"1st"`` or ``"2nd"``

    Returns:
        P: [E, M]  where M = 4 (1st) or 10 (2nd)
    """
    ones = torch.ones(dr.shape[0], 1, dtype=dr.dtype, device=dr.device)
    dx, dy, dz = dr[:, 0:1], dr[:, 1:2], dr[:, 2:3]

    if order == "1st":
        # P = [1, dx, dy, dz]   -> 4 terms
        return torch.cat([ones, dx, dy, dz], dim=1)
    elif order == "2nd":
        # P = [1, dx, dy, dz, dx^2/2, dy^2/2, dz^2/2, dx*dy, dx*dz, dy*dz]
        return torch.cat([
            ones, dx, dy, dz,
            0.5 * dx ** 2, 0.5 * dy ** 2, 0.5 * dz ** 2,
            dx * dy, dx * dz, dy * dz,
        ], dim=1)
    else:
        raise NotImplementedError(f"Moving LSQ order '{order}' not implemented (use '1st' or '2nd')")


def moving_lsq_3d(
    phi_source: torch.Tensor,
    source_pos: torch.Tensor,
    target_pos: torch.Tensor,
    source_to_target_edge: torch.Tensor,
    order: str = "1st",
) -> tuple:
    """Moving Least Squares reconstruction from source points to target points.

    For each target point, a weighted polynomial is fitted through surrounding
    source values.  Returns both the reconstructed function value and gradient
    at target positions.

    **Polynomial basis** (1st order)::

        P(dr) = [1, dx, dy, dz]        (M = 4 terms)

    **Weighting**::

        w_i = exp( -(|dr_i| / r_max)^2 )

    where ``r_max`` is the maximum distance among the target's surrounding
    source points.

    **Normal equations** assembled per target::

        A = sum_i  w_i  P_i^T  P_i          [M, M]
        b = sum_i  w_i  P_i^T  phi_i        [M, C]

    Solve ``A @ coeffs = b`` to get ``coeffs`` of shape ``[M, C]``:

    * ``coeffs[0, :]``   = reconstructed phi at the target
    * ``coeffs[1:4, :]``  = gradient (d/dx, d/dy, d/dz) at the target

    Args:
        phi_source: [N_source, C] field values at source (cell-center) points
        source_pos: [N_source, 3] source point positions
        target_pos: [N_target, 3] target point positions
        source_to_target_edge: [2, E]  where ``[0]`` = target index,
            ``[1]`` = source index
        order: ``"1st"`` for linear, ``"2nd"`` for quadratic

    Returns:
        phi_target:  [N_target, C] reconstructed function values
        grad_target: [N_target, C, 3] reconstructed gradients
    """
    phi_source, rhs_layout = pack_field_rhs(phi_source)

    target_idx = source_to_target_edge[0]   # [E]
    source_idx = source_to_target_edge[1]   # [E]

    N_target = target_pos.shape[0]
    C = phi_source.shape[1]

    # --- position differences: source - target  (per edge) ---
    dr = source_pos[source_idx] - target_pos[target_idx]  # [E, 3]
    dist = torch.norm(dr, dim=1, keepdim=True)             # [E, 1]

    # --- per-target maximum radius for weight normalisation ---
    r_max = scatter(
        dist, target_idx, dim=0, dim_size=N_target, reduce='max',
    )  # [N_target, 1]

    # Avoid division by zero for isolated targets
    r_max = r_max.clamp(min=1e-30)
    r_max_edge = r_max[target_idx]  # [E, 1]

    # --- exponential RBF weight ---
    w = torch.exp(-(dist / r_max_edge) ** 2)  # [E, 1]

    # --- polynomial basis (includes constant term) ---
    P = _mlsq_poly_basis(dr, order)  # [E, M]
    M = P.shape[1]

    # --- weighted basis ---
    wP = w * P  # [E, M]

    # --- assemble A = sum_i  wP_i^T  wP_i  (symmetric, per target) ---
    #   outer product per edge:  [E, M, 1] x [E, 1, M] -> [E, M, M]
    A_edge = wP.unsqueeze(2) * wP.unsqueeze(1)  # [E, M, M]
    A = scatter(
        A_edge, target_idx, dim=0, dim_size=N_target, reduce='sum',
    )  # [N_target, M, M]

    # --- assemble b = sum_i  wP_i^T  phi_i  (per target) ---
    #   phi_source at source points:  [E, C]
    phi_e = phi_source[source_idx]  # [E, C]
    #   wP^T * phi -> [E, M, 1] * [E, 1, C] -> [E, M, C]
    b_edge = wP.unsqueeze(2) * (w * phi_e).unsqueeze(1)  # [E, M, C]
    b = scatter(
        b_edge, target_idx, dim=0, dim_size=N_target, reduce='sum',
    )  # [N_target, M, C]

    # --- solve A @ coeffs = b ---
    # Add small Tikhonov regularisation to handle rank-deficient stencils
    # (e.g. 2nd-order fit with too few neighbours).
    reg = 1e-12 * torch.eye(M, dtype=A.dtype, device=A.device)
    coeffs = torch.linalg.solve(A + reg, b)  # [N_target, M, C]

    # --- extract outputs ---
    phi_target = coeffs[:, 0, :]           # [N_target, C]
    grad_target = coeffs[:, 1:4, :].permute(0, 2, 1)  # [N_target, C, 3]

    return (
        unpack_field_rhs(phi_target, rhs_layout),
        unpack_gradient_rhs(grad_target, rhs_layout),
    )


# @torch.compile(mode="default", dynamic=True)
def green_gauss_gradient(
    phi_cpd_cell: torch.Tensor,
    neighbor_cpd_cell: torch.Tensor,
    cell_volume: torch.Tensor,
    cells_face_surface_vec: torch.Tensor,
    cells_face: torch.Tensor,
    cells_face_ptr: torch.Tensor,
    mask_interior_cell: torch.Tensor,
    mask_boundary_face: torch.Tensor,
    gC: torch.Tensor,
    gF: torch.Tensor,
    Cf: torch.Tensor,
    Ff: torch.Tensor,
    corrections: int = 2,
    ) -> torch.Tensor:
    """
    Compute the Green-Gauss gradient using the finite volume method.

    Note: unlike the WLSQ path, this returns INTERIOR-cell gradients only.
    On non-orthogonal tetrahedral meshes Green-Gauss does not converge
    (measured order ~0), so it is not used by the production solvers.

    Args:
        phi_cpd_cell (torch.Tensor): [num_cpd_cells, num_channels] Values on
            real cells plus boundary-face ghost cells.
        neighbor_cpd_cell (torch.Tensor): [2, num_faces] Per-face (C, F) pair.
        cell_volume (torch.Tensor): [num_cells, 1] Volume of each real cell.
        cells_face_surface_vec (torch.Tensor): [num_cells_face, 3] Outward
            surface vectors, one entry per (cell, face) incidence.
        cells_face (torch.Tensor): [num_cells_face] Face index of each
            (cell, face) incidence.
        cells_face_ptr (torch.Tensor): [num_cells_face] Owning cell index of
            each (cell, face) incidence (scatter index, not a CSR pointer).
        mask_interior_cell (torch.Tensor): [num_cpd_cells] True on real cells.
        mask_boundary_face (torch.Tensor): [num_faces] True on boundary faces.
        gC, gF (torch.Tensor): [num_faces, 1, 1] linear interpolation weights.
        Cf, Ff (torch.Tensor): [num_faces, 3, 1] cell-centre-to-face vectors.
    Returns:
        torch.Tensor: [num_cells, num_channels, 3] interior-cell gradients.
    References:
        Moukalled, F., Mangani, L., & Darwish, M. (2016). The finite volume method 
        in computational fluid dynamics. Springer, Section 9.2.
    """

    # Green--Gauss is linear in the transported channels.  Treat leading
    # time/batch axes as multiple RHS channels, preserving the legacy path.
    phi_cpd_cell, rhs_layout = pack_field_rhs(phi_cpd_cell)

    # Consistency checks
    num_cells = cell_volume.shape[0]
    num_cpd_cells = phi_cpd_cell.shape[0]
    cell_volume = cell_volume[:,:,None]  # Ensure volume is 3D for broadcasting
    cells_face_surface_vec = cells_face_surface_vec[:,None,:]  # Ensure surface vector is 3D for broadcasting

    # edge_neighbor_index[0] is C,edge_neighbor_index[1] is F
    C_senders,F_recivers = neighbor_cpd_cell[0],neighbor_cpd_cell[1] # 对应Moukalled书中的C和F， pdf中276页

    phi_cpd_cell_expand = phi_cpd_cell[:,:,None]

    phi_f_hat = phi_cpd_cell_expand[C_senders]*gC+phi_cpd_cell_expand[F_recivers]*gF

    # Every write below is out of place, and the accumulator carries phi's dtype
    # rather than the default float32.  Both were required to reach this function
    # from a solver forward (collocated MMS control arm): the
    # Jacobian-vector product differentiates the residual TWICE, and an in-place
    # write to a tensor the tape still needs raises outright, while a float32
    # accumulator silently truncated a float64 Newton residual measured against a
    # 1e-11 gate.  The arithmetic is unchanged -- including the correction being
    # ACCUMULATED into phi_f_hat across the two sweeps rather than recomputed
    # from the uncorrected face value.
    interior_ids = mask_interior_cell.reshape(-1).nonzero(as_tuple=True)[0]

    def cell_gradient(face_value):
        interior = scatter(
            src=face_value[cells_face]*cells_face_surface_vec,
            index=cells_face_ptr,
            dim=0,
            dim_size=num_cells,
            reduce='sum',
        )/cell_volume
        return torch.zeros(
            (num_cpd_cells, phi_cpd_cell.shape[1], 3),
            device=phi_cpd_cell.device, dtype=interior.dtype,
        ).index_copy(0, interior_ids, interior)

    grad_phi_cpd_cell = cell_gradient(phi_f_hat)

    # start correcting the gradient
    # Moukalled书中276页的公式9.2.3
    # 迭代两次，收敛性较好
    # 但是首先要将boundary cell grad移植给boundary face 即：将边界面的接收者设置为发送者(边界面的邻居在网格解析阶段就指向其owner)
    # The remap applies to the CORRECTION gather only: the uncorrected face value
    # above still reads the ghost cell, which is where the Dirichlet value lives.
    # ``corrections = 0`` is the "common variant of the divergence-theorem
    # gradient" that Syrakos et al. (2017) Table 5 place at order 0 on arbitrary
    # meshes; it is also the only variant whose dependency stencil stays inside
    # one hop.  Each sweep widens it by a hop, and the collocated Jacobian's
    # colouring assumes a 2-hop cell ball: measured on the perturbed
    # 8^3 case, the assembled J reproduced the matrix-free product to 3.2e-5
    # relative with the WLSQ gradient and to only 6.9e-2 with two sweeps.
    F_corrected = torch.where(mask_boundary_face.reshape(-1), C_senders, F_recivers)
    for _ in range(int(corrections)):

        phi_f_hat = phi_f_hat + gC*(grad_phi_cpd_cell[C_senders]@Cf)+gF*(grad_phi_cpd_cell[F_corrected]@Ff)

        grad_phi_cpd_cell = cell_gradient(phi_f_hat)

    return unpack_gradient_rhs(grad_phi_cpd_cell[mask_interior_cell], rhs_layout)

