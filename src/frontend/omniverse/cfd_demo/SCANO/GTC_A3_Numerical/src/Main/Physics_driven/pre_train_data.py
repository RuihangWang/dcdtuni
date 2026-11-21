#!/usr/bin/env python3
"""
分布式训练启动脚本

基于配置文件的分布式训练，无需命令行参数
"""

import os
import sys
import yaml
import torch
import torch.multiprocessing as mp
import torch.distributed as dist
import json
# 添加src路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# Configure PyTorch for development
from main.trainer import Trainer
from Utils.key import Key
from Utils.normalization import StandardDeviationNormalizer
from config import Config


def setup_distributed(rank, config):
    """设置分布式训练环境"""
    training_config = config.training_config

    os.environ['MASTER_ADDR'] = 'localhost'
    os.environ['MASTER_PORT'] = training_config.master_port
    os.environ['RANK'] = str(rank)
    os.environ['WORLD_SIZE'] = str(training_config.world_size)

    # 初始化进程组
    dist.init_process_group(
        backend=training_config.backend,
        rank=rank,
        world_size=training_config.world_size
    )

    if rank == 0:
        print(f"🌐 分布式训练环境初始化完成")
        print(f"   后端: {training_config.backend}")
        print(f"   进程数: {training_config.world_size}")
        print(f"   使用GPU: {training_config.gpu_ids}")
        print(f"   主节点: localhost:{training_config.master_port}")

def cleanup_distributed():
    """清理分布式训练环境"""
    if dist.is_initialized():
        dist.destroy_process_group()

def distributed_train_worker(rank, config):
    """分布式训练工作进程"""
    try:
        # 设置分布式环境
        setup_distributed(rank, config)

        training_config = config.training_config
        gpu_id = training_config.get_gpu_for_rank(rank)

        if rank == 0:
            print(f"🚀 启动分布式训练 - Rank {rank}/{training_config.world_size}")
            print(f"   使用GPU: {gpu_id}")
        
        # 创建物理量Keys
        with open(os.path.join(config.data_config.data_root, "meta.json"), "r") as f:
            dataset_statistics = json.load(f)["dataset_statistics"]
        input_keys = [
            Key(name="u_cpd_cell_init", dimension=1),
            Key(name="v_cpd_cell_init", dimension=1),
            Key(name="w_cpd_cell_init", dimension=1),
            Key(name="p_cpd_cell_init", dimension=1),
            Key(name="T_cpd_cell_init", dimension=1),
        ]
        output_keys = [
            Key(name="u_cpd_cell_pred", dimension=1),
            Key(name="v_cpd_cell_pred", dimension=1),
            Key(name="w_cpd_cell_pred", dimension=1),
            Key(name="p_cpd_cell_pred", dimension=1),
            Key(name="T_cpd_cell_pred", dimension=1),
        ]
        target_keys = [
            Key(
                name="u_cpd_cell_true", 
                dimension=1, 
                normalizer=StandardDeviationNormalizer(
                    mean=torch.tensor(dataset_statistics["mean"]["uvw_cpd_cell_u"], dtype=torch.float32),
                    std=torch.tensor(dataset_statistics["std"]["uvw_cpd_cell_u"], dtype=torch.float32),
                )
            ),
            Key(
                name="v_cpd_cell_true", 
                dimension=1, 
                normalizer=StandardDeviationNormalizer(
                    mean=torch.tensor(dataset_statistics["mean"]["uvw_cpd_cell_v"], dtype=torch.float32),
                    std=torch.tensor(dataset_statistics["std"]["uvw_cpd_cell_v"], dtype=torch.float32),
                )
            ),
            Key(
                name="w_cpd_cell_true", 
                dimension=1, 
                normalizer=StandardDeviationNormalizer(
                    mean=torch.tensor(dataset_statistics["mean"]["uvw_cpd_cell_w"], dtype=torch.float32),
                    std=torch.tensor(dataset_statistics["std"]["uvw_cpd_cell_w"], dtype=torch.float32),
                )
            ),
            Key(
                name="p_cpd_cell_true", 
                dimension=1, 
                normalizer=StandardDeviationNormalizer(
                    mean=torch.tensor(dataset_statistics["mean"]["press_cpd_cell"], dtype=torch.float32),
                    std=torch.tensor(dataset_statistics["std"]["press_cpd_cell"], dtype=torch.float32),
                )
            ),
            Key(
                name="T_cpd_cell_true", 
                dimension=1, 
                normalizer=StandardDeviationNormalizer(
                    mean=torch.tensor(dataset_statistics["mean"]["temp_cpd_cell"], dtype=torch.float32),
                    std=torch.tensor(dataset_statistics["std"]["temp_cpd_cell"], dtype=torch.float32),
                )
            )
        ]
        
        trainer = Trainer(
            config=config,
            input_keys=input_keys,
            output_keys=output_keys,
            target_keys=target_keys,
            distributed=True,
            gpu_ids=training_config.gpu_ids  # 从配置中获取GPU列表
        )
        
        if rank == 0:
            print(f"✅ Rank {rank}: 分布式Trainer创建成功")
            print(f"   模型: {type(trainer.simulator.model).__name__}")
            print(f"   设备: {trainer.device}")
            print(f"   实际GPU: {trainer.actual_gpu_id}")
            print(f"   参数数量: {sum(p.numel() for p in trainer.simulator.model.parameters()):,}")
        
        # 检查是否需要恢复训练
        checkpoint_path = config.training_config.checkpoint_path
        if rank == 0:
            if checkpoint_path:
                print(f"\n🔄 检测到checkpoint恢复配置")
                print(f"   Checkpoint路径: {checkpoint_path}")

                # 验证checkpoint路径是否存在
                from pathlib import Path
                if Path(checkpoint_path).exists():
                    print(f"✅ Checkpoint路径有效，将恢复训练")
                else:
                    print(f"⚠️  Checkpoint路径不存在，将开始新训练")
                    checkpoint_path = None
            else:
                print(f"\n🆕 开始新的训练")

        # 开始训练
        num_epochs = config.training_config.num_epochs
        if rank == 0:
            if checkpoint_path:
                print(f"\n🎯 恢复分布式训练 ({num_epochs} epochs)")
                print(f"   从checkpoint恢复: {checkpoint_path}")
            else:
                print(f"\n🎯 开始分布式训练 ({num_epochs} epochs)")
            print("=" * 60)

        # 启用最佳模型保存的训练，支持checkpoint恢复
        trainer.train(
            num_epochs=num_epochs,
            save_best=True,  # 启用最佳模型保存
            resume_from_checkpoint=checkpoint_path  # 使用config中的checkpoint_path
        )

        if rank == 0:
            print(f"\n🎉 分布式训练完成！")

            # 显示训练统计信息
            if checkpoint_path:
                print(f"   训练模式: 从checkpoint恢复")
                print(f"   恢复路径: {checkpoint_path}")
            else:
                print(f"   训练模式: 全新训练")

            print(f"   目标epochs: {num_epochs}")
            print(f"   当前epoch: {trainer.current_epoch}")
            print(f"   所有 {training_config.world_size} 个进程正常工作")

            # 显示最佳验证损失
            if trainer.best_val_loss != float('inf'):
                print(f"   最佳验证损失: {trainer.best_val_loss:.6f}")
            else:
                print(f"   最佳验证损失: 尚未记录 (从旧checkpoint恢复)")

            # 显示保存的模型路径
            if trainer.logger:
                best_model_path = f"{trainer.logger.saving_path}/states/best.state"
                print(f"   最佳模型保存在: {best_model_path}")
                print(f"   Logger目录: {trainer.logger.saving_path}")

                # 显示TensorBoard信息
                tensorboard_path = f"{trainer.logger.saving_path}/tensorboard"
                print(f"   TensorBoard日志: {tensorboard_path}")

                if checkpoint_path:
                    print(f"   📊 训练日志已连续记录到原有目录")
        
    except Exception as e:
        print(f"❌ Rank {rank} 训练失败: {e}")
        import traceback
        traceback.print_exc()
        raise
    finally:
        cleanup_distributed()

def main():
    """主函数 - 基于配置文件的分布式训练"""
    print("🚀 分布式训练启动脚本")
    print("=" * 60)

    # 加载配置文件
    config_path = 'configs/config.yaml'
    print(f"📁 加载配置文件: {config_path}")

    try:
        with open(config_path, 'r') as f:
            config_data = yaml.safe_load(f)
        config = Config.from_dict(config_data)
        print("✅ 配置加载成功")
    except Exception as e:
        print(f"❌ 配置文件加载失败: {e}")
        return

    # 检查分布式配置
    training_config = config.training_config
    if not training_config.distributed:
        print("❌ 分布式训练未启用，请在配置文件中设置 training_config.distributed = true")
        return

    # 检查CUDA可用性
    if not torch.cuda.is_available():
        print("❌ CUDA不可用，无法进行分布式训练")
        return

    # 验证GPU可用性
    num_gpus = torch.cuda.device_count()
    for gpu_id in training_config.gpu_ids:
        if gpu_id >= num_gpus:
            print(f"❌ 指定的GPU不可用")
            print(f"   系统GPU数量: {num_gpus}")
            print(f"   指定的GPU: {training_config.gpu_ids}")
            return

    print(f"📊 系统检测到 {num_gpus} 个GPU")
    print(f"🎯 使用GPU: {training_config.gpu_ids}")
    print(f"🔧 将启动 {training_config.world_size} 个进程进行分布式训练")
    print(f"🔄 训练轮数: {training_config.num_epochs}")
    print(f"📡 主节点端口: {training_config.master_port}")

    # 显示checkpoint恢复配置
    if training_config.checkpoint_path:
        print(f"🔄 Checkpoint恢复: {training_config.checkpoint_path}")

        # 验证checkpoint路径
        from pathlib import Path
        if Path(training_config.checkpoint_path).exists():
            print(f"✅ Checkpoint路径有效")
        else:
            print(f"⚠️  Checkpoint路径不存在，将开始新训练")
    else:
        print(f"🆕 模式: 全新训练")

    try:
        if training_config.world_size == 1:
            # 单GPU训练
            print("🔧 单GPU模式...")
            distributed_train_worker(0, config)
        else:
            # 多GPU分布式训练
            print(f"🔧 多GPU分布式模式 ({training_config.world_size} GPUs)...")
            mp.spawn(
                distributed_train_worker,
                args=(config,),
                nprocs=training_config.world_size,
                join=True
            )

        print("\n✅ 分布式训练成功完成！")

    except Exception as e:
        print(f"\n❌ 分布式训练失败: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
