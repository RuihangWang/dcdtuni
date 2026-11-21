#!/usr/bin/env python3
"""
Unified Optimizer Module

This module provides:
1. SOAP optimizer implementation (second-order optimization with preconditioning)
2. Factory function to create optimizers (Adam, AdamW, SGD, SOAP)
3. PyTorch optimization settings (Triton, CUDA Graphs, torch.compile, etc.)

Consolidates functionality from:
- src/config/optimizer.py (optimizer configurations)
- src/Utils/torch_optimization.py (PyTorch optimization settings)
- Original local_optimizer.py (SOAP implementation)
"""

import os
import logging
import torch
import torch.optim as optim
from itertools import chain
from typing import Dict, Any, Union, Optional

logger = logging.getLogger(__name__)


# =============================================================================
# PyTorch Optimization Settings
# =============================================================================

def disable_triton_autotune():
    """
    Disable Triton autotune to speed up training startup.

    Triton autotune can take 15-50 seconds per kernel to find optimal configurations.
    For development and testing, it's often better to disable it.
    """
    os.environ['TRITON_DISABLE_AUTOTUNE'] = '1'
    os.environ['TRITON_CACHE_DIR'] = '/tmp/triton_cache'
    logger.info("🚀 Triton autotune disabled for faster startup")


def configure_triton_for_shared_memory():
    """
    Configure Triton to work within shared memory limitations.

    This helps avoid "out of resource: shared memory" errors by:
    - Reducing block sizes
    - Limiting pipeline stages
    - Using conservative memory settings
    """
    os.environ['TRITON_MAX_BLOCK_SIZE'] = '512'  # Conservative block size
    os.environ['TRITON_DEFAULT_NUM_STAGES'] = '1'  # Minimal pipeline stages
    os.environ['TRITON_DISABLE_AUTOTUNE'] = '1'  # Avoid resource-intensive autotune
    os.environ['TRITON_CACHE_DIR'] = '/tmp/triton_cache'

    # Additional conservative settings
    os.environ['TRITON_MAX_SHARED_MEMORY'] = '49152'  # Half of hardware limit (98304/2)

    logger.info("🚀 Triton configured for shared memory limitations")


def disable_torch_compile():
    """
    Disable torch.compile to avoid compilation overhead and CUDA Graphs issues.

    torch.compile can provide performance benefits but adds compilation time
    and may cause CUDA Graphs tensor overwrite errors in some cases.
    """
    if hasattr(torch, '_dynamo'):
        torch._dynamo.config.suppress_errors = True
        torch._dynamo.config.disable = True
        logger.info("🚀 torch.compile disabled")


def disable_cuda_graphs():
    """
    Disable CUDA Graphs to avoid tensor overwrite errors.

    CUDA Graphs can cause "accessing tensor output that has been overwritten"
    errors when used with certain operations or torch.compile.
    """
    os.environ['TORCH_CUDAGRAPHS_DISABLE'] = '1'
    if hasattr(torch, '_dynamo'):
        torch._dynamo.config.capture_scalar_outputs = False
        torch._dynamo.config.capture_dynamic_output_shape_ops = False
    logger.info("🚀 CUDA Graphs disabled")


def enable_torch_compile_for_production():
    """
    Enable torch.compile with production-optimized settings.

    This configuration enables torch.compile while avoiding common issues:
    - Disables CUDA Graphs to prevent tensor overwrite errors
    - Uses memory-efficient compilation modes
    - Optimizes for large model performance
    - Handles shared memory limitations
    """
    if hasattr(torch, '_dynamo'):
        # Enable torch.compile
        torch._dynamo.config.disable = False
        torch._dynamo.config.suppress_errors = False

        # Configure for memory efficiency and stability
        torch._dynamo.config.cache_size_limit = 128  # Increase cache for better performance

        # Disable problematic features that can cause CUDA Graphs issues
        torch._dynamo.config.capture_scalar_outputs = False
        torch._dynamo.config.capture_dynamic_output_shape_ops = False

        # Memory optimization settings (only set if they exist)
        if hasattr(torch._dynamo.config, 'optimize_ddp'):
            torch._dynamo.config.optimize_ddp = False  # Avoid DDP compilation issues

        # Additional memory optimizations for large models
        if hasattr(torch._dynamo.config, 'automatic_dynamic_shapes'):
            torch._dynamo.config.automatic_dynamic_shapes = False  # More predictable memory usage

        logger.info("🚀 torch.compile enabled with production settings")

    # Set environment variables for better memory management and shared memory limits
    os.environ['TORCHINDUCTOR_CACHE_DIR'] = '/tmp/torchinductor_cache'

    # Configure Triton to handle shared memory limitations
    os.environ['TRITON_MAX_BLOCK_SIZE'] = '1024'  # Reduce block size to fit shared memory
    os.environ['TRITON_DEFAULT_NUM_STAGES'] = '2'  # Reduce pipeline stages
    os.environ['TRITON_DISABLE_AUTOTUNE'] = '1'  # Disable autotune to avoid resource conflicts

    # Still disable CUDA Graphs explicitly to avoid conflicts
    disable_cuda_graphs()


def enable_fast_math():
    """
    Enable fast math operations for better performance.

    This may reduce numerical precision slightly but can improve speed.
    """
    torch.backends.cuda.matmul.allow_tf32 = True
    torch.backends.cudnn.allow_tf32 = True
    logger.info("🚀 Fast math enabled (TF32)")


def set_memory_efficient():
    """
    Configure PyTorch for memory efficiency.
    """
    torch.backends.cudnn.benchmark = True
    torch.backends.cudnn.deterministic = False
    logger.info("🚀 Memory efficient settings enabled")


def set_aggressive_memory_optimization():
    """
    Configure PyTorch for aggressive memory optimization.

    This includes additional memory-saving techniques for large models.
    """
    # Basic memory efficiency
    set_memory_efficient()

    # Additional memory optimizations
    torch.backends.cuda.matmul.allow_tf32 = True
    torch.backends.cudnn.allow_tf32 = True

    # Enable memory pool optimizations
    if hasattr(torch.cuda, 'memory'):
        # Set memory fraction to use most of GPU memory
        torch.cuda.set_per_process_memory_fraction(0.95)

    # Configure garbage collection for better memory management
    import gc
    gc.collect()
    torch.cuda.empty_cache()

    logger.info("🚀 Aggressive memory optimization enabled")


def configure_for_development():
    """
    Configure PyTorch for development (fast startup, less optimization).

    This configuration prioritizes:
    - Fast startup time
    - Reduced compilation overhead
    - Easier debugging
    - Stable execution
    """
    logger.info("🔧 Configuring PyTorch for development...")

    disable_triton_autotune()
    disable_torch_compile()
    disable_cuda_graphs()
    set_memory_efficient()

    logger.info("✅ Development configuration applied")


def configure_for_production():
    """
    Configure PyTorch for production (maximum performance).

    This configuration prioritizes:
    - Maximum runtime performance
    - Optimized kernels
    - Fast math operations
    - Memory efficiency with torch.compile
    """
    logger.info("🔧 Configuring PyTorch for production...")

    # Keep autotune enabled for production
    enable_fast_math()
    set_memory_efficient()

    # Enable torch.compile for better memory efficiency and performance
    enable_torch_compile_for_production()

    logger.info("✅ Production configuration applied")


def configure_for_production_high_memory():
    """
    Configure PyTorch for production with aggressive memory optimization.

    This configuration prioritizes:
    - Maximum memory efficiency
    - torch.compile for memory optimization
    - Aggressive memory management
    - Shared memory limitation handling
    - Suitable for large models with limited GPU memory
    """
    logger.info("🔧 Configuring PyTorch for production (high memory efficiency)...")

    # Configure Triton for shared memory limitations first
    configure_triton_for_shared_memory()

    # Aggressive memory optimizations
    set_aggressive_memory_optimization()

    # Enable torch.compile for memory efficiency
    enable_torch_compile_for_production()

    logger.info("✅ Production high-memory configuration applied")


def configure_for_inference():
    """
    Configure PyTorch for inference (balanced performance and startup).

    This configuration provides:
    - Reasonable startup time
    - Good inference performance
    - Memory efficiency
    """
    logger.info("🔧 Configuring PyTorch for inference...")

    disable_triton_autotune()  # Faster startup
    enable_fast_math()         # Better performance
    set_memory_efficient()     # Memory efficiency

    logger.info("✅ Inference configuration applied")


def print_current_config():
    """Print current PyTorch configuration"""
    logger.info("📊 Current PyTorch Configuration:")
    logger.info("   • CUDA available: %s", torch.cuda.is_available())
    if torch.cuda.is_available():
        logger.info("   • CUDA device count: %s", torch.cuda.device_count())
        logger.info("   • Current device: %s", torch.cuda.current_device())

    logger.info("   • CuDNN benchmark: %s", torch.backends.cudnn.benchmark)
    logger.info("   • CuDNN deterministic: %s", torch.backends.cudnn.deterministic)
    logger.info("   • TF32 matmul: %s", torch.backends.cuda.matmul.allow_tf32)
    logger.info("   • TF32 cudnn: %s", torch.backends.cudnn.allow_tf32)

    # Check environment variables
    triton_autotune = os.environ.get('TRITON_DISABLE_AUTOTUNE', '0')
    cuda_graphs = os.environ.get('TORCH_CUDAGRAPHS_DISABLE', '0')
    logger.info("   • Triton autotune disabled: %s", triton_autotune == '1')
    logger.info("   • CUDA Graphs disabled: %s", cuda_graphs == '1')

    if hasattr(torch, '_dynamo'):
        logger.info("   • torch.compile disabled: %s", torch._dynamo.config.disable)


def setup_torch(mode='development'):
    """
    Setup PyTorch with predefined configurations.

    Args:
        mode: Configuration mode ('development', 'production', 'production_high_memory', 'inference')
    """
    if mode == 'development':
        configure_for_development()
    elif mode == 'production':
        configure_for_production()
    elif mode == 'production_high_memory':
        configure_for_production_high_memory()
    elif mode == 'inference':
        configure_for_inference()
    else:
        raise ValueError(f"Unknown mode: {mode}. Use 'development', 'production', 'production_high_memory', or 'inference'")

    print_current_config()


# =============================================================================
# SOAP Optimizer Implementation
# =============================================================================

class SOAP(optim.Optimizer):
    """
    Implements SOAP algorithm (https://arxiv.org/abs/2409.11321).

    Second-order optimization with preconditioning for improved convergence.

    Parameters:
        params: Iterable of parameters to optimize or dictionaries defining parameter groups.
        lr: The learning rate to use (default: 0.003).
        betas: Adam's betas parameters (b1, b2) (default: (0.95, 0.95)).
        shampoo_beta: If >= 0, use this beta for the preconditioner moving average instead of betas[1] (default: -1).
        eps: Adam's epsilon for numerical stability (default: 1e-08).
        weight_decay: Weight decay coefficient (default: 0.01).
        precondition_frequency: How often to update the preconditioner (default: 10).
        max_precond_dim: Maximum dimension of the preconditioner (default: 10000).
        merge_dims: Whether or not to merge dimensions of the preconditioner (default: False).
        precondition_1d: Whether or not to precondition 1D gradients (default: False).
        normalize_grads: Whether or not to normalize gradients per layer (default: False).
        data_format: Data format of the input for convolutional layers (default: "channels_first").
        correct_bias: Whether or not to use bias correction in Adam (default: True).
    """

    def __init__(
        self,
        params,
        lr: float = 3e-3,
        betas=(0.95, 0.95),
        shampoo_beta: float= -1,
        eps: float = 1e-8,
        weight_decay: float = 0.01,
        precondition_frequency: int=10,
        max_precond_dim: int=10000,
        merge_dims: bool = False,
        precondition_1d: bool = False,
        normalize_grads: bool = False,
        data_format: str = "channels_first",
        correct_bias: bool = True,
    ):
        defaults = {
            "lr": lr,
            "betas": betas,
            "shampoo_beta": shampoo_beta,
            "eps": eps,
            "weight_decay": weight_decay,
            "precondition_frequency": precondition_frequency,
            "max_precond_dim": max_precond_dim,
            "merge_dims": merge_dims,
            "precondition_1d": precondition_1d,
            "normalize_grads": normalize_grads,
            "correct_bias": correct_bias,
        }
        super().__init__(params, defaults)
        self._data_format = data_format

    def merge_dims(self, grad, max_precond_dim):
        """
        Merges dimensions of the gradient tensor till the product of the dimensions is less than or equal to max_precond_dim.
        """
        assert self._data_format in ["channels_first", "channels_last"]
        if self._data_format == "channels_last" and grad.dim() == 4:
            grad = grad.permute(0, 3, 1, 2)

        shape = grad.shape
        new_shape = []
        curr_shape = 1
        for sh in shape:
            temp_shape = curr_shape * sh
            if temp_shape > max_precond_dim:
                if curr_shape > 1:
                    new_shape.append(curr_shape)
                    curr_shape = sh
                else:
                    new_shape.append(sh)
                    curr_shape = 1
            else:
                curr_shape = temp_shape

        if curr_shape > 1 or len(new_shape)==0:
            new_shape.append(curr_shape)

        new_grad = grad.reshape(new_shape)
        return new_grad

    @torch.no_grad()
    def step(self, closure = None):
        """
        Performs a single optimization step.

        Arguments:
            closure: A closure that reevaluates the model and returns the loss.
        """
        if closure is None:
            loss = None
        else:
            loss = closure()

        for group in self.param_groups:
            for p in group["params"]:
                if p.grad is None:
                    continue

                grad = p.grad
                state = self.state[p]

                if "step" not in state:
                    state["step"] = 0

                # State initialization
                if "exp_avg" not in state:
                    # Exponential moving average of gradient values
                    state["exp_avg"] = torch.zeros_like(grad)
                    # Exponential moving average of squared gradient values
                    state["exp_avg_sq"] = torch.zeros_like(grad)

                if 'Q' not in state:
                    self.init_preconditioner(
                        grad, state,
                        precondition_frequency=group['precondition_frequency'],
                        precondition_1d=group['precondition_1d'],
                        shampoo_beta=(group['shampoo_beta'] if group['shampoo_beta'] >= 0 else group["betas"][1]),
                        max_precond_dim=group['max_precond_dim'],
                        merge_dims=group["merge_dims"],
                    )
                    self.update_preconditioner(grad, state, max_precond_dim=group['max_precond_dim'], merge_dims=group["merge_dims"], precondition_1d=group["precondition_1d"])
                    continue  # first step is skipped so that we never use the current gradients in the projection.

                # Projecting gradients to the eigenbases of Shampoo's preconditioner
                grad_projected = self.project(grad, state, merge_dims=group["merge_dims"], max_precond_dim=group['max_precond_dim'])

                exp_avg, exp_avg_sq = state["exp_avg"], state["exp_avg_sq"]
                beta1, beta2 = group["betas"]
                state["step"] += 1

                # Decay the first and second moment running average coefficient
                exp_avg.mul_(beta1).add_(grad_projected, alpha=(1.0 - beta1))
                exp_avg_sq.mul_(beta2).add_(grad_projected.square(), alpha=(1.0 - beta2))
                denom = exp_avg_sq.sqrt().add_(group["eps"])

                exp_avg_projected = exp_avg

                step_size = group["lr"]
                if group["correct_bias"]:
                    bias_correction1 = 1.0 - beta1 ** (state["step"])
                    bias_correction2 = 1.0 - beta2 ** (state["step"])
                    step_size = step_size * (bias_correction2 ** .5) / bias_correction1

                # Projecting back the preconditioned exponential moving average of gradients
                norm_grad = self.project_back(exp_avg_projected / denom, state, merge_dims=group["merge_dims"], max_precond_dim=group['max_precond_dim'])

                if group["normalize_grads"]:
                    norm_grad = norm_grad / (1e-30+torch.mean(norm_grad**2)**0.5)

                p.add_(norm_grad, alpha=-step_size)

                # Add weight decay at the end (fixed version)
                if group["weight_decay"] > 0.0:
                    p.add_(p, alpha=(-group["lr"] * group["weight_decay"]))

                # Update preconditioner after the gradient step
                self.update_preconditioner(grad, state, max_precond_dim=group['max_precond_dim'], merge_dims=group["merge_dims"], precondition_1d=group["precondition_1d"])

        return loss

    def init_preconditioner(self, grad, state, precondition_frequency=10, shampoo_beta=0.95, max_precond_dim=10000, precondition_1d=False, merge_dims=False):
        """
        Initializes the preconditioner matrices (L and R in the paper).
        """
        state['GG'] = []  # Will hold all the preconditioner matrices
        if grad.dim() == 1:
            if not precondition_1d or grad.shape[0] > max_precond_dim:
                state['GG'].append([])
            else:
                state['GG'].append(torch.zeros(grad.shape[0], grad.shape[0], device=grad.device))
        else:
            if merge_dims:
                grad = self.merge_dims(grad, max_precond_dim)
            for sh in grad.shape:
                if sh > max_precond_dim:
                    state['GG'].append([])
                else:
                    state['GG'].append(torch.zeros(sh, sh, device=grad.device))

        state['Q'] = None  # Will hold all the eigenbases of the preconditioner
        state['precondition_frequency'] = precondition_frequency
        state['shampoo_beta'] = shampoo_beta

    def project(self, grad, state, merge_dims=False, max_precond_dim=10000):
        """
        Projects the gradient to the eigenbases of the preconditioner.
        """
        original_shape = grad.shape
        if merge_dims:
            if grad.dim() == 4 and self._data_format == 'channels_last':
                permuted_shape = grad.permute(0, 3, 1, 2).shape
            grad = self.merge_dims(grad, max_precond_dim)

        for mat in state['Q']:
            if len(mat) > 0:
                grad = torch.tensordot(
                    grad, mat,
                    dims=[[0], [0]],
                )
            else:
                permute_order = list(range(1, len(grad.shape))) + [0]
                grad = grad.permute(permute_order)

        if merge_dims:
            if self._data_format == 'channels_last' and len(original_shape) == 4:
                grad = grad.reshape(permuted_shape).permute(0, 2, 3, 1)
            else:
                grad = grad.reshape(original_shape)

        return grad

    def project_back(self, grad, state, merge_dims=False, max_precond_dim=10000):
        """
        Projects the gradient back to the original space.
        """
        original_shape = grad.shape
        if merge_dims:
            if self._data_format == 'channels_last' and grad.dim() == 4:
                permuted_shape = grad.permute(0, 3, 1, 2).shape
            grad = self.merge_dims(grad, max_precond_dim)

        for mat in state['Q']:
            if len(mat) > 0:
                grad = torch.tensordot(
                    grad, mat,
                    dims=[[0], [1]],
                )
            else:
                permute_order = list(range(1, len(grad.shape))) + [0]
                grad = grad.permute(permute_order)

        if merge_dims:
            if self._data_format == 'channels_last' and len(original_shape) == 4:
                grad = grad.reshape(permuted_shape).permute(0, 2, 3, 1)
            else:
                grad = grad.reshape(original_shape)

        return grad

    def update_preconditioner(self, grad, state, max_precond_dim=10000, merge_dims=False, precondition_1d=False):
        """
        Updates the preconditioner matrices and the eigenbases.
        """
        if state["Q"] is not None:
            state["exp_avg"] = self.project_back(state["exp_avg"], state, merge_dims=merge_dims, max_precond_dim=max_precond_dim)

        if grad.dim() == 1:
            if precondition_1d and grad.shape[0] <= max_precond_dim:
                state['GG'][0].lerp_(grad.unsqueeze(1) @ grad.unsqueeze(0), 1-state['shampoo_beta'])
        else:
            if merge_dims:
                new_grad = self.merge_dims(grad, max_precond_dim)
                for idx, sh in enumerate(new_grad.shape):
                    if sh <= max_precond_dim:
                        outer_product = torch.tensordot(
                            new_grad, new_grad,
                            dims=[[*chain(range(idx), range(idx + 1, len(new_grad.shape)))]] * 2,
                        )
                        state['GG'][idx].lerp_(outer_product, 1-state['shampoo_beta'])
            else:
                for idx, sh in enumerate(grad.shape):
                    if sh <= max_precond_dim:
                        outer_product = torch.tensordot(
                            grad, grad,
                            dims=[[*chain(range(idx), range(idx + 1, len(grad.shape)))]] * 2,
                        )
                        state['GG'][idx].lerp_(outer_product, 1-state['shampoo_beta'])

        if state['Q'] is None:
            state['Q'] = self.get_orthogonal_matrix(state['GG'])

        if state['step'] > 0 and state['step'] % state['precondition_frequency'] == 0:
            state['Q'] = self.get_orthogonal_matrix_QR(state, max_precond_dim, merge_dims)

        if state["step"] > 0:
            state["exp_avg"] = self.project(state["exp_avg"], state, merge_dims=merge_dims, max_precond_dim=max_precond_dim)

    def get_orthogonal_matrix(self, mat):
        """
        Computes the eigenbases of the preconditioner using torch.linalg.eigh decomposition.
        """
        matrix = []
        for m in mat:
            if len(m) == 0:
                matrix.append([])
                continue
            if m.data.dtype != torch.float:
                float_data = False
                original_type = m.data.dtype
                original_device = m.data.device
                matrix.append(m.data.float())
            else:
                float_data = True
                matrix.append(m.data)

        final = []
        for m in matrix:
            if len(m) == 0:
                final.append([])
                continue
            try:
                _, Q = torch.linalg.eigh(m+1e-30*torch.eye(m.shape[0], device=m.device))
            except:
                _, Q = torch.linalg.eigh(m.to(torch.float64)+1e-30*torch.eye(m.shape[0], device=m.device))
                Q = Q.to(m.dtype)
            Q = torch.flip(Q, [1])
            if not float_data:
                Q = Q.to(original_device).type(original_type)
            final.append(Q)
        return final

    def get_orthogonal_matrix_QR(self, state, max_precond_dim=10000, merge_dims=False):
        """
        Computes the eigenbases using one round of power iteration followed by torch.linalg.qr decomposition.
        """
        precond_list = state['GG']
        orth_list = state['Q']
        matrix = []
        orth_matrix = []
        for m,o in zip(precond_list, orth_list):
            if len(m) == 0:
                matrix.append([])
                orth_matrix.append([])
                continue
            if m.data.dtype != torch.float:
                float_data = False
                original_type = m.data.dtype
                original_device = m.data.device
                matrix.append(m.data.float())
                orth_matrix.append(o.data.float())
            else:
                float_data = True
                matrix.append(m.data.float())
                orth_matrix.append(o.data.float())

        orig_shape = state['exp_avg_sq'].shape
        if self._data_format == 'channels_last' and len(orig_shape) == 4:
            permuted_shape = state['exp_avg_sq'].permute(0, 3, 1, 2).shape
        if merge_dims:
            exp_avg_sq = self.merge_dims(state['exp_avg_sq'], max_precond_dim)
        else:
            exp_avg_sq = state['exp_avg_sq']

        final = []
        for ind, (m,o) in enumerate(zip(matrix, orth_matrix)):
            if len(m)==0:
                final.append([])
                continue
            est_eig = torch.diag(o.T @ m @ o)
            sort_idx = torch.argsort(est_eig, descending=True)
            exp_avg_sq = exp_avg_sq.index_select(ind, sort_idx)
            o = o[:,sort_idx]
            power_iter = m @ o
            Q, _ = torch.linalg.qr(power_iter)
            if not float_data:
                Q = Q.to(original_device).type(original_type)
            final.append(Q)

        if merge_dims:
            if self._data_format == 'channels_last' and len(orig_shape) == 4:
                exp_avg_sq = exp_avg_sq.reshape(permuted_shape).permute(0, 2, 3, 1)
            else:
                exp_avg_sq = exp_avg_sq.reshape(orig_shape)
        state['exp_avg_sq'] = exp_avg_sq
        return final


# =============================================================================
# Unified Optimizer Factory Function
# =============================================================================

def create_optimizer(
    optimizer_type: str,
    model_parameters,
    config: Optional[Union[Dict[str, Any], object]] = None,
    **kwargs
) -> optim.Optimizer:
    """
    Unified factory function to create optimizers.

    Supports both dictionary-based and config object-based parameter passing.

    Args:
        optimizer_type: Optimizer type ('Adam', 'AdamW', 'SGD', 'SOAP')
        model_parameters: Model parameters to optimize
        config: Configuration dict or object containing optimizer parameters
        **kwargs: Override parameters (highest priority)

    Returns:
        Configured optimizer instance

    Examples:
        # Using dictionary config
        optimizer = create_optimizer('Adam', model.parameters(),
                                    config={'learning_rate': 1e-4, 'weight_decay': 1e-6})

        # Using config object
        optimizer = create_optimizer('Adam', model.parameters(), config=optimizer_config)

        # Using kwargs override
        optimizer = create_optimizer('Adam', model.parameters(), lr=1e-3, weight_decay=0.01)
    """
    from torch.optim import Adam, AdamW, SGD

    optimizer_type = optimizer_type.lower()

    # Helper function to get parameter value with priority: kwargs > config > default
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

    # Create optimizer based on type
    if optimizer_type in ['adam', 'adamw', 'sgd', 'soap']:
        # Get common parameters
        lr = get_param('lr', 1e-4, ['learning_rate'])
        weight_decay = get_param('weight_decay', 1e-6)

        if optimizer_type == 'adam':
            beta1 = get_param('beta1', 0.9, ['adam_beta1'])
            beta2 = get_param('beta2', 0.999, ['adam_beta2'])
            eps = get_param('eps', 1e-8, ['adam_eps'])

            optimizer = Adam(
                model_parameters,
                lr=lr,
                betas=(beta1, beta2),
                eps=eps,
                weight_decay=weight_decay
            )
            logger.info("✅ 使用Adam优化器")
            logger.info("   - 学习率: %s", lr)
            logger.info("   - Beta参数: (%s, %s)", beta1, beta2)
            logger.info("   - Epsilon: %s", eps)
            logger.info("   - 权重衰减: %s", weight_decay)

        elif optimizer_type == 'adamw':
            beta1 = get_param('beta1', 0.9, ['adam_beta1'])
            beta2 = get_param('beta2', 0.999, ['adam_beta2'])
            eps = get_param('eps', 1e-8, ['adam_eps'])

            optimizer = AdamW(
                model_parameters,
                lr=lr,
                betas=(beta1, beta2),
                eps=eps,
                weight_decay=weight_decay
            )
            logger.info("✅ 使用AdamW优化器")
            logger.info("   - 学习率: %s", lr)
            logger.info("   - Beta参数: (%s, %s)", beta1, beta2)
            logger.info("   - Epsilon: %s", eps)
            logger.info("   - 权重衰减: %s", weight_decay)

        elif optimizer_type == 'sgd':
            momentum = get_param('momentum', 0.9, ['sgd_momentum'])
            nesterov = get_param('nesterov', False)

            optimizer = SGD(
                model_parameters,
                lr=lr,
                momentum=momentum,
                weight_decay=weight_decay,
                nesterov=nesterov
            )
            logger.info("✅ 使用SGD优化器")
            logger.info("   - 学习率: %s", lr)
            logger.info("   - 动量: %s", momentum)
            logger.info("   - 权重衰减: %s", weight_decay)
            logger.info("   - Nesterov: %s", nesterov)

        elif optimizer_type == 'soap':
            beta1 = get_param('beta1', 0.95, ['soap_beta1', 'soap_betas'])
            beta2 = get_param('beta2', 0.95, ['soap_beta2'])
            eps = get_param('eps', 1e-8, ['adam_eps'])
            precondition_frequency = get_param('precondition_frequency', 10, ['soap_precond_freq'])
            max_precond_dim = get_param('max_precond_dim', 10000, ['soap_max_precond_dim'])
            precondition_1d = get_param('precondition_1d', False, ['soap_precond_1d'])
            normalize_grads = get_param('normalize_grads', False, ['soap_normalize_grads'])
            correct_bias = get_param('correct_bias', True, ['soap_correct_bias'])

            # Handle betas as tuple if provided
            if 'betas' in kwargs:
                beta1, beta2 = kwargs['betas']
            elif 'soap_betas' in (config if isinstance(config, dict) else vars(config) if config else {}):
                betas = config['soap_betas'] if isinstance(config, dict) else config.soap_betas
                beta1, beta2 = betas

            optimizer = SOAP(
                model_parameters,
                lr=lr,
                betas=(beta1, beta2),
                weight_decay=weight_decay,
                precondition_frequency=precondition_frequency,
                max_precond_dim=max_precond_dim,
                precondition_1d=precondition_1d,
                normalize_grads=normalize_grads,
                eps=eps,
                correct_bias=correct_bias,
            )
            logger.info("✅ 使用SOAP优化器 - 二阶优化with预条件")
            logger.info("   - 学习率: %s", lr)
            logger.info("   - Beta参数: (%s, %s)", beta1, beta2)
            logger.info("   - 预条件频率: %s 步", precondition_frequency)
            logger.info("   - 最大预条件维度: %s", max_precond_dim)
            logger.info("   - 权重衰减: %s", weight_decay)

        return optimizer

    else:
        supported_optimizers = ['adam', 'adamw', 'sgd', 'soap']
        raise ValueError(f"不支持的优化器类型: {optimizer_type}. 支持的类型: {supported_optimizers}")


# Backward compatibility alias
get_optimizer = create_optimizer


__all__ = [
    # PyTorch optimization settings
    'setup_torch',
    'configure_for_development',
    'configure_for_production',
    'configure_for_production_high_memory',
    'configure_for_inference',
    'disable_triton_autotune',
    'disable_torch_compile',
    'disable_cuda_graphs',
    'enable_fast_math',
    'set_memory_efficient',
    'print_current_config',

    # Optimizer
    'SOAP',
    'create_optimizer',
    'get_optimizer',  # Backward compatibility
]
