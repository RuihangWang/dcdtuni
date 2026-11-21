#!/usr/bin/env python3
# ODIL-inspired PDE solver with adaptive loss weighting and improved optimization
import sys
import os
os.environ['TORCH_COMPILE_BACKEND'] = "eager" # Force eager mode to disable torch.compile for ConFIG compatibility

import warnings
warnings.filterwarnings("ignore", message=".*Sparse CSR tensor support is in beta state.*")

cur_path = os.path.split(__file__)[0]
sys.path.append(cur_path)

import torch
import numpy as np
from FVsolver.FVdiscretization.FVscheme import Intergrator

# Import optimizers
from Utils.local_optimizer import get_optimizer
from Utils.local_scheduler import get_scheduler
from FVdomain.Graph_loader import HeteroDatasetFactory

from Utils import get_param
from Utils.get_param import get_hyperparam
from Utils.Logger import Logger
import random
from Post_process.to_vtk import export_full_mesh_vtu


class PDETrainer:
    """PDE求解器训练类"""

    def __init__(
        self,
        uvwp_new_cell,
        uvwp_old_cpd_cell,
        fv_graph,
        params,
        integrator,
        optimizer,
        scheduler,
        device,
        logger=None,
        use_conflict_free=True,
        ):
        """
        初始化训练器

        Args:
            params: 参数对象
            integrator: FVM积分器
            optimizer: 优化器
            scheduler: 学习率调度器
            device: 设备
            uvwp_new_cell: 新的解向量
            uvwp_old_cpd_cell: 旧的解向量
            fv_graph: 图组件 (graph_node, graph_face, graph_cell, graph_cell_x, graph_Index)
            logger: 日志记录器
            use_conflict_free: 是否使用conflict-free优化
        """
        self.params = params
        self.integrator = integrator
        self.optimizer = optimizer
        self.scheduler = scheduler
        self.device = device
        self.uvwp_new_cell = uvwp_new_cell
        self.uvwp_old_cpd_cell = uvwp_old_cpd_cell
        self.logger = logger

        # 解包图组件
        self.fv_graph = fv_graph
        self.graph_node = fv_graph.graph_node
        self.graph_face = fv_graph.graph_face
        self.graph_cell = fv_graph.graph_cell
        self.graph_cell_x = fv_graph.graph_cell_x
        self.graph_Index = fv_graph.graph_Index

        # 初始化损失记录
        self.losses = []
        self.mom_losses = []
        self.loss_init_total = torch.ones((self.graph_cell.num_graphs, 3), device=device)

    def compute_loss(self, iteration=0):
        """计算损失函数（兼容原有接口）"""
        # 原有的损失计算逻辑
        (loss_total, uvwp_new_cpd_cell) = self.integrator(
            uvwp_old_cpd_cell=self.uvwp_old_cpd_cell.detach(),
            uvwp_new_cell=self.uvwp_new_cell,
        )

        if torch.isnan(loss_total).any() or torch.isinf(loss_total).any():
            print(f"❌ 损失为NaN或Inf，跳过此迭代")
            return (
                torch.tensor(1e6, device=self.device, requires_grad=True),
                torch.tensor(1e6, device=self.device, requires_grad=True),
                torch.tensor(1e6, device=self.device, requires_grad=True),
                torch.tensor(1e6, device=self.device, requires_grad=True),
                uvwp_new_cpd_cell
            )

        loss_cont_L2, loss_momentum_L2, loss_phi_jump_L2 = self.integrator.get_separated_losses()

        if iteration == 0.:
            self.loss_init_total = torch.cat((loss_cont_L2, loss_momentum_L2, loss_phi_jump_L2), dim=1).detach()

        loss_cont_L2 /= self.loss_init_total[:,0:1]
        loss_momentum_L2 /= self.loss_init_total[:,1:2]
        loss_phi_jump_L2 /= self.loss_init_total[:,2:3] if (self.loss_init_total[:,2:3]!=0.).all() else 1.

        loss_total = torch.log(self.params.loss_cont*loss_cont_L2 + \
            self.params.loss_mom*loss_momentum_L2 + self.params.loss_phi_jump*loss_phi_jump_L2)

        # 返回总损失、动量损失、连续性损失、phi_jump损失
        return (
            torch.mean(loss_total),
            torch.mean(loss_momentum_L2),
            torch.mean(loss_cont_L2),
            torch.mean(loss_phi_jump_L2),
            uvwp_new_cpd_cell
        )

    def get_variable_gradient_vector(self, variable):
        """获取变量的梯度向量（适配ConFIG的接口）"""
        if variable.grad is None:
            return torch.zeros_like(variable.view(-1))
        return variable.grad.view(-1)

    def apply_variable_gradient_vector(self, variable, grad_vec):
        """将梯度向量应用到变量（适配ConFIG的接口）"""
        variable.grad = grad_vec.view(variable.shape)

    def optimization_step_standard(self, iteration=0):
        """执行标准优化步骤"""
        self.optimizer.zero_grad()
        loss_total, loss_mom, loss_cont, loss_phi_jump, uvwp_new_cpd_cell = self.compute_loss(iteration)
        loss_total.backward()
        self.optimizer.step()
        return loss_total, loss_mom, loss_cont, loss_phi_jump, uvwp_new_cpd_cell

    def optimization_step(self, iteration=0):
        """执行一步优化（自动选择方法）"""

        return self.optimization_step_standard(iteration)

    def check_early_stopping(self, current_lr, current_loss_mom):
        """检查早停条件"""
        # 检查动量损失是否已经足够小
        if current_loss_mom <= self.params.early_stop_threshold:
            print(f"🛑 早停触发！")
            print(f"   动量损失已降至目标阈值: {current_loss_mom:.8e} <= {self.params.early_stop_threshold:.8e}")
            print(f"   当前学习率: {current_lr:.8e}")
            return True

        return False

    def optimize(self):
        """主优化循环"""
        print(f"\n=== 开始优化 ({self.params.n_epochs} 次迭代) ===")
        for i in range(self.params.n_epochs):
            # 执行优化步骤
            loss_total, loss_mom, loss_cont, loss_phi_jump, uvwp_new_cpd_cell = self.optimization_step(i)

            # 记录损失
            current_loss_total = loss_total.item()
            current_loss_mom = loss_mom.item()
            current_loss_cont = loss_cont.item()
            current_loss_phi_jump = loss_phi_jump.item()
            self.losses.append(current_loss_total)
            self.mom_losses.append(current_loss_mom)

            self.logger.log_residuals(
                iteration_step=float(i),
                loss_total_L2=current_loss_total,
                loss_momentum_L2=current_loss_mom,
                loss_cont_L2=current_loss_cont,
                loss_phi_jump_L2=current_loss_phi_jump
            )

            # 根据调度器类型决定是否传入损失值
            if self.params.scheduler_type.lower() == "plateau":
                # ReduceLROnPlateau需要传入损失值
                self.scheduler.step(current_loss_mom)
            else:
                # 其他调度器(cosine, step, exponential)不需要传入损失值
                self.scheduler.step()

            # 打印进度
            if (i + 1) % self.params.print_freq == 0 or i < 5:
                current_lr = self.optimizer.param_groups[0]['lr']
                # 使用一个通用的最小学习率阈值来判断
                min_lr_threshold = 1e-4
                lr_status = "最低值" if current_lr <= min_lr_threshold else "调整中"
                print(f"  第 {i+1:5d} 次迭代, Loss cont: {current_loss_cont:.8e}, Loss mom: {current_loss_mom:.8e}, Loss phi jump: {current_loss_phi_jump:.8e},学习率: {current_lr:.8e} ({lr_status})")

            # 早停检测
            current_lr = self.optimizer.param_groups[0]['lr']
            if self.check_early_stopping(current_lr, current_loss_mom):
                print(f"第{i+1}次迭代")
                break

        # 优化结束后，将剩余的残差数据写入文件
        if self.logger is not None:
            self.logger.finalize_residuals()


    def print_final_results(self):
        """打印最终结果"""
        if len(self.losses) > 0:
            print(f"✅ 优化完成！")
            print(f"   最终损失: {self.losses[-1]:.8e}")
            print(f"   最佳损失: {min(self.losses):.8e}")
            print(f"   总迭代次数: {len(self.losses)}")

            # 计算最终残差
            with torch.no_grad():
                loss_cont_L2, loss_mom_L2, loss_phi_jump_L2 = self.integrator.get_separated_losses()
                cont_loss_mean, mom_loss_mean, phi_jump_mean = torch.mean(loss_cont_L2).item(), torch.mean(loss_mom_L2).item(), torch.mean(loss_phi_jump_L2).item()
                print(f"    最后一次损失: cont={cont_loss_mean:.8e}, mom={mom_loss_mean:.8e}, phi_jump={phi_jump_mean:.8e}")

            self.uvwp_new_cell = self.uvwp_new_cell.detach()
            print(f"速度范围: u=[{self.uvwp_new_cell[:,0].min():.3f}, {self.uvwp_new_cell[:,0].max():.3f}]")
            print(f"         v=[{self.uvwp_new_cell[:,1].min():.3f}, {self.uvwp_new_cell[:,1].max():.3f}]")
            print(f"         w=[{self.uvwp_new_cell[:,2].min():.3f}, {self.uvwp_new_cell[:,2].max():.3f}]")
            print(f"压力范围: p=[{self.uvwp_new_cell[:,3].min():.3f}, {self.uvwp_new_cell[:,3].max():.3f}]")
        else:
            print("❌ 优化失败")


def export_results(uvwp_new_cell, meta_data, logger, case_name):
    """导出VTU结果文件"""
    export_full_mesh_vtu(
        mesh_pos=meta_data["node|pos"],
        pv_cells_node=meta_data["pv_cells_node"],
        pv_cells_type=meta_data["pv_cells_type"],
        data_dict={
            "cell|Velocity": uvwp_new_cell[:,0:3].cpu(),
            "cell|Pressure": uvwp_new_cell[:,3:4].cpu(),
        },
        save_file_path=f"{logger.saving_path}/rollout_results/{case_name}/steady_solution",
        t=np.float32(0.0)
    )
    print("✅ VTU文件已导出")


def main():
    """主函数"""
    print("🚀 开始PDE求解...")

    # 初始化参数和设备
    params = get_param.params()
    
    """设置训练超参数"""
    # 基础设置
    params.on_gpu = 5
    params.net = "TransFVGN_v2"
    params.dataset_dir = "mesh_example/Cylinder_flow_3D_cyclic_refined"
    params.load_date_time = None
    params.load_index = None
    params.dataset_size = 1
    params.batch_size = 1
    params.integrator = "implicit"
    params.loss_cont = 1e5
    params.loss_mom = 1e5
    params.loss_phi_jump = 0
    params.cfc_smooth = False

    # ConFIG conflict-free优化设置
    params.use_conflict_free = True     # 是否使用ConFIG conflict-free优化

    # 优化器选择和参数
    params.optimizer_type = "soap"      # 优化器类型: "adam" 或 "soap"
    params.lr = 1                       # 学习率 (将被调度器覆盖)

    # Adam优化器参数
    params.adam_betas = (0.9, 0.999)    # Adam动量参数
    params.adam_eps = 1e-8              # Adam数值稳定性参数

    # SOAP优化器参数
    params.soap_betas = (0.95, 0.95)    # SOAP推荐的动量参数
    params.soap_weight_decay = 0.01     # L2正则化
    params.soap_precond_freq = 10       # 预条件更新频率
    params.soap_max_precond_dim = 10000 # 最大预条件维度
    params.soap_precond_1d = False      # 是否预条件1D参数
    params.soap_normalize_grads = False # 是否归一化梯度
    params.soap_correct_bias = True     # 是否使用偏置校正

    params.scheduler_type = "progressive_restart"  # 调度器类型
    params.n_epochs = 2000                       # 总训练步数
    
    # 简化的早停参数（监视动量损失绝对值）
    params.early_stop_threshold = 1e-5  # 早停动量损失最大阈值（当loss_mom <= 1e-5时停止）

    # 输出控制参数
    params.print_freq = 500   # 损失打印频率

    # 种子参数
    seed = int(777)
    np.random.seed(seed)
    random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.set_per_process_memory_fraction(0.99, device=params.on_gpu)
    torch.set_num_threads(os.cpu_count() // 2)
    torch.set_float32_matmul_precision('highest')
    device = torch.device(f"cuda:{params.on_gpu}" if torch.cuda.is_available() else "cpu")
    print(f"✅ 使用设备: {device}")

    # 1. 设置数据集和模型
    print("🔧 初始化数据集和模型...")

    # initialize Logger
    logger = Logger(
        get_hyperparam(params),
        use_csv=True,
        params=params,
        copy_code=True,
        seed=seed,
    )

    # initialize Training Dataset
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

    # 2. 设置图数据和解向量
    fv_graph = next(iter(loader))
    meta_data = datasets.meta_pool[fv_graph.graph_Index.graph_index.cpu().item()]
    os.makedirs(f"{logger.saving_path}/rollout_results/{case_name}", exist_ok=True)

    # Move to device and preprocess
    fv_graph = datasets.datapreprocessing_hetero(fv_graph.to(device,exclude_keys=['global_idx']))

    integrator = torch.compile(Intergrator(params, fv_graph))

    # create initial guess
    uvwp_new_cell = (torch.ones((integrator.num_cells, 4), device=device) + \
        torch.randn((integrator.num_cells, 4), device=device)).requires_grad_(True)
    uvwp_old_cpd_cell = torch.ones((integrator.num_cpd_cells, 4), device=device, requires_grad=False)

    # 3. 设置优化器和调度器
    optimizer = get_optimizer(params.optimizer_type, [uvwp_new_cell], params)

    # 调度器配置
    scheduler = get_scheduler(params.scheduler_type, optimizer,
                            restart_window_size=2000,      # 重启窗口大小
                            restart_window_multiplier=200,  # 重启窗口数量
                            initial_max_lr=1.0,            # 初始最大学习率
                            restart_decay_factor=0.5,      # 重启衰减因子
                            min_restart_lr=1e-2,           # 最低重启学习率
                            lr_min_lr=5e-5)                # 周期内最小学习率

    # 4. 创建训练器
    print("🔧 创建训练器...")
    trainer = PDETrainer(
        uvwp_new_cell=uvwp_new_cell,
        uvwp_old_cpd_cell=uvwp_old_cpd_cell,
        fv_graph=fv_graph,
        params=params,
        integrator=integrator,
        optimizer=optimizer,
        scheduler=scheduler,
        device=device,
        logger=logger,
        use_conflict_free=params.use_conflict_free,
    )

    print("✅ 初始化完成！")

    # 5. 执行优化
    trainer.optimize()

    # 6. 处理结果
    uvwp_new_cpd_cell=trainer.integrator.enforce_boundary_condition(trainer.uvwp_new_cell)
    uvwp_new_cell = trainer.integrator.cfc_smooth(uvwp_cpd_cell = uvwp_new_cpd_cell)
    trainer.print_final_results()
    export_results(uvwp_new_cell.detach().cpu(), meta_data, logger, case_name)

    print("✅ 求解完成")

if __name__ == "__main__":
    main()
