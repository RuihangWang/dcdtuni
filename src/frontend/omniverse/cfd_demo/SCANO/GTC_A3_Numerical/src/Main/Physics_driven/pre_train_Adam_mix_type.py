#!/usr/bin/env python3
import sys
import os
import warnings
warnings.filterwarnings("ignore", message=".*Sparse CSR tensor support is in beta state.*")

cur_path = os.path.split(__file__)[0]
sys.path.append(cur_path)

import torch
from torch.optim import Adam
import numpy as np
from FVmodel.importer import NNmodel as Simulator

from FVdomain.Graph_loader import HeteroDatasetFactory

from Utils import get_param
import time
from Utils.get_param import get_hyperparam
from Utils.Logger import Logger
from math import ceil
import random
import datetime

torch.set_float32_matmul_precision('high')

# configurate parameters
params = get_param.params()
seed = int(datetime.datetime.now().timestamp())
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
print(f"Mixed precision enabled")
model = Simulator(params)
fluid_model = model.to(device)
fluid_model.train()

# use GradScaler
scaler = torch.amp.GradScaler('cuda')
optimizer = Adam(fluid_model.parameters(), lr=params.lr)
print(f"--- GradScaler initial scale: {scaler.get_scale()}")

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

    if epoch % ceil(params.average_sequence_length / (params.dataset_size)) == 0:
        rst_time = ceil(params.dataset_size / (params.average_sequence_length))
        datasets._set_reset_env_flag(flag=True, rst_time=rst_time)

    start = time.time()

    for i_iter in range(params.max_inner_steps):
        
        if i_iter == params.max_inner_steps - 1:
            refresh_pool=True
            
        for batch_index, fv_graph in enumerate(loader):

            fv_graph = datasets.datapreprocessing_hetero(fv_graph.to(device,exclude_keys=['global_idx']))

            optimizer.zero_grad()

            with torch.amp.autocast('cuda'):
                
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

                # #保护损失函数，避免对负数或零取对数
                # loss_batch_safe = torch.clamp(loss_total, min=1e-8)
                # loss = torch.mean(torch.log(loss_batch_safe))

                # 使用scaler进行反向传播
                scaler.scale(loss).backward()
                
                # 🔧 修复2: 梯度裁剪
                scaler.unscale_(optimizer)
                torch.nn.utils.clip_grad_norm_(fluid_model.parameters(), max_norm=1.0)
                
                # 进行优化步骤
                scaler.step(optimizer)
                scaler.update()

            if refresh_pool:
                # put back to dataset pool
                datasets.refresh_pool(
                    uvwp_new=uvwp_cell_new.detach().cpu(),
                    global_idx=fv_graph.graph_cell.global_idx,
                )

    # 输出此epoch的处理时间
    print(f"Epoch {epoch} Completed in {time.time() - start:.2f} seconds")
    print(f"Epoch {epoch} Loss {loss.item():.5f}")
    print(f"Epoch {epoch} GradScaler scale: {scaler.get_scale():.0f}")
    
    # save state after every 2 epoch
    if (epoch % 50 == 0) or (epoch == params.n_epochs - 1):
        model_saving_path = logger.save_state(
            model=fluid_model,
            optimizer=None,
            scheduler=None,
            index=str(epoch % 3),
        )

print("✅ Mixed-Precision Training Done!")