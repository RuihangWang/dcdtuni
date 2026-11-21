import sys
import os

# add src to path
cur_path = os.path.split(__file__)[0]
parent_path = os.path.dirname(cur_path)
sys.path.append(cur_path)
sys.path.append(parent_path)

import torch
import numpy as np


from Utils import get_param
from Utils.get_param import get_hyperparam
from Utils.Logger import Logger
from Post_process.to_vtk import export_full_mesh_vtu
import random
import datetime
from FVmodel.FVdiscretization.FVgrad import gradient_reconstruction
from FVmodel.FVdiscretization.FVscheme import Intergrator
from FVdomain.Graph_loader import HeteroDatasetFactory

# configurate parameters
params = get_param.params()
seed = int(datetime.datetime.now().timestamp())
np.random.seed(seed)
random.seed(seed)
torch.manual_seed(seed)
torch.cuda.set_per_process_memory_fraction(0.8, params.on_gpu)
torch.set_float32_matmul_precision('highest')

device = "cuda:4"
params.dataset_dir="mesh_example/Cylinder_flow_3D_cyclic"
params.dataset_size=1
params.batch_size=1
params.order = "1st" # 1st, 2nd, 3rd, 4th, GG

# initialize Logger and load model / optimizer if according parameters were given
logger = Logger(
    get_hyperparam(params),
    use_csv=False,
    params=params,
    copy_code=False,
    seed=seed,
)

# initialize Dataset
datasets_factory = HeteroDatasetFactory(
    params=params,
    dataset_dir=params.dataset_dir,
    state_save_dir=logger.saving_path,
    device=device,
)

# create dataset object
datasets, loader = datasets_factory.create_loader(
    batch_size=params.batch_size, num_workers=0, pin_memory=False, shuffle=True
)

''' >>> fetch data and move to GPU >>> '''
fv_graph = next(iter(loader))
fv_graph = datasets.datapreprocessing_hetero(fv_graph.to(device,exclude_keys=['global_idx']))
''' <<< fetch data and move to GPU <<< '''

# initialize fluid model
integrator = Intergrator(params, fv_graph)

# calcualate phi node value for 3D
from Utils.utilities import Scalar_Eular_solution_3D

# Use 3D version for 3D meshes
phi_GT_cpd_cell, grad_phi_GT_cpd_cell, hessian_phi_GT_cpd_cell = Scalar_Eular_solution_3D(
    mesh_pos=fv_graph.graph_cell.cpd_centroid,
    phi_0=1.0,
    phi_x=0.01,
    phi_y=0.01,
    phi_z=0.01,  # z component for 3D
    phi_xy=0.01,
    phi_xz=0.01,  # xz component for 3D
    phi_yz=0.01,  # yz component for 3D
    alpha_x=1,
    alpha_y=1,
    alpha_z=1,  # z component for 3D
    alpha_xy=2,
    alpha_xz=2,  # xz component for 3D
    alpha_yz=2,  # yz component for 3D
    L=2.5,
    device=device,
)

mask_interior_cell = integrator.mask_interior_cell
''' >>> Weighted-LSQ >>> '''
grad_phi = gradient_reconstruction(
    order=params.order,
    phi_node=phi_GT_cpd_cell,
    edge_index=integrator.cpd_neighbor_cell_x,
    mask_valid_node=integrator.mask_interior_cell,
    mesh_pos=integrator.cpd_centroid,
    precompute_Moments=[integrator.A_cell_to_cell, integrator.single_B_cell_to_cell],
)[:, :, 0:3]  # Extract gradients only: [N_cells, 7_variables, 3_spatial_dimensions]
''' <<< Weighted-LSQ <<< '''


''' >>> Green-Gauss Gradient >>> '''
# grad_phi = gradient_reconstruction(
#     order=params.order,
#     phi_cpd_cell=phi_GT_cpd_cell,
#     cpd_neighbor_cell=integrator.cpd_neighbor_cell,
#     cells_volume=integrator.cells_volume,
#     cells_face_surface_vec=integrator.cells_face_surface_vec,
#     cells_face=integrator.cells_face,
#     cells_face_ptr=integrator.cells_face_ptr,
#     mask_interior_cell=integrator.mask_interior_cell,
#     mask_boundary_face=integrator.mask_boundary_face,
#     gC=integrator.gC,
#     gF=integrator.gF,
#     Cf=integrator.Cf,
#     Ff=integrator.Ff,
# )
# with torch.autocast(device_type='cuda', dtype=torch.float64):
#     phi_GT_face, _, _ = Scalar_Eular_solution_3D(
#         mesh_pos=fv_graph.graph_face.pos,
#         phi_0=1.0,
#         phi_x=0.01,
#         phi_y=0.01,
#         phi_z=0.01,  # z component for 3D
#         phi_xy=0.01,
#         phi_xz=0.01,  # xz component for 3D
#         phi_yz=0.01,  # yz component for 3D
#         alpha_x=1,
#         alpha_y=1,
#         alpha_z=1,  # z component for 3D
#         alpha_xy=1,
#         alpha_xz=1,  # xz component for 3D
#         alpha_yz=1,  # yz component for 3D
#         L=250,
#         device=device,
#     )
#     face_node = integrator.face_node
#     face_node_ptr = integrator.face_node_ptr
#     grad_phi = scatter_add(
#         src=phi_GT_face[integrator.cells_face,:,None]*(integrator.cells_face_surface_vec[:,None]),
#         index=integrator.cells_face_ptr,
#         dim=0,
#         dim_size=integrator.num_cells,
#     )/(integrator.cells_volume[:,:,None])
''' <<< Green-Gauss Gradient <<< '''


'''>>> calculate the relative L2 error for gradients >>>'''
Grad_Relative_l2 = torch.norm(
    grad_phi[:, 0, 0:3] - grad_phi_GT_cpd_cell[mask_interior_cell, 0:3], dim=0
) / torch.norm(grad_phi_GT_cpd_cell[mask_interior_cell, 0:3], dim=0)

Grad_MSE = torch.mean((grad_phi[:, 0, 0:3] - grad_phi_GT_cpd_cell[mask_interior_cell, 0:3])**2)
print(f"Gradient Relative L2 error: {Grad_Relative_l2}")
print(f"Gradient MSE: {Grad_MSE}")
'''<<< calculate the relative L2 error for gradients <<<'''

''' 将梯度保存到vtu文件 '''
meta_data = datasets.meta_pool[fv_graph.graph_Index.graph_index.cpu().item()]
father_dir = os.path.dirname(logger.saving_path)
for _ in range(1):
    father_dir = os.path.dirname(father_dir)
case_name = meta_data["case_name"]
os.makedirs(f"{father_dir}/Grad_test", exist_ok=True)
# Prepare VTU output data for 3D meshes
vtu_data = {
    "cell|centroid" : integrator.cpd_centroid[mask_interior_cell].cpu().numpy(),
    "cell|phi_cell_GT": phi_GT_cpd_cell[mask_interior_cell].cpu().numpy(),
    "cell|grad_phi_GT": grad_phi_GT_cpd_cell[mask_interior_cell].cpu().numpy(),
    "cell|grad_phi": grad_phi[:,0,0:3].cpu().numpy(),  # 3D gradients
}
export_full_mesh_vtu(
    mesh_pos=meta_data["node|pos"], 
    pv_cells_node=meta_data["pv_cells_node"], 
    pv_cells_type=meta_data["pv_cells_type"],
    save_file_path=f"{father_dir}/Grad_test/grad_{case_name}_{params.order}.vtu",
    data_dict=vtu_data
)
print(f"✅ VTU export to: {father_dir}/Grad_test/grad_{case_name}_{params.order}.vtu")
''' 将梯度保存到vtu文件 '''
