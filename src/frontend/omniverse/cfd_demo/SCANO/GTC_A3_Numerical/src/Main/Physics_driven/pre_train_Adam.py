#!/usr/bin/env python3
# Training script with support for both Adam and SOAP optimizers
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

from torch.optim import Adam
import numpy as np
from FVmodel.importer import NNmodel as Simulator

# Import SOAP optimizer
from Utils.local_optimizer import SOAP
from FVdomain.Graph_loader import HeteroDatasetFactory

from Utils import get_param
import time
from Utils.get_param import get_hyperparam
from Utils.Logger import Logger
from math import ceil
import random

torch.set_float32_matmul_precision('high')

# configurate parameters
params = get_param.params()
seed = int(777) # int(datetime.datetime.now().timestamp())
np.random.seed(seed)
random.seed(seed)
torch.manual_seed(seed)
torch.cuda.set_per_process_memory_fraction(0.99, params.on_gpu)
torch.set_num_threads(os.cpu_count() // 2)
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

# initialize Logger and load model / optimizer if according parameters were given
logger = Logger(
    get_hyperparam(params),
    use_csv=True,
    params=params,
    copy_code=True,
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
fluid_model.train()

# Choose optimizer: Adam or SOAP
use_soap = True  # Set to False to use Adam

if use_soap:
    # SOAP optimizer with recommended settings for minibatch training
    # SOAP (ShampoO with Adam in the Preconditioner's eigenbasis) is a second-order optimizer
    # that combines Shampoo's preconditioning with Adam's stability
    optimizer = SOAP(
        fluid_model.parameters(),
        lr=params.lr,
        betas=(0.95, 0.95),         # Recommended betas for SOAP (higher than Adam's default)
        weight_decay=0.01,          # L2 regularization
        precondition_frequency=10,  # Update preconditioner every 10 steps (balance between performance and memory)
        max_precond_dim=10000,      # Maximum dimension for preconditioning (excludes large vocab layers)
        precondition_1d=False,      # Don't precondition 1D parameters (bias terms)
        normalize_grads=False,      # Don't normalize gradients for small precondition_frequency
        eps=1e-8,                   # Numerical stability
        correct_bias=True,          # Use bias correction like Adam
    )
    print("✅ Using SOAP optimizer - Second-order optimization with preconditioning")
    print(f"   - Learning rate: {params.lr}")
    print(f"   - Precondition frequency: 10 steps")
    print(f"   - Max precondition dimension: 10000")
else:
    optimizer = Adam(fluid_model.parameters(), lr=params.lr)
    print("✅ Using Adam optimizer")

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


# training loop
for epoch in range(params.n_epochs):
    fluid_model.train()
    refresh_pool = False

    # 这里控制重置pool中case重置的频率，以实现多case多time_state的目的，避免overfit到某个固定时间步
    if epoch % ceil(params.average_sequence_length / (params.dataset_size)) == 0:
        rst_time = ceil(params.dataset_size / (params.average_sequence_length))
        datasets._set_reset_env_flag(flag=True, rst_time=rst_time)

    start = time.time()
    for i_iter in range(params.max_inner_steps):

        # 仅在最后一个内迭代步执行放回
        if i_iter == params.max_inner_steps - 1:
            refresh_pool=True

        for batch_index, fv_graph in enumerate(loader):

            # Move to device and preprocess
            fv_graph = datasets.datapreprocessing_hetero(fv_graph.to(device,exclude_keys=['global_idx']))

            optimizer.zero_grad()
            (
                loss,
                uvwp_cell_new,
            ) = fluid_model(
                graph_node=fv_graph.graph_node,
                graph_face=fv_graph.graph_face,
                graph_cell=fv_graph.graph_cell,
                graph_cell_x=fv_graph.graph_cell_x,
                graph_Index=fv_graph.graph_Index,
                is_training=True,
            )

            # compute gradients
            loss.backward()

            # perform optimization step
            optimizer.step()

            if refresh_pool:
                # put back to dataset pool
                datasets.refresh_pool(
                    uvwp_new=uvwp_cell_new.detach().cpu(),
                    global_idx=fv_graph.graph_cell.global_idx,
                )

    # 输出此epoch的处理时间
    print(f"Epoch {epoch} Completed in {time.time() - start:.2f} seconds")
    print(f"Epoch {epoch} Loss {loss.item():.5f}")

    # save state after every 2 epoch
    if (epoch % 10 == 0) or (epoch == params.n_epochs - 1):
        model_saving_path = logger.save_state(
            model=fluid_model,
            optimizer=None,
            scheduler=None,
            index=str(epoch % 3),
        )

print("✅ Training Done!")
