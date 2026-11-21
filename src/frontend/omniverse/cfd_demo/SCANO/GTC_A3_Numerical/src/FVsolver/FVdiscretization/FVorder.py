import torch
from torch_scatter import scatter_add


def moments_order(
    order="1nd",
    num_nodes=0,
    mesh_pos_diff_on_edge=None,
    indegree_node_index=None,
):
    """
    Compute moments for WLSQ reconstruction in 3D.
    Args:
        order (str): Order of reconstruction ('1st', '2nd', '3rd', '4th').
        mesh_pos_diff_on_edge (Tensor): [2*E, 3] Edge position differences in 3D.
        indegree_node_index (Tensor): [N] Node indices.
    Returns:
        Tuple of (A_cell_to_cell, B_cell_to_cell).
        A_cell_to_cell: [N, x, x], x depends on order.
        B_cell_to_cell: [2*E, x]
    """
    dx, dy, dz = mesh_pos_diff_on_edge[:, 0:1], mesh_pos_diff_on_edge[:, 1:2], mesh_pos_diff_on_edge[:, 2:3]
    
    if order=="1st":
        od=1
        # 3D first order: [dx, dy, dz] -> 3 terms
        displacement = mesh_pos_diff_on_edge.unsqueeze(2)
    elif order=="2nd":
        od=1
        # 3D second order: [dx, dy, dz, dx^2/2, dy^2/2, dz^2/2, dx*dy, dx*dz, dy*dz] -> 9 terms
        displacement = torch.cat(
            (
                mesh_pos_diff_on_edge,  # [dx, dy, dz]
                0.5 * (mesh_pos_diff_on_edge**2),  # [dx^2/2, dy^2/2, dz^2/2]
                dx * dy,  # dx*dy
                dx * dz,  # dx*dz
                dy * dz,  # dy*dz
            ),
            dim=-1,
        ).unsqueeze(2)
    elif order=="3rd":
        od=1
        # 3D third order: add cubic terms -> 19 terms
        displacement = torch.cat(
            (
                mesh_pos_diff_on_edge,  # [dx, dy, dz]
                0.5 * (mesh_pos_diff_on_edge**2),  # [dx^2/2, dy^2/2, dz^2/2]
                dx * dy,  # dx*dy
                dx * dz,  # dx*dz
                dy * dz,  # dy*dz
                (1 / 6) * (mesh_pos_diff_on_edge**3),  # [dx^3/6, dy^3/6, dz^3/6]
                0.5 * (dx ** 2) * dy,  # dx^2*dy/2
                0.5 * (dx ** 2) * dz,  # dx^2*dz/2
                0.5 * (dy ** 2) * dx,  # dy^2*dx/2
                0.5 * (dy ** 2) * dz,  # dy^2*dz/2
                0.5 * (dz ** 2) * dx,  # dz^2*dx/2
                0.5 * (dz ** 2) * dy,  # dz^2*dy/2
                dx * dy * dz,  # dx*dy*dz
            ),
            dim=-1,
        ).unsqueeze(2)
    elif order=="4th":
        od=1
        # 3D fourth order: add quartic terms -> 34 terms
        displacement = torch.cat(
            (
                mesh_pos_diff_on_edge,  # [dx, dy, dz]
                0.5 * (mesh_pos_diff_on_edge**2),  # [dx^2/2, dy^2/2, dz^2/2]
                dx * dy,  # dx*dy
                dx * dz,  # dx*dz
                dy * dz,  # dy*dz
                (1 / 6) * (mesh_pos_diff_on_edge**3),  # [dx^3/6, dy^3/6, dz^3/6]
                0.5 * (dx ** 2) * dy,  # dx^2*dy/2
                0.5 * (dx ** 2) * dz,  # dx^2*dz/2
                0.5 * (dy ** 2) * dx,  # dy^2*dx/2
                0.5 * (dy ** 2) * dz,  # dy^2*dz/2
                0.5 * (dz ** 2) * dx,  # dz^2*dx/2
                0.5 * (dz ** 2) * dy,  # dz^2*dy/2
                dx * dy * dz,  # dx*dy*dz
                (1 / 24) * (mesh_pos_diff_on_edge ** 4),  # [dx^4/24, dy^4/24, dz^4/24]
                (1 / 6) * (dx ** 3) * dy,  # dx^3*dy/6
                (1 / 6) * (dx ** 3) * dz,  # dx^3*dz/6
                (1 / 6) * (dy ** 3) * dx,  # dy^3*dx/6
                (1 / 6) * (dy ** 3) * dz,  # dy^3*dz/6
                (1 / 6) * (dz ** 3) * dx,  # dz^3*dx/6
                (1 / 6) * (dz ** 3) * dy,  # dz^3*dy/6
                (1 / 4) * (dx ** 2) * (dy ** 2),  # dx^2*dy^2/4
                (1 / 4) * (dx ** 2) * (dz ** 2),  # dx^2*dz^2/4
                (1 / 4) * (dy ** 2) * (dz ** 2),  # dy^2*dz^2/4
                0.5 * (dx ** 2) * dy * dz,  # dx^2*dy*dz/2
                0.5 * (dy ** 2) * dx * dz,  # dy^2*dx*dz/2
                0.5 * (dz ** 2) * dx * dy,  # dz^2*dx*dy/2
            ),
            dim=-1,
        ).unsqueeze(2)    
    else:
        raise NotImplementedError(f"{order} Order not implemented")
    
    displacement_T = displacement.transpose(1, 2)
    weight_cell_to_cell = (1 / torch.norm(mesh_pos_diff_on_edge, dim=1, keepdim=True)**od).unsqueeze(2)
    left_on_edge = torch.matmul(
        displacement * weight_cell_to_cell,
        displacement_T,
    )
    A_cell_to_cell = scatter_add(
        left_on_edge, indegree_node_index, dim=0, dim_size=num_nodes
    ) # [N, x, x], x is depend on order
    B_cell_to_cell = weight_cell_to_cell * displacement
    # [2*E, x]
    return A_cell_to_cell, B_cell_to_cell