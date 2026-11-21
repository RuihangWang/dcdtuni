import torch
from torch_scatter import scatter_add, scatter_max
from FVsolver.FVdiscretization.FVorder import moments_order


@torch.compile(mode="max-autotune")
def gradient_reconstruction(order="2nd", *args, **kwargs):
    """
    Unified gradient computation API with adaptive parameter handling.
    
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
        
    Returns:
    --------
    gradients : array
        Computed gradients for each cell
        
    Examples:
    ---------
    For WLSQ methods, pass parameters as they would be to weighted_lstsq:
    >>> grad = compute_gradient("2nd", phi_node=field, edge_index=edges, 
    ...                        mesh_pos=positions, order=2)
    
    For Green-Gauss method, pass parameters as they would be to green_gauss_gradient:
    >>> grad = compute_gradient("GG", phi_cell=field, neighbor_cell=neighbors,
    ...                        centroid=centroids, face_pos=face_pos,
    ...                        cells_volume=volumes, cells_face_surf_vector=surf_vec,
    ...                        cells_face=faces, cells_face_ptr=face_ptr, 
    ...                        face_type=f_type)
    
    The function automatically routes to the appropriate gradient computation method
    and forwards all arguments without modification.
    """
    
    # Validate input parameters
    valid_orders = ["1st", "2nd", "3rd", "4th", "GG"]
    if order not in valid_orders:
        raise ValueError(f"Invalid order '{order}'. Must be one of {valid_orders}")
    
    # Route to appropriate gradient computation method
    if order == "GG":
        # Use Green-Gauss gradient method - forward all arguments
        return green_gauss_gradient(*args, **kwargs)
    
    else:
        kwargs['order'] = order
        
        return weighted_lstsq(*args, **kwargs)


def compute_normal_matrix(
    order="1st",
    mesh_pos=None,
    edge_index=None, # 默认应该是仅包含1阶邻居点+构成共点的单元的所有点
):
    """
    Compute the normal matrices A and B for node-based weighted least squares (WLSQ) gradient reconstruction.
    Args:
        order (str): Order of reconstruction ('1st', '2nd', '3rd', '4th').
        mesh_pos (Tensor): [N, D] Node positions.
        edge_index (Tensor): [2, E] Edge indices.
    Returns:
        Tuple of (A_cell_to_cell, B_cell_to_cell[:split_index], B_cell_to_cell[split_index:]).
    """
    
    twoway_edge_index = torch.cat((edge_index,edge_index.flip(0)),dim=1)

    outdegree_node_index, indegree_node_index = twoway_edge_index[0], twoway_edge_index[1]   
    
    mesh_pos_diff_on_edge = mesh_pos[outdegree_node_index] - mesh_pos[indegree_node_index]
    
    # L_local = scatter_mean(src=mesh_pos_diff_on_edge**2,index=indegree_node_index,dim=0).sqrt()
    
    # normaled_mesh_pos_diff_on_edge = mesh_pos_diff_on_edge/(L_local[indegree_node_index])
    
    (A_cell_to_cell, B_cell_to_cell) = moments_order(
        order=order,
        num_nodes=mesh_pos.shape[0],
        mesh_pos_diff_on_edge=mesh_pos_diff_on_edge,
        indegree_node_index=indegree_node_index,
    )

    return (A_cell_to_cell, B_cell_to_cell)

def weighted_lstsq(
    phi_node=None,
    edge_index=None, # 输入的edge_index是否是双向的,注意对于edge_index一定是0-1
    mask_valid_node=None, # 只在需要求梯度的点求梯度
    mesh_pos=None,
    order=None,
    precompute_Moments: list = None, # 应一定包含3个元素，[A, 单向的B，和额外的B（即仅内部点指向边界点）]
    rt_cond=False,
):
    '''
    Weighted Least Squares (WLSQ) gradient reconstruction.
    Args:
        phi_node (Tensor): [N, C] Node values.
        edge_index (Tensor): [2, E] Edge indices.
        mask_valid_node (Tensor, optional): [2, E_extra] Extra edge indices.
        mesh_pos (Tensor): [N, D] Node positions.
        order (str): Order of reconstruction ('1st', '2nd', '3rd', '4th').
        precompute_Moments (list, optional): Precomputed [A, B, extra_B] moments.
        rt_cond (bool): If True, also return condition number.
    Returns:
        nabla_phi_node_lst (Tensor): [N, C, ...] Node gradients (shape depends on order).
        If rt_cond is True, also returns condition number.
    '''
    # edge_index = knn_graph(mesh_pos, k=9, loop=False)
    if (order is None) or (order not in ["1st", "2nd", "3rd", "4th"]):
        raise ValueError("order must be specified in [\"1st\", \"2nd\", \"3rd\", \"4th\"]")
    
    twoway_edge_index = torch.cat((edge_index,edge_index.flip(0)),dim=1)

    if mask_valid_node is None:
        mask_valid_node = torch.ones(mesh_pos.shape[0], dtype=torch.bool)

    outdegree_node_index, indegree_node_index = twoway_edge_index[0], twoway_edge_index[1]   

    if precompute_Moments is None:

        """node to node contribution"""
        (A_cell_to_cell, two_way_B_cell_to_cell) = compute_normal_matrix(
            order=order,
            mesh_pos=mesh_pos,
            edge_index=edge_index, # 默认应该是仅包含1阶邻居点+构成共点的单元的所有点
        )
        B_cell_to_cell = two_way_B_cell_to_cell
        """node to node contribution"""
        
        phi_diff_on_edge = B_cell_to_cell * (
            (phi_node[outdegree_node_index] - phi_node[indegree_node_index]).unsqueeze(1)
        )

        B_phi_cell_to_cell = scatter_add(
            phi_diff_on_edge, indegree_node_index, dim=0, dim_size=mesh_pos.shape[0]
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
        
        if od >= 3:
            # For 3D: cubic terms start at index 9 and go to 12 [dx^3/6, dy^3/6, dz^3/6]
            two_way_B_cell_to_cell[half_dim:, 9:12] *= -1
        
        B_cell_to_cell = two_way_B_cell_to_cell
        
        phi_diff_on_edge = B_cell_to_cell * (
            (phi_node[outdegree_node_index] - phi_node[indegree_node_index]).unsqueeze(
                1
            )
        )

        B_phi_cell_to_cell = scatter_add(
            phi_diff_on_edge,
            indegree_node_index,
            dim=0,
            dim_size=mesh_pos.shape[0],
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
    
    """ first method"""
    # nabla_phi_node_lst = torch.linalg.lstsq(
    #     A_normalized, B_normalized
    # ).solution.transpose(1, 2)

    """ second method"""
    # nabla_phi_node_lst = torch.matmul(A_inv_cell_to_cell_x,B_phi_cell_to_cell_x)

    """ third method"""
    nabla_phi_node_lst = torch.linalg.solve(
        A_cell_to_cell[mask_valid_node], B_phi_cell_to_cell[mask_valid_node]
    ).transpose(1, 2)

    """ fourth method"""
    # nabla_phi_node_lst = torch.matmul(R_inv_Q_t,B_phi_cell_to_cell_x)

    if rt_cond:
        return nabla_phi_node_lst, torch.linalg.cond(A_cell_to_cell[mask_valid_node]) 
    else:
        return nabla_phi_node_lst


@torch.compile(mode="max-autotune")
def Moving_LSQ(
    phi_node=None,
    edge_index=None,
    mesh_pos=None,
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
        mesh_pos (Tensor): [N, 2] Node positions (2D only).
        dual_edge (bool): If True, use bidirectional edges.
        order (str): Order of reconstruction.
        precompute_Moments (list, optional): Precomputed moments.
        mask_boundary (Tensor, optional): Boundary mask.
    Returns:
        nabla_phi_node_lst (Tensor): [N, C, ...] Node gradients (shape depends on order).
    """
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

    mesh_pos_diff_on_edge = (
        mesh_pos[outdegree_node_index] - mesh_pos[indegree_node_index]
    )
    # In MLS, first calculate the weight matrix
    radius = torch.norm(mesh_pos_diff_on_edge, dim=1, keepdim=True)
    max_node_radius = scatter_max(
        radius, indegree_node_index, dim=0, dim_size=mesh_pos.shape[0]
    )[0]
    weight_cell_to_cell = torch.exp(-torch.pow(radius / max_node_radius[outdegree_node_index], 2))

    displacement = torch.cat(
        (
            weight_cell_to_cell,
            mesh_pos_diff_on_edge*weight_cell_to_cell,
            0.5 * (mesh_pos_diff_on_edge**2)*weight_cell_to_cell,
            mesh_pos_diff_on_edge[:, 0:1] * mesh_pos_diff_on_edge[:, 1:2]*weight_cell_to_cell,
        ),
        dim=-1,
    ).unsqueeze(2)

    displacement_T = displacement.transpose(1, 2)

    left_on_edge = torch.matmul(
        displacement,
        displacement_T,
    )

    A_cell_to_cell = scatter_add(
        left_on_edge, indegree_node_index, dim=0, dim_size=mesh_pos.shape[0]
    )

    phi_on_edge = (
        (
            (
                phi_node[outdegree_node_index]*weight_cell_to_cell
            ).unsqueeze(1)
        )
        * displacement
    )

    B_phi_cell_to_cell = scatter_add(
        phi_on_edge, indegree_node_index, dim=0, dim_size=mesh_pos.shape[0]
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
    
    return nabla_phi_node_lst[:,:,1:]

# @torch.compile(mode="max-autotune")
def green_gauss_gradient(
    phi_cpd_cell: torch.Tensor,
    cpd_neighbor_cell: torch.Tensor,
    cells_volume: torch.Tensor,
    cells_face_surface_vec: torch.Tensor,
    cells_face: torch.Tensor,
    cells_face_ptr: torch.Tensor,
    mask_interior_cell: torch.Tensor,
    mask_boundary_face: torch.Tensor,
    gC: torch.Tensor,
    gF: torch.Tensor,
    Cf: torch.Tensor,
    Ff: torch.Tensor,
    ) -> torch.Tensor:
    """
    Compute the Green-Gauss gradient using the finite volume method.
    Args:
        phi_cell (torch.Tensor): [num_cells, num_channels] Cell values.
        neighbor_cell (torch.Tensor): [2, num_edges] Edge neighbors (C, F).
        cells_volume (torch.Tensor): [num_cells] Volume of each cell.
        cells_face_surface_vec (torch.Tensor): [num_edges, 3] Surface vectors of faces.
        cells_face (torch.Tensor): [num_edges] Indices of faces in cells.
        cells_face_ptr (torch.Tensor): [num_cells+1] Pointer to faces in cells.
        face_type (torch.Tensor): [num_edges] Type of each face (e.g., NORMAL, WALL).
    Returns:
        torch.Tensor: [num_edges, num_channels] Face-centered gradients.        
    References:
        Moukalled, F., Mangani, L., & Darwish, M. (2016). The finite volume method 
        in computational fluid dynamics. Springer, Section 9.2.
    """

    # Consistency checks
    num_cells = cells_volume.shape[0]
    num_cpd_cells = phi_cpd_cell.shape[0]
    cells_volume = cells_volume[:,:,None]  # Ensure volume is 3D for broadcasting
    cells_face_surface_vec = cells_face_surface_vec[:,None,:]  # Ensure surface vector is 3D for broadcasting

    # edge_neighbor_index[0] is C,edge_neighbor_index[1] is F
    C_senders,F_recivers = cpd_neighbor_cell[0],cpd_neighbor_cell[1] # 对应Moukalled书中的C和F， pdf中276页

    phi_cpd_cell_expand = phi_cpd_cell[:,:,None]
    
    phi_f_hat = phi_cpd_cell_expand[C_senders]*gC+phi_cpd_cell_expand[F_recivers]*gF

    grad_phi_cpd_cell = torch.zeros((num_cpd_cells, phi_cpd_cell.shape[1], 3), device=phi_cpd_cell.device)
    grad_phi_cpd_cell[mask_interior_cell] = scatter_add(
        src=phi_f_hat[cells_face]*cells_face_surface_vec,
        index=cells_face_ptr,
        dim=0,
        dim_size=num_cells,
    )/cells_volume

    # start correcting the gradient
    # Moukalled书中276页的公式9.2.3
    # 迭代两次，收敛性较好
    # 但是首先要将boundary cell grad移植给boundary face 即：将边界面的接收者设置为发送者,注意查看"src/Parse_mesh/parse_openfoam.py line:422"
    F_recivers[mask_boundary_face] = C_senders[mask_boundary_face] 
    for _ in range(2):

        phi_f_hat += gC*(grad_phi_cpd_cell[C_senders]@Cf)+gF*(grad_phi_cpd_cell[F_recivers]@Ff)

        grad_phi_cpd_cell[mask_interior_cell] = scatter_add(
            src=phi_f_hat[cells_face]*cells_face_surface_vec,
            index=cells_face_ptr,
            dim=0,
            dim_size=num_cells,
        )/cells_volume

    return grad_phi_cpd_cell[mask_interior_cell]