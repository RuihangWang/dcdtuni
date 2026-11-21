#!/usr/bin/env python3
import sys
import os
import warnings
warnings.filterwarnings("ignore", message=".*Sparse CSR tensor support is in beta state.*")

cur_path = os.path.split(__file__)[0]
sys.path.append(cur_path)

import torch
# Force PyTorch to use eager mode (disable torch.compile)
torch._dynamo.config.suppress_errors = True
torch._dynamo.config.disable = True
torch.set_float32_matmul_precision('high')

import random
import time
import numpy as np

from FVmodel.importer import NNmodel as Simulator
from FVdomain.Graph_loader import HeteroDatasetFactory
from Utils.utilities import FaceType
from Utils import get_param
from Utils.get_param import get_hyperparam
from Utils.Logger import Logger
from Post_process.to_vtk import export_full_mesh_vtu

params = get_param.params()

''' set params'''
params.on_gpu = 3
params.net = "TransFVGN_v2"
params.dataset_dir = "mesh_example/Cylinder_flow_3D_cyclic"
params.load_date_time = "2025-09-02-00-27-51"
params.load_index = 2
params.dataset_size=1
params.batch_size=1
params.n_epochs = 500

seed = int(1) # int(datetime.datetime.now().timestamp())
np.random.seed(seed)
random.seed(seed)
torch.manual_seed(seed)
torch.cuda.set_per_process_memory_fraction(0.99, params.on_gpu)
torch.set_num_threads(os.cpu_count() // 2)
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

# initialize Logger and load model / optimizer if according parameters were given
logger = Logger(
    get_hyperparam(params),
    use_csv=False,
    params=params,
    copy_code=False,
    seed=seed,
)

# initialize Training Dataset
start = time.time()
datasets_factory = HeteroDatasetFactory(
    params=params,
    dataset_dir=params.dataset_dir,
    state_save_dir=logger.saving_path,
    device=device,
)

# create dataset objetc
datasets, loader = datasets_factory.create_loader(
    batch_size=params.batch_size, num_workers=0, pin_memory=False, shuffle=True
)

end = time.time()
print("Training traj has been loaded time consuming:{0}".format(end - start))

# initialize fluid model
model = Simulator(params)
fluid_model = model.to(device)
fluid_model.eval()

if (
    params.load_date_time is not None
    or params.load_index is not None
):
    logger.load_logger(datetime=params.load_date_time)
    params.load_date_time, params.load_index = logger.load_state(
        model=fluid_model,
        optimizer=None,
        scheduler=None,
        datetime=params.load_date_time,
        index=params.load_index,
        device=device,
    )
    params.load_index = int(params.load_index)
    print(f"loaded: {params.load_date_time}, {params.load_index}")
params.load_index = 0 if params.load_index is None else params.load_index

fv_graph = next(iter(loader))
meta_data = datasets.meta_pool[fv_graph.graph_Index.graph_index.cpu().item()]
mask_interior_cell = meta_data["cpd|cell_type"].long()==FaceType.NORMAL  # NORMAL
case_name = meta_data["case_name"]
dt = meta_data["dt"].squeeze().item()
os.makedirs(f"{logger.saving_path}/rollout_results/{case_name}", exist_ok=True)

with torch.no_grad():
    
    # training loop
    for epoch in range(params.n_epochs):
        fluid_model.eval()

        # Move to device and preprocess
        fv_graph = datasets.datapreprocessing_hetero(fv_graph.to(device,exclude_keys=['global_idx']))

        _, uvwp_new_cpd_cell = fluid_model(
            graph_node=fv_graph.graph_node,
            graph_face=fv_graph.graph_face,
            graph_cell=fv_graph.graph_cell,
            graph_cell_x=fv_graph.graph_cell_x,
            graph_Index=fv_graph.graph_Index,
            is_training=False,
        )
        
        fv_graph.graph_cell.x = uvwp_new_cpd_cell.detach()
        
        uvwp_new_cpu_cell = uvwp_new_cpd_cell.detach().cpu()[mask_interior_cell]

        # Time series export with t parameter
        export_full_mesh_vtu(
            mesh_pos=meta_data["node|pos"],
            pv_cells_node=meta_data["pv_cells_node"],
            pv_cells_type=meta_data["pv_cells_type"],
            data_dict={
                f"cell|Velocity":uvwp_new_cpu_cell[:,0:3], # [num_cells, 3]
                f"cell|Pressure":uvwp_new_cpu_cell[:,3:4], # [num_cells, 1]
            },
            save_file_path=f"{logger.saving_path}/rollout_results/{case_name}/time_series",
            t=np.float32(epoch*dt)  # Time value for time series
        )

print("✅ Rollout Done!")
