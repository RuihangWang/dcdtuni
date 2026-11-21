#!/usr/bin/env python3
"""
Unified Scheduler Module

This module provides:
1. Custom learning rate schedulers (CustomExponentialLR, PolynomialDecayLR, InverseDecayLR, ProgressiveRestartCosineAnnealingLR)
2. Factory function to create schedulers
3. Support for both config-based and kwargs-based scheduler creation

Consolidates functionality from:
- src/config/scheduler.py (scheduler configurations)
- Original local_scheduler.py (custom scheduler implementations)
"""

import matplotlib
matplotlib.use('Agg')  # 为了在没有显示界面的环境下运行matplotlib
import math
import logging
from torch.optim.lr_scheduler import _LRScheduler
from typing import Dict, Any, Union, Optional

logger = logging.getLogger(__name__)

# =============================================================================
# Custom Scheduler Implementations
# =============================================================================

class CustomExponentialLR(_LRScheduler):
    """
    自定义指数衰减学习率调度器

    特点：
    1. 精确控制起始学习率和最小学习率
    2. 在指定的总步数后精确降至最小学习率
    3. 通过decay_power参数控制衰减曲线的"凹陷"程度

    数学公式：
    lr(t) = lr_min + (lr_start - lr_min) * exp(-decay_power * t / total_epochs)

    参数：
    - lr_start: 起始学习率
    - lr_min: 最小学习率
    - decay_power: 衰减强度 (>1更凹陷, <1更平缓, =1标准指数)
    - total_epochs: 总训练步数
    """

    def __init__(self, optimizer, lr_start, lr_min, decay_power, total_epochs, last_epoch=-1):
        self.lr_start = lr_start
        self.lr_min = lr_min
        self.decay_power = decay_power
        self.total_epochs = total_epochs

        # 设置初始学习率
        for group in optimizer.param_groups:
            group['lr'] = lr_start

        super(CustomExponentialLR, self).__init__(optimizer, last_epoch)

    def get_lr(self):
        """计算当前学习率"""
        if self.last_epoch == 0:
            return [self.lr_start for _ in self.optimizer.param_groups]

        # 计算当前进度 (0 到 1)
        progress = min(self.last_epoch / self.total_epochs, 1.0)

        # 指数衰减公式
        lr_current = self.lr_min + (self.lr_start - self.lr_min) * math.exp(-self.decay_power * progress)

        return [lr_current for _ in self.optimizer.param_groups]


class PolynomialDecayLR(_LRScheduler):
    """
    多项式衰减学习率调度器 - 凹向坐标原点的衰减曲线

    特点：
    1. 使用反向多项式函数进行学习率衰减，产生"凹向坐标原点"的曲线
    2. 支持不同的凹陷程度控制（concavity参数）
    3. 在指定的总步数后精确降至最小学习率
    4. 当concavity=1时，曲线变为线性衰减 y = -x + 1
    5. concavity > 1时，曲线更加凹陷（类似1/x形状）
    6. concavity < 1时，曲线较为平缓

    数学公式：
    progress = t / total_epochs  (0 到 1)
    decay_factor = 1 - (1 - progress)^(1/concavity)  (凹向原点的衰减因子)
    lr(t) = lr_min + (lr_start - lr_min) * (1 - decay_factor)

    参数：
    - lr_start: 起始学习率
    - lr_min: 最小学习率
    - concavity: 凹陷程度控制参数 (默认2.0)
      * concavity = 1.0: 线性衰减 y = -x + 1
      * concavity > 1.0: 更凹陷，类似 1/x 形状
      * concavity < 1.0: 较平缓的凹陷
    - total_epochs: 总训练步数
    """

    def __init__(self, optimizer, lr_start, lr_min, concavity, total_epochs, last_epoch=-1):
        self.lr_start = lr_start
        self.lr_min = lr_min
        self.concavity = concavity
        self.total_epochs = total_epochs

        # 设置初始学习率
        for group in optimizer.param_groups:
            group['lr'] = lr_start

        super(PolynomialDecayLR, self).__init__(optimizer, last_epoch)

    def get_lr(self):
        """计算当前学习率"""
        if self.last_epoch == 0:
            return [self.lr_start for _ in self.optimizer.param_groups]

        # 计算当前进度 (0 到 1)
        progress = min(self.last_epoch / self.total_epochs, 1.0)

        # 凹向坐标原点的多项式衰减公式
        # 使用 1 - (1-progress)^(1/concavity) 来产生凹向原点的曲线
        if self.concavity == 1.0:
            # 当concavity=1时，直接使用线性衰减避免数值问题
            decay_factor = progress
        else:
            decay_factor = 1 - ((1 - progress) ** (1.0 / self.concavity))

        lr_current = self.lr_min + (self.lr_start - self.lr_min) * (1 - decay_factor)

        return [lr_current for _ in self.optimizer.param_groups]


class InverseDecayLR(_LRScheduler):
    """
    反比例衰减学习率调度器 - 基于 y = k * (1/x) 的衰减曲线

    特点：
    1. 使用反比例函数进行学习率衰减，产生类似 1/x 的"凹向坐标原点"曲线
    2. 支持凹陷倍率控制（k参数）
    3. 当k=0时，退化为线性衰减 y = lr_init - step_factor * x
    4. k > 0时，产生反比例衰减曲线，k越大凹陷程度越强

    数学公式：
    当 k > 0 时：
        lr(t) = lr_min + k * (total_epochs / (t + 1))
    当 k = 0 时：
        lr(t) = lr_init - step_factor * t
        其中 step_factor = (lr_init - lr_min) / total_epochs

    参数：
    - lr_init: 初始学习率
    - lr_min: 最小学习率
    - k: 凹陷倍率控制参数 (默认1.0)
      * k = 0.0: 线性衰减 y = lr_init - x
      * k > 0.0: 反比例衰减，k越大凹陷程度越强
    - total_epochs: 总训练步数
    """

    def __init__(self, optimizer, lr_init, lr_min, k, total_epochs, last_epoch=-1):
        self.lr_init = lr_init
        self.lr_min = lr_min
        self.k = k
        self.total_epochs = total_epochs

        # 计算线性衰减的步长因子（当k=0时使用）
        self.step_factor = (lr_init - lr_min) / total_epochs if total_epochs > 0 else 0

        # 设置初始学习率
        for group in optimizer.param_groups:
            group['lr'] = lr_init

        super(InverseDecayLR, self).__init__(optimizer, last_epoch)

    def get_lr(self):
        """计算当前学习率"""
        if self.last_epoch == 0:
            return [self.lr_init for _ in self.optimizer.param_groups]

        current_step = self.last_epoch

        if self.k == 0.0:
            # 线性衰减：y = lr_init - step_factor * x
            lr_current = max(self.lr_init - self.step_factor * current_step, self.lr_min)
        else:
            # 反比例衰减：y = lr_min + k * (total_epochs / (x + 1))
            # 这里使用 (current_step + 1) 避免除零错误
            lr_current = self.lr_min + self.k * (self.total_epochs / (current_step + 1))
            # 确保不超过初始学习率和不低于最小学习率
            lr_current = max(min(lr_current, self.lr_init), self.lr_min)

        return [lr_current for _ in self.optimizer.param_groups]


class ProgressiveRestartCosineAnnealingLR:
    """
    渐进式重启余弦退火学习率调度器

    每次重启时，最大学习率都会按指定因子递减，避免后期无意义震荡。

    参数配置：
    - window_size: 重启窗口大小
    - total_windows: 重启窗口数量
    - initial_max_lr: 初始最大学习率
    - decay_factor: 重启衰减因子
    - min_restart_lr: 最低重启学习率
    - eta_min: 周期内最小学习率
    """

    def __init__(self, optimizer, window_size, total_windows, initial_max_lr,
                 decay_factor, min_restart_lr, eta_min):
        self.optimizer = optimizer
        self.window_size = window_size
        self.total_windows = total_windows
        self.initial_max_lr = initial_max_lr
        self.decay_factor = decay_factor
        self.min_restart_lr = min_restart_lr
        self.eta_min = eta_min

        self.current_step = 0
        self.current_window = 0

        # 设置初始学习率
        for param_group in self.optimizer.param_groups:
            param_group['lr'] = self.initial_max_lr

    def get_current_max_lr(self):
        """计算当前窗口的最大学习率"""
        current_max = self.initial_max_lr * (self.decay_factor ** self.current_window)
        return max(current_max, self.min_restart_lr)

    def step(self):
        """更新学习率"""
        step_in_window = self.current_step % self.window_size

        # 检查是否需要重启到新窗口
        if step_in_window == 0 and self.current_step > 0:
            self.current_window += 1

        # 计算当前窗口的最大学习率
        current_max_lr = self.get_current_max_lr()

        # 在当前窗口内使用余弦退火
        progress = step_in_window / self.window_size
        lr = self.eta_min + (current_max_lr - self.eta_min) * (1 + math.cos(math.pi * progress)) / 2

        # 更新优化器学习率
        for param_group in self.optimizer.param_groups:
            param_group['lr'] = lr

        self.current_step += 1

    def get_last_lr(self):
        """获取最后一次的学习率"""
        return [param_group['lr'] for param_group in self.optimizer.param_groups]


# =============================================================================
# Unified Scheduler Factory Function
# =============================================================================

def create_scheduler(
    scheduler_type: str,
    optimizer,
    config: Optional[Union[Dict[str, Any], object]] = None,
    **kwargs
):
    """
    Unified factory function to create learning rate schedulers.

    Supports both dictionary-based and config object-based parameter passing.

    Args:
        scheduler_type: Scheduler type (e.g., 'StepLR', 'CosineAnnealingLR', 'ReduceLROnPlateau', etc.)
        optimizer: Optimizer instance
        config: Configuration dict or object containing scheduler parameters
        **kwargs: Override parameters (highest priority)

    Returns:
        Configured scheduler instance, or None if use_lr_scheduler=False

    Examples:
        # Using dictionary config
        scheduler = create_scheduler('CosineAnnealingLR', optimizer,
                                    config={'T_max': 10000, 'eta_min': 1e-6})

        # Using config object
        scheduler = create_scheduler('CosineAnnealingLR', optimizer, config=scheduler_config)

        # Using kwargs override
        scheduler = create_scheduler('StepLR', optimizer, step_size=1000, gamma=0.5)
    """
    from torch.optim.lr_scheduler import (
        ReduceLROnPlateau, StepLR, CosineAnnealingLR,
        CosineAnnealingWarmRestarts, ExponentialLR
    )

    # Check if we should use scheduler
    def get_param(name, default=None, config_names=None):
        """Get parameter with priority: kwargs > config > default"""
        if name in kwargs:
            return kwargs[name]

        if config is not None:
            # Try config_names first (alternative names in config)
            if config_names:
                for config_name in (config_names if isinstance(config_names, list) else [config_names]):
                    if isinstance(config, dict):
                        if config_name in config:
                            return config[config_name]
                    else:
                        if hasattr(config, config_name):
                            return getattr(config, config_name)

            # Try original name
            if isinstance(config, dict):
                if name in config:
                    return config[name]
            else:
                if hasattr(config, name):
                    return getattr(config, name)

        return default

    use_lr_scheduler = get_param('use_lr_scheduler', True)
    if not use_lr_scheduler:
        logger.info("❌ 不使用学习率调度器")
        return None

    scheduler_type = scheduler_type.lower()

    # Create scheduler based on type
    if scheduler_type == "plateau":
        factor = get_param('factor', 0.5, ['lr_factor'])
        patience = get_param('patience', 500, ['lr_patience'])
        threshold = get_param('threshold', 1e-6, ['lr_threshold'])
        threshold_mode = get_param('threshold_mode', 'rel')
        min_lr = get_param('min_lr', 1e-6, ['lr_min_lr'])

        scheduler = ReduceLROnPlateau(
            optimizer,
            mode='min',
            factor=factor,
            patience=patience,
            threshold=threshold,
            threshold_mode=threshold_mode,
            min_lr=min_lr
        )
        logger.info("✅ 使用ReduceLROnPlateau学习率调度器")
        logger.info("   - 监视指标: 损失")
        logger.info("   - 扫描窗口: %s 步", patience)
        logger.info("   - 衰减因子: %s", factor)
        logger.info("   - 最小学习率: %s", min_lr)

    elif scheduler_type == "step" or scheduler_type == "steplr":
        step_size = get_param('step_size', 1000, ['lr_step_size'])
        gamma = get_param('gamma', 0.5, ['lr_factor'])

        scheduler = StepLR(
            optimizer,
            step_size=step_size,
            gamma=gamma
        )
        logger.info("✅ 使用StepLR学习率调度器")
        logger.info("   - 步长: %s", step_size)
        logger.info("   - 衰减因子: %s", gamma)

    elif scheduler_type == "cosine" or scheduler_type == "cosineannealinglr":
        T_max = get_param('T_max', 10000, ['lr_T_max'])
        eta_min = get_param('eta_min', 1e-5, ['lr_min_lr'])

        scheduler = CosineAnnealingLR(
            optimizer,
            T_max=T_max,
            eta_min=eta_min
        )
        logger.info("✅ 使用CosineAnnealingLR余弦退火学习率调度器")
        logger.info("   - 初始学习率: %s", optimizer.param_groups[0]['lr'])
        logger.info("   - 最终学习率: %s", eta_min)
        logger.info("   - 总迭代步数: %s", T_max)

    elif scheduler_type == "cosine_restart" or scheduler_type == "cosineannealingwarmrestarts":
        T_0 = get_param('T_0', 2000, ['lr_T_0'])
        T_mult = get_param('T_mult', 1, ['lr_T_mult'])
        eta_min = get_param('eta_min', 5e-5, ['lr_min_lr'])
        eta_max = get_param('eta_max', 1e-2, ['lr_max_lr'])

        # 先将优化器的学习率设置为我们想要的最大学习率
        for param_group in optimizer.param_groups:
            param_group['lr'] = eta_max

        scheduler = CosineAnnealingWarmRestarts(
            optimizer,
            T_0=T_0,
            T_mult=T_mult,
            eta_min=eta_min
        )
        logger.info("✅ 使用CosineAnnealingWarmRestarts余弦重启学习率调度器")
        logger.info("   - 重启最大学习率: %s", eta_max)
        logger.info("   - 最小学习率: %s", eta_min)
        logger.info("   - 第一个周期长度: %s 步", T_0)
        logger.info("   - 周期倍增因子: %s", T_mult)

    elif scheduler_type == "progressive_restart":
        window_size = get_param('window_size', 2000, ['restart_window_size'])
        window_multiplier = get_param('window_multiplier', 20, ['restart_window_multiplier'])
        initial_max_lr = get_param('initial_max_lr', 1.0)
        decay_factor = get_param('decay_factor', 0.5, ['restart_decay_factor'])
        min_restart_lr = get_param('min_restart_lr', 1e-4)
        eta_min = get_param('eta_min', 5e-5, ['lr_min_lr'])

        scheduler = ProgressiveRestartCosineAnnealingLR(
            optimizer=optimizer,
            window_size=window_size,
            total_windows=window_multiplier,
            initial_max_lr=initial_max_lr,
            decay_factor=decay_factor,
            min_restart_lr=min_restart_lr,
            eta_min=eta_min
        )
        logger.info("✅ 使用ProgressiveRestartCosineAnnealingLR渐进式重启余弦退火调度器")
        logger.info("   - 重启窗口大小: %s 步", window_size)
        logger.info("   - 重启窗口数量: %s 个", window_multiplier)
        logger.info("   - 初始最大学习率: %s", initial_max_lr)
        logger.info("   - 重启衰减因子: %s", decay_factor)

    elif scheduler_type == "exponential" or scheduler_type == "exponentiallr":
        gamma = get_param('gamma', 0.999, ['lr_gamma'])

        scheduler = ExponentialLR(
            optimizer,
            gamma=gamma
        )
        logger.info("✅ 使用ExponentialLR学习率调度器")
        logger.info("   - 衰减因子: %s", gamma)

    elif scheduler_type == "custom_exponential":
        lr_start = get_param('lr_start', 1.0)
        lr_min = get_param('lr_min', 1e-5)
        decay_power = get_param('decay_power', 2.0)
        n_epochs = get_param('n_epochs', 10000, ['total_epochs'])

        scheduler = CustomExponentialLR(
            optimizer,
            lr_start=lr_start,
            lr_min=lr_min,
            decay_power=decay_power,
            total_epochs=n_epochs
        )
        logger.info("✅ 使用CustomExponentialLR学习率调度器")
        logger.info("   - 起始学习率: %s", lr_start)
        logger.info("   - 最小学习率: %s", lr_min)
        logger.info("   - 衰减强度: %s", decay_power)
        logger.info("   - 总步数: %s", n_epochs)

    elif scheduler_type == "polynomial_decay":
        lr_start = get_param('lr_start', 1.0)
        lr_min = get_param('lr_min', 1e-5)
        concavity = get_param('concavity', 2.0)
        n_epochs = get_param('n_epochs', 10000, ['total_epochs'])

        scheduler = PolynomialDecayLR(
            optimizer,
            lr_start=lr_start,
            lr_min=lr_min,
            concavity=concavity,
            total_epochs=n_epochs
        )
        logger.info("✅ 使用PolynomialDecayLR学习率调度器")
        logger.info("   - 起始学习率: %s", lr_start)
        logger.info("   - 最小学习率: %s", lr_min)
        logger.info("   - 凹陷程度: %s", concavity)
        logger.info("   - 总步数: %s", n_epochs)

    elif scheduler_type == "inverse_decay":
        lr_init = get_param('lr_init', 1.0)
        lr_min = get_param('lr_min', 1e-5)
        k = get_param('k', 1.0)
        n_epochs = get_param('n_epochs', 10000, ['total_epochs'])

        scheduler = InverseDecayLR(
            optimizer,
            lr_init=lr_init,
            lr_min=lr_min,
            k=k,
            total_epochs=n_epochs
        )
        logger.info("✅ 使用InverseDecayLR学习率调度器")
        logger.info("   - 初始学习率: %s", lr_init)
        logger.info("   - 最小学习率: %s", lr_min)
        logger.info("   - 凹陷倍率: %s", k)
        logger.info("   - 总步数: %s", n_epochs)

    else:
        supported_schedulers = [
            "plateau", "step", "steplr", "cosine", "cosineannealinglr",
            "cosine_restart", "cosineannealingwarmrestarts", "progressive_restart",
            "exponential", "exponentiallr", "custom_exponential",
            "polynomial_decay", "inverse_decay"
        ]
        raise ValueError(f"不支持的调度器类型: {scheduler_type}. 支持的类型: {supported_schedulers}")

    return scheduler


# Backward compatibility alias
get_scheduler = create_scheduler


# =============================================================================
# Visualization Utilities
# =============================================================================

def plot_learning_rate_curves(total_steps=10000, save_path=None):
    """
    绘制所有学习率调度器的曲线对比图

    Args:
        total_steps (int): 总训练步数，默认10000
        save_path (str): 保存路径，如果为None则显示图像
    """
    import torch
    import torch.nn as nn
    import matplotlib.pyplot as plt

    # 创建一个简单的模型和优化器用于测试
    model = nn.Linear(10, 1)
    lr_init = 1.0

    # 存储所有调度器的学习率曲线
    lr_curves = {}
    steps = list(range(total_steps))

    # Test different schedulers
    logger.info("📊 绘制学习率调度器曲线...")

    # 1. StepLR
    optimizer = torch.optim.Adam(model.parameters(), lr=lr_init)
    scheduler = create_scheduler("step", optimizer, step_size=1000, gamma=0.5)
    step_lrs = []
    for step in range(total_steps):
        step_lrs.append(scheduler.get_last_lr()[0])
        scheduler.step()
    lr_curves['StepLR'] = step_lrs

    # 2. CosineAnnealingLR
    optimizer = torch.optim.Adam(model.parameters(), lr=lr_init)
    scheduler = create_scheduler("cosine", optimizer, T_max=total_steps, eta_min=1e-5)
    cosine_lrs = []
    for step in range(total_steps):
        cosine_lrs.append(scheduler.get_last_lr()[0])
        scheduler.step()
    lr_curves['CosineAnnealingLR'] = cosine_lrs

    # 3. CustomExponentialLR
    optimizer = torch.optim.Adam(model.parameters(), lr=lr_init)
    scheduler = create_scheduler("custom_exponential", optimizer,
                                lr_start=lr_init, lr_min=1e-5, decay_power=15.0, n_epochs=total_steps)
    custom_exp_lrs = []
    for step in range(total_steps):
        custom_exp_lrs.append(scheduler.get_last_lr()[0])
        scheduler.step()
    lr_curves['CustomExponentialLR'] = custom_exp_lrs

    # 4. PolynomialDecayLR
    optimizer = torch.optim.Adam(model.parameters(), lr=lr_init)
    scheduler = create_scheduler("polynomial_decay", optimizer,
                                lr_start=lr_init, lr_min=1e-5, concavity=0.5, n_epochs=total_steps)
    poly_lrs = []
    for step in range(total_steps):
        poly_lrs.append(scheduler.get_last_lr()[0])
        scheduler.step()
    lr_curves['PolynomialDecayLR'] = poly_lrs

    # Plot all curves
    fig, axes = plt.subplots(2, 2, figsize=(12, 10))
    axes = axes.flatten()

    colors = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728']

    for i, (name, lrs) in enumerate(lr_curves.items()):
        ax = axes[i]
        ax.plot(steps, lrs, color=colors[i], linewidth=2)
        ax.set_title(name, fontsize=12, fontweight='bold')
        ax.set_xlabel('Training Steps', fontsize=10)
        ax.set_ylabel('Learning Rate', fontsize=10)
        ax.grid(True, alpha=0.3)
        ax.set_yscale('log')

    plt.tight_layout()

    if save_path:
        plt.savefig(save_path, dpi=300, bbox_inches='tight')
        logger.info("✅ 学习率曲线图已保存到: %s", save_path)
    else:
        plt.show()

    return lr_curves


__all__ = [
    # Custom schedulers
    'CustomExponentialLR',
    'PolynomialDecayLR',
    'InverseDecayLR',
    'ProgressiveRestartCosineAnnealingLR',

    # Factory function
    'create_scheduler',
    'get_scheduler',  # Backward compatibility

    # Visualization
    'plot_learning_rate_curves',
]
