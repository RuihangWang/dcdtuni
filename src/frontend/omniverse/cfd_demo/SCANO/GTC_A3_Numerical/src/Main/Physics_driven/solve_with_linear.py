import sys
import os
import warnings

# Set warning filter before importing torch
warnings.filterwarnings("ignore", message=".*Sparse CSR tensor support is in beta state.*")

cur_path = os.path.split(__file__)[0]
sys.path.append(cur_path)

import torch
import numpy as np

# import os
from FVdomain import Graph_loader
from Utils import get_param
import time
from Utils.get_param import get_hyperparam
from Utils.Logger import Logger
from Post_process.to_vtk import export_full_mesh_vtu
import random
import datetime
from FVsolver.FVdiscretization.FVgrad import gradient_reconstruction
from FVsolver.FVdiscretization.FVInterpolation import FV_Interpolation
from Utils.utilities import FaceType
from torch_scatter import scatter


# configurate parameters
params = get_param.params()
seed = int(datetime.datetime.now().timestamp())
np.random.seed(seed)
random.seed(seed)
torch.manual_seed(seed)
torch.cuda.set_per_process_memory_fraction(0.8, params.on_gpu)

device = "cuda"

# Support external parameter passing
import sys
if len(sys.argv) > 1:
    params.dataset_dir = sys.argv[1]
else:
    params.dataset_dir = "mesh_example/lidDrivenCavity3D-simple"

params.dataset_size=1
params.batch_size=1
params.order = "2nd" # 1st, 2nd, 3rd, 4th, GG

# initialize Logger and load model / optimizer if according parameters were given
logger = Logger(
    get_hyperparam(params),
    use_csv=True,
    use_tensorboard=False,
    params=params,
    copy_code=True,
    seed=seed,
)

# initialize Training Dataset
start = time.time()
datasets_factory = Graph_loader.DatasetFactory(
    params=params,
    dataset_dir=params.dataset_dir,
    state_save_dir=logger.saving_path,
    device=device,
)

# refresh dataset size
params.dataset_size = datasets_factory.dataset_size

# create dataset objetc
datasets, loader = datasets_factory.create_loader(
    batch_size=params.batch_size, num_workers=0, pin_memory=False, shuffle=False
)

end = time.time()
print("Training traj has been loaded time consuming:{0}".format(end - start))

''' >>> fetch data and move to GPU >>> '''
fv_graph = next(iter(loader))
fv_graph = fv_graph.to(device, exclude_keys=['global_idx'])
''' <<< fetch data and move to GPU <<< '''

''' FV Graph Data Structure Description 

The fv_graph is a comprehensive finite volume graph structure containing multiple sub-graphs:

=== fv_graph.graph_node ===
Node-level data structure representing mesh vertices:
    - face_node: [num_faces*max_nodes_per_face] - Face-to-node connectivity (node indices flattened)
    - cells_node: [num_cells*max_nodes_per_cell] - Cell-to-node connectivity (node indices flattened)
    - num_nodes: int - Total number of mesh nodes
    - _num_nodes: int - Internal node count reference
    - graph_index: [1] - Graph batch index

=== fv_graph.graph_face ===  
Face-level data structure representing mesh faces/edges:
    - face_type: [num_faces] - Face type classification (boundary, interior, etc. refer to src/Utils/utilities.py)
    - face_area: [num_faces] - Area of each face
    - cells_face: [num_cells*max_faces_per_cell] - Cell-to-face connectivity
    - face_node_ptr: [num_faces*max_nodes_per_face] - same length as cells_face since face_node is flattened, this array is used for distinguish node idx in face_node belong to which face
    - pos: [num_faces, D_pos] - Face center positions
    - _num_faces: int - Total number of faces
    - graph_index: [1] - Graph batch index

=== fv_graph.graph_cell ===
Cell-level data structure representing finite volume cells:
    - x: [num_cpd_cells, 4] - Physical field variables (u,v,w,p) at cell centers
    - neighbor_cell: [2, num_cell_to_cell edges] - Cell-to-cell connectivity (cpd_neighbor_cell)
    - cells_face_unv: [num_cells*max_faces_per_cell, D_norm] - flattened Unit normal vectors of cell faces
    - cells_volume: [num_cells, 1] - Volume of each cell
    - cell_type: [num_cells, 1] - Cell type (NORMAL=0, BOUNDARY=1, etc.)
    - cpd_centroid: [num_cpd_cells, D_pos] - Compound cell centroid positions, cell concated with boundary faces
    - y: [num_cpd_cells, C_target] - Target values for training/validation
    - global_idx: [num_cpd_cells] - Global indexing for cells
    - cells_node_ptr: [num_cells*max_nodes_per_cell] - same length as cells_face since face_node is flattened, this array is used for distinguish node idx in face_node belong to which face
    - cells_face_ptr: [num_cells*max_faces_per_cell] - same length as cells_face since face_node is flattened, this array is used for distinguish face idx in face_node belong to which face
    - case_name: [L_case_name] - Case identifier (encoded as character ordinals)
    - num_nodes: int - Number of compound cells (= _num_cpd_cells)
    - _num_cells: int - Total number of standard cells
    - _num_cpd_cells: int - Total number of compound cells
    - graph_index: [1] - Graph batch index

Note: 
- "cpd" refers to "compound" cells, which represent merged interior cells with boundary faces
- The graph structure supports multi-level connectivity (node, face, cell) for FVM operations
- Cell-centered approach is used where physical variables are stored at cell centroids

'''
cells_face = fv_graph.graph_face.cells_face
face_type = fv_graph.graph_face.face_type
neighbor_cell = fv_graph.graph_cell.neighbor_cell

''' >>> Prepare internal and boundary cell/face masks >>> '''
# Internal domain
internal_neighbor_cell_mask = ~(neighbor_cell[0] == neighbor_cell[1])
internal_face_mask = (face_type==FaceType.NORMAL)  # NORMAL
if (~(internal_face_mask==internal_neighbor_cell_mask)).sum() > 0:
    raise ValueError("internal_face_mask != internal_neighbor_cell_mask, indicating neighbor cell generation error, neighbor cell should maintain consistent dimensions with variables starting with 'face|'")

# Filter out internal faces in cells_face
internal_cells_face_mask = internal_face_mask[cells_face]
internal_cells_face = cells_face[internal_cells_face_mask]

# Boundary domain
boundary_neighbor_cell_mask = (neighbor_cell[0] == neighbor_cell[1])
boundary_face_mask = ~(face_type==FaceType.NORMAL)  # NORMAL
if (~(boundary_face_mask==boundary_neighbor_cell_mask)).sum() > 0:
    raise ValueError("boundary_face_mask != boundary_neighbor_cell_mask, indicating neighbor cell generation error, neighbor cell should maintain consistent dimensions with variables starting with 'face|'")

# Filter out boundary faces in cells_face
boundary_cells_face_mask = boundary_face_mask[cells_face]
boundary_cells_face = cells_face[boundary_cells_face_mask]
''' <<< Prepare internal and boundary cell/face masks <<< '''

''' >>> Calculate geometric coefficients >>> '''
owner = fv_graph.graph_cell.cells_face_ptr

# Filter out internal neighbors corresponding to owner
cpd_senders_cell, cpd_receivers_cell = fv_graph.graph_cell.cpd_neighbor_cell[:,cells_face] # Note: check neighbor_cell generation method, whether boundary face idx is replaced at boundary faces

cpd_neighbor = torch.where(cpd_senders_cell==owner, cpd_receivers_cell, cpd_senders_cell)

# Start calculating non-orthogonal correction geometric diffusion coefficients
cpd_centroid = fv_graph.graph_cell.cpd_centroid
face_center_pos = fv_graph.graph_face.pos

# Geometric coefficients
E_CF = cpd_centroid[cpd_neighbor] - cpd_centroid[owner]
d_CF = torch.norm(E_CF, dim=1, keepdim=True)
e_CF = E_CF / d_CF
if (~torch.isfinite(e_CF)).any():
    raise ValueError("e_CF is not finite")

face_area = fv_graph.graph_face.face_area[cells_face]
cells_face_surf_vec = fv_graph.graph_cell.cells_face_unv * face_area[:, None]

Ef_vec = ((cells_face_surf_vec[:,None,:]@cells_face_surf_vec[:,:,None])/(cells_face_surf_vec[:,None,:]@e_CF[:,:,None]))*e_CF[:,None,:] # page 243
Ef_sca = torch.norm(Ef_vec, dim=-1, keepdim=True)
Tf_vec = (cells_face_surf_vec[:,None,:]-Ef_vec).transpose(1,2) # [N_cells_face, 1, 3] Over-relaxation term page 243
Tf_vec_i = Tf_vec[internal_cells_face_mask]
Tf_vec_b = Tf_vec[boundary_cells_face_mask]

gDiff = Ef_sca/d_CF[:,:,None] # [N_cells_face, 1, 1]

gDiff_f = gDiff[internal_cells_face_mask]
gDiff_b = gDiff[boundary_cells_face_mask]
owner_i = owner[internal_cells_face_mask]
neighbor_i = cpd_neighbor[internal_cells_face_mask]
owner_b = owner[boundary_cells_face_mask]
neighbor_b = cpd_neighbor[boundary_cells_face_mask]
''' <<< Calculate geometric coefficients <<< '''

# Initialize coefficient matrix
num_cells = fv_graph.graph_cell._num_cells
num_cpd_cells = fv_graph.graph_cell._num_cpd_cells
cpd_cell_value = torch.ones((num_cpd_cells,1),device=device) # Initialize for delayed gradient computation
def assemble_matrix_sparse(owner_i, neighbor_i, a_C, a_F, num_cells, device):
    """
    Efficient matrix assembly method using sparse matrices - memory saving
    Correction: Consistent with assemble_matrix_efficient_v2, only add owner->neighbor connections

    Parameters:
    - owner_i: Owner cell indices of internal faces [N_internal_faces]
    - neighbor_i: Neighbor cell indices of internal faces [N_internal_faces]
    - a_C: Main diagonal coefficients [num_cells, 1]
    - a_F: Off-diagonal coefficients [N_internal_faces, 1]
    - num_cells: Total number of cells
    - device: Device

    Returns:
    - A_sparse: Assembled sparse coefficient matrix
    """
    # Prepare indices and values for sparse matrix
    indices_list = []
    values_list = []

    # Main diagonal
    diag_indices = torch.arange(num_cells, device=device)
    indices_list.append(torch.stack([diag_indices, diag_indices]))
    values_list.append(a_C.squeeze())

    # Off-diagonal - only add owner -> neighbor (consistent with efficient_v2)
    indices_list.append(torch.stack([owner_i, neighbor_i]))
    values_list.append(a_F.squeeze())

    # Merge all indices and values
    all_indices = torch.cat(indices_list, dim=1)
    all_values = torch.cat(values_list, dim=0)

    # Create sparse matrix
    A_sparse = torch.sparse_coo_tensor(
        all_indices, all_values,
        (num_cells, num_cells),
        device=device
    ).coalesce()

    return A_sparse

def solve_sparse_system(A_sparse, b, max_iter=1000, tol=1e-6):
    """
    Solve linear system Ax = b using PyTorch's sparse solver
    Note: torch.sparse.spsolve is only available on CUDA devices

    Parameters:
    - A_sparse: Sparse coefficient matrix
    - b: Right-hand side vector
    - max_iter: Maximum iterations (unused, kept for interface compatibility)
    - tol: Convergence tolerance (unused, kept for interface compatibility)

    Returns:
    - x: Solution vector
    - converged: Whether converged
    - iterations: Actual iteration count
    """
    try:
        # Check device, torch.sparse.spsolve is only available on CUDA
        if not A_sparse.is_cuda:
            raise RuntimeError("torch.sparse.spsolve is only available on CUDA devices, please move matrix and vector to GPU")

        # Ensure matrix is in CSR format, which is required by torch.sparse.spsolve
        if A_sparse.layout == torch.sparse_coo:
            A_csr = A_sparse.to_sparse_csr()
        elif A_sparse.layout == torch.sparse_csr:
            A_csr = A_sparse
        else:
            # If in other format, convert to COO first then to CSR
            A_csr = A_sparse.coalesce().to_sparse_csr()

        x = torch.sparse.spsolve(A_csr, b)
        return x, True, 0  # 0 indicates maximum iterations not used
    except RuntimeError as e:
        print(f"Sparse linear solver error: {e}")
        print("Hint: torch.sparse.spsolve is only available on CUDA devices")
        return None, False, 0
    
B = torch.zeros((num_cells,1) ,device=device)

# Prepare for applying boundary conditions
cpd_cell_type = fv_graph.graph_cell.cpd_cell_type
internal_cell = (cpd_cell_type==FaceType.NORMAL).squeeze()  # NORMAL
boundary_cellf = ~internal_cell
inflow_mask = (cpd_cell_type==FaceType.PATCH).squeeze()  # 有问题
wall_mask = (cpd_cell_type==FaceType.PATCH).squeeze()   # 有问题

# Poisson equation coefficient configuration - MMS verification
diff_coeff = 0.01
cells_volume = fv_graph.graph_cell.cells_volume.view(-1,1)

# MMS analytical solution: phi(x,y,z) = 0.1 * sin(pi*x) * sin(pi*y) * sin(pi*z)
# Corresponding source term: S = 0.1 * 3 * pi^2 * DT * sin(pi*x) * sin(pi*y) * sin(pi*z)
# Where DT = 0.01, so S = 0.1 * 3 * pi^2 * 0.01 * sin(pi*x) * sin(pi*y) * sin(pi*z)
import math
cell_centers = fv_graph.graph_cell.cpd_centroid[:fv_graph.graph_cell._num_cells]  # Only take centers of internal cells
pi = math.pi
Qv = 0.1 * 3.0 * pi**2 * diff_coeff * torch.sin(pi*cell_centers[:,0:1]) * torch.sin(pi*cell_centers[:,1:2]) * torch.sin(pi*cell_centers[:,2:3])

# Analytical solution function for boundary conditions and error calculation
def analytical_solution(x, y, z):
    """MMS analytical solution: phi = 0.1 * sin(pi*x) * sin(pi*y) * sin(pi*z)"""
    return 0.1 * torch.sin(pi*x) * torch.sin(pi*y) * torch.sin(pi*z)

Flux_C = torch.zeros((cells_face.shape[0],gDiff_b.shape[1]),device = device)
Flux_V = torch.zeros((cells_face.shape[0],gDiff_b.shape[1]),device = device)

# Initialize interpolator
FV_interp = FV_Interpolation()
FV_interp.register_geometrics(fv_graph.graph_cell, fv_graph.graph_face, fv_graph.graph_Index)
num_faces = fv_graph.graph_face._num_faces

num_channel = 1 # Poisson equation is a scalar equation
grad_phi_face = torch.full((num_faces, num_channel, 3), 0., device=device)
phi_face  = torch.full((num_faces, num_channel), 0., device=device)

# Convergence control parameters
max_epochs = 100
tolerance = 1e-7  # Residual convergence tolerance
print_frequency = 10  # How often to print residuals (can be set to 1 to print every step)
use_relative_residual = True  # Whether to use relative residual as convergence criterion
monitor_iteration_time = False  # Whether to monitor iteration time

print(f"Starting Poisson equation solving")
print(f"Maximum iterations: {max_epochs}")
print(f"Convergence tolerance: {tolerance:.2e}")
print(f"Convergence criterion: {'Relative residual' if use_relative_residual else 'Absolute residual'}")
print(f"Grid size: {num_cells} cells")
print("=" * 80)
if monitor_iteration_time:
    print(f"{'Iter':>4s} {'Sol_Change':>12s} {'Rel_Change':>12s} {'Lin_Iters':>10s} {'Time(s)':>8s} {'Status':>10s}")
    print("-" * 88)
else:
    print(f"{'Iter':>4s} {'Sol_Change':>12s} {'Rel_Change':>12s} {'Lin_Iters':>10s} {'Status':>10s}")
    print("-" * 80)

# Residual history record (optional)
residual_history = []

# Start timing
solve_start_time = time.time()

for i in range(max_epochs):

    # Single iteration timing start
    if monitor_iteration_time:
        iter_start_time = time.time()

    # Apply MMS boundary conditions - all boundaries use analytical solution values
    boundary_mask = boundary_cellf  # All boundary cells
    boundary_coords = cpd_centroid[boundary_mask]
    cpd_cell_value[boundary_mask] = analytical_solution(
        boundary_coords[:,0:1],
        boundary_coords[:,1:2],
        boundary_coords[:,2:3]
    )
    
    # First calculate the gradient of the entire field at the current iteration step, for subsequent non-orthogonal corrections
    grad_phi = gradient_reconstruction(
        order=params.order,
        phi_node=cpd_cell_value,
        edge_index=fv_graph.graph_cell_x.neighbor_cell_x,
        mesh_pos=fv_graph.graph_cell.cpd_centroid,
    )[:,:,0:3] # [N_cpd_cells, 1, 3], only extract gradient, i.e., first-order derivative is sufficient
    
    # Start interpolating gradients and phi to faces
    grad_phi_face[FV_interp.mask_interior_face,0,0:3] = FV_interp.interpolating_gradients_to_faces(
        phi_cell=cpd_cell_value, # Input needs to be cpd version, automatically replaced with internal neighbor_cell in the interpolator
        grad_phi_cell=grad_phi, # Input needs to be cpd version, automatically replaced with internal neighbor_cell in the interpolator
    )
    grad_phi_face[FV_interp.mask_boundary_face] = grad_phi[FV_interp.mask_boundary_cell] # Boundary gradients are directly calculated

    phi_face[FV_interp.mask_interior_face] = FV_interp.interpolating_phic_to_faces(
        phi_cell=cpd_cell_value, # [Cell, Nc] Cell is number of cells, Nc is number of channels
        grad_phi_cell=grad_phi, # [Cell, Nc, 3] Cell is number of cells, Nc is number of channels, 3 is gradient components in 3D
    ).view(-1,num_channel)
    phi_face[FV_interp.mask_boundary_face] = cpd_cell_value[FV_interp.mask_boundary_cell] # Boundary values remain unchanged as boundary conditions
    
    ''' >>> Assemble coefficient matrix >>> '''
    a_F = -diff_coeff*gDiff_f[:,0] # Off-diagonal coefficients
    
    Flux_Cf = diff_coeff*gDiff_f[:,0]
    Flux_C[internal_cells_face_mask] = Flux_Cf
    Flux_Cb = diff_coeff*gDiff_b[:,0]
    Flux_C[boundary_cells_face_mask] = Flux_Cb
    
    a_C = scatter(
        src = Flux_C,
        index = owner,
        dim=0,
        dim_size=num_cells,
        reduce='sum',
    ) # Main diagonal coefficients
    
    Flux_V[internal_cells_face_mask] = (diff_coeff*grad_phi_face[internal_cells_face] @ Tf_vec_i).squeeze(1)
    Flux_V[boundary_cells_face_mask] = (
        diff_coeff*gDiff_b[:,0]*phi_face[boundary_cells_face] +\
        ((diff_coeff*grad_phi_face[boundary_cells_face]) @ Tf_vec_b)[:,0]
    )
    
    b_C = cells_volume * Qv +\
        scatter(
            src = Flux_V,
            index = owner,
            dim=0,
            dim_size=num_cells,
            reduce='sum',
        ) # Right-hand side (RHS)
    
    A_sparse = assemble_matrix_sparse(owner_i, neighbor_i, a_C, a_F, num_cells, device)
    ''' <<< Assemble coefficient matrix <<< '''

    # 6. Solve linear system
    B = b_C

    # Save previous solution for residual calculation
    if i == 0:
        cpd_cell_value_old = cpd_cell_value.clone()
    else:
        cpd_cell_value_old = cpd_cell_value.clone()

    # Use sparse solver to solve linear system
    solution, converged, iterations = solve_sparse_system(A_sparse, B.squeeze(), max_iter=3000, tol=1e-10)
    if not converged:
        print(f"  Warning: Linear solver did not converge in iteration {i+1}, iterations: {iterations}")

    cpd_cell_value[internal_cell] = solution.unsqueeze(1)

    # Calculate nonlinear iteration residual (solution change)
    # Method 1: Calculate absolute change of solution ||x_new - x_old||
    residual_solution = torch.norm(cpd_cell_value[internal_cell] - cpd_cell_value_old[internal_cell]).item()

    # Method 2: Calculate relative change of solution
    solution_norm = torch.norm(cpd_cell_value[internal_cell]).item()
    relative_residual = residual_solution / (solution_norm + 1e-12)  # Avoid division by zero

    # Single iteration timing end
    if monitor_iteration_time:
        iter_end_time = time.time()
        iter_time = iter_end_time - iter_start_time
    else:
        iter_time = 0.0

    # Record residual history
    residual_history.append({
        'iteration': i + 1,
        'solution_change': residual_solution,
        'relative_residual': relative_residual,
        'linear_iterations': iterations,
        'iteration_time': iter_time
    })

    # Select convergence criterion
    convergence_residual = relative_residual if use_relative_residual else residual_solution

    # Determine convergence status
    if i == 0:
        status = "Initial"
    elif convergence_residual < tolerance:
        status = "Converged"
    elif convergence_residual < tolerance * 10:
        status = "Near Conv"
    else:
        status = "Iterating"

    # Print residual information
    if i % print_frequency == 0 or i == max_epochs - 1 or status == "Converged":
        if monitor_iteration_time:
            print(f"{i+1:4d} {residual_solution:12.6e} {relative_residual:12.6e} {iterations:10d} {iter_time:8.4f} {status:>10s}")
        else:
            print(f"{i+1:4d} {residual_solution:12.6e} {relative_residual:12.6e} {iterations:10d} {status:>10s}")

    # Convergence check
    if convergence_residual < tolerance and i > 0:
        print("-" * 80)
        print(f"✓ Convergence achieved! At iteration {i+1}")
        print(f"Final {'relative' if use_relative_residual else 'absolute'} residual: {convergence_residual:.6e} < {tolerance:.6e}")
        print("=" * 80)
        break
else:
    # If loop ends normally (no break), it means not converged
    print(f"\nWarning: Not converged after {max_epochs} iterations!")
    print(f"Final relative residual: {relative_residual:.6e} >= {tolerance:.6e}")
    print("Suggestion: Increase maximum iterations or relax convergence tolerance")
    print("=" * 80)

# Calculate total time
solve_end_time = time.time()
total_solve_time = solve_end_time - solve_start_time

# Calculate MMS error
internal_coords = cpd_centroid[internal_cell]
analytical_values = analytical_solution(
    internal_coords[:,0:1],
    internal_coords[:,1:2],
    internal_coords[:,2:3]
)
numerical_values = cpd_cell_value[internal_cell]

# Calculate various error norms
L1_error = torch.mean(torch.abs(numerical_values - analytical_values)).item()
L2_error = torch.sqrt(torch.mean((numerical_values - analytical_values)**2)).item()
Linf_error = torch.max(torch.abs(numerical_values - analytical_values)).item()

print(f"\n=== MMS Error Analysis ===")
print(f"L1 error:   {L1_error:.6e}")
print(f"L2 error:   {L2_error:.6e}")
print(f"L∞ error:   {Linf_error:.6e}")
print(f"Grid cells: {num_cells}")

# Estimate grid spacing (assuming cubic domain)
domain_volume = 1.0  # Unit cube
avg_cell_volume = domain_volume / num_cells
dx = avg_cell_volume**(1/3)  # Characteristic grid spacing
print(f"Characteristic grid spacing dx: {dx:.6f}")

print(f"\nSolving completed, final solution range: [{cpd_cell_value[internal_cell].min().item():.6f}, {cpd_cell_value[internal_cell].max().item():.6f}]")
print(f"Analytical solution range: [{analytical_values.min().item():.6f}, {analytical_values.max().item():.6f}]")
print(f"Total solving time: {total_solve_time:.3f} seconds")
print(f"Average time per iteration: {total_solve_time / len(residual_history):.4f} seconds")

# Print convergence history summary
if len(residual_history) > 1:
    initial_residual = residual_history[0]['relative_residual']
    final_residual = residual_history[-1]['relative_residual']
    reduction_factor = initial_residual / (final_residual + 1e-12)
    print(f"Residual reduction factor: {reduction_factor:.2e} (from {initial_residual:.2e} to {final_residual:.2e})")

# Performance statistics
print("\nPerformance Statistics:")
print("-" * 40)
print(f"Actual iterations: {len(residual_history)}")
print(f"Grid size: {num_cells} cells")
print(f"Matrix non-zero elements: {len(owner_i) * 2 + num_cells}")  # Estimate non-zero elements
print(f"Solving efficiency: {num_cells / total_solve_time:.0f} cells/second")

if monitor_iteration_time and len(residual_history) > 0:
    iteration_times = [r['iteration_time'] for r in residual_history if r['iteration_time'] > 0]
    if iteration_times:
        avg_iter_time = sum(iteration_times) / len(iteration_times)
        min_iter_time = min(iteration_times)
        max_iter_time = max(iteration_times)
        print(f"Average iteration time: {avg_iter_time:.4f} seconds")
        print(f"Fastest iteration time: {min_iter_time:.4f} seconds")
        print(f"Slowest iteration time: {max_iter_time:.4f} seconds")

# Optional: Save residual history to file
# import json
# with open(f"{father_dir}/residual_history.json", 'w') as f:
#     json.dump(residual_history, f, indent=2)

''' Save gradients to vtu file '''
cpd_cell_type = fv_graph.graph_cell.cpd_cell_type
interior_cell_mask = (cpd_cell_type== FaceType.NORMAL)  # NORMAL
father_dir = os.path.dirname(logger.saving_path)
for _ in range(1):
    father_dir = os.path.dirname(father_dir)
case_name = "".join(
    chr(code) for code in fv_graph.graph_cell.case_name.cpu().tolist()
)
os.makedirs(f"{father_dir}/Possion", exist_ok=True)

# Prepare VTU output data for 3D meshes
vtu_data = {
    "cell|Qv": Qv.cpu().numpy(),
    "cell|cpd_cell_value": cpd_cell_value[interior_cell_mask].cpu().numpy(),
}
export_full_mesh_vtu(
    mesh_pos=datasets.meta_pool[fv_graph.graph_cell.graph_index.cpu().item()]["node|pos"], 
    pv_cells_node=datasets.meta_pool[fv_graph.graph_cell.graph_index.cpu().item()]["pv_cells_node"], 
    pv_cells_type=datasets.meta_pool[fv_graph.graph_cell.graph_index.cpu().item()]["pv_cells_type"],
    save_file_path=f"{father_dir}/Possion/Torch_FVM_{case_name}_{params.order}.vtu",
    data_dict=vtu_data
)
''' Save gradients to vtu file '''
