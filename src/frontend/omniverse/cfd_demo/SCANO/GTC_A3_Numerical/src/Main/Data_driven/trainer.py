"""
Trainer

This module provides a complete, unified trainer that can be initialized with just a Config object.
It automatically creates and manages all training components including:
- Simulator (model + loss)
- Dataset and DataLoaders
- Optimizer and Scheduler
- Device management and logging
"""

import os
import json
import time
import torch
from pathlib import Path
from typing import Dict, Any, Optional, List
import logging
logger = logging.getLogger(__name__)
from torch.optim import Optimizer
from torch.optim.lr_scheduler import _LRScheduler


# Imports
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from config import Config
from Utils.Logger import Logger
from Utils.key import Key
from Main.Data_driven.runner import Runner


class Trainer(Runner):
    """
    Complete trainer that can be initialized with just a Config object.

    This trainer automatically creates and manages all training components:
    - Simulator (neural network model + loss function)
    - Dataset and DataLoaders (train only)
    - Optimizer and Scheduler
    - Device management and model placement
    - Training loop
    - Checkpointing and model saving
    - TensorBoard logging
    - Mixed precision training support

    Inherits from Runner base class for common infrastructure.
    """

    def __init__(
        self,
        config: Config,
        input_keys: List[Key],
        output_keys: List[Key],
        target_keys: List[Key],
        backup_paths: Optional[List[str]] = None,
        skip_dirs: Optional[List[str]] = None,
    ):
        """
        Initialize the Trainer.

        Args:
            config: Configuration object
            input_keys: List of input keys
            output_keys: List of output keys
            target_keys: List of target keys
        """
        # Call parent class initialization (mode="train")
        # This will setup: distributed, device, simulator, scaler
        super().__init__(config, input_keys, output_keys, target_keys, mode="train")
        
        self.backup_paths = backup_paths
        self.skip_dirs = skip_dirs

        if self.is_main_process:
            logger.info("🏋️ Trainer initialized")
            logger.info(f"   Distributed: {self.distributed}")
            logger.info(f"   GPU IDs: {self.gpu_ids}")
            logger.info(f"   Device: {self.device}")
            logger.info(f"   Mixed Precision: {self.use_amp}")
            if self.distributed:
                logger.info(f"   Rank: {self.rank}/{self.world_size}")

        # Training-specific initialization
        self.train_loader = None

        # Setup optimizer and scheduler
        self.optimizer = self._create_optimizer()
        self.scheduler = self._create_scheduler()

        # Training state
        self.current_epoch = 0
        self.global_step = 0
        self.best_train_loss = float('inf')
        self.training_history = []

        # Metrics tracking
        self.train_losses = []

        # Store resume path for logger setup (treat empty string as None)
        checkpoint_path = self.config.training_config.get('checkpoint_path')
        self.resume_checkpoint_path = checkpoint_path if checkpoint_path and checkpoint_path != "" else None

        # Setup logging with integrated Logger (only on main process)
        if not self.distributed or self.is_main_process:
            self.logger = self._setup_logger()
            self.log_dir = Path(self.logger.saving_path)
        else:
            self.logger = None
            self.log_dir = None

    def _create_optimizer(self) -> Optimizer:
        """Create optimizer from config using unified factory function"""
        from Utils.local_optimizer import create_optimizer

        optimizer_config = self.config.optimizer_config
        optimizer_type = optimizer_config.get('optimizer_type', 'Adam')

        all_params = [p for p in self.simulator.parameters() if p.requires_grad]

        if True:
            param_groups = [{'params': all_params, 'weight_decay': 0.0}]
            if self.is_main_process:
                logger.info("   🔧 weight_decay=0 for ALL %d params", len(all_params))
            optimizer = create_optimizer(
                optimizer_type=optimizer_type,
                model_parameters=param_groups,
                config=optimizer_config
            )
        else:
            optimizer = create_optimizer(
                optimizer_type=optimizer_type,
                model_parameters=self.simulator.parameters(),
                config=optimizer_config
            )

        return optimizer

    def _create_scheduler(self) -> Optional[_LRScheduler]:
        """Create learning rate scheduler from config using unified factory function"""
        from Utils.local_scheduler import create_scheduler

        # Get scheduler config (now a dict)
        scheduler_config = self.config.scheduler_config.copy() if self.config.scheduler_config else {}

        # Extract scheduler type
        scheduler_type = scheduler_config.get('scheduler_type', None)

        if scheduler_type is None or scheduler_type.lower() == 'none':
            if self.is_main_process and not self.distributed:
                logger.info("   ⏭️ No scheduler configured")
            return None

        # Auto-calculate T_max for CosineAnnealingLR if not specified
        if scheduler_type.lower() in ['cosine', 'cosineannealinglr']:
            if 'T_max' not in scheduler_config or scheduler_config.get('auto_T_max', True):
                # 🔧 FIX: T_max should be in epochs (not steps) for per-epoch scheduling
                # Since we now call scheduler.step() once per epoch (not per batch),
                # T_max should equal num_epochs
                num_epochs = self.config.training_config.get('num_epochs', 100)
                scheduler_config['T_max'] = num_epochs

                if self.is_main_process:
                    logger.info(f"   📊 Auto-calculated T_max = {num_epochs} epochs")
                    logger.info(f"   ✅ Using per-epoch scheduling (scheduler.step() called once per epoch)")

                    # Calculate expected LR at different epochs for user reference
                    import math
                    lr_init = self.config.training_config.get('optimizer_config', {}).get('learning_rate', 5e-4)
                    eta_min = scheduler_config.get('eta_min', 5e-5)

                    # Show LR at key epochs
                    key_epochs = [1, 10, 50, 100, 500, 1000, num_epochs // 2, num_epochs]
                    key_epochs = [e for e in key_epochs if e <= num_epochs]
                    logger.info(f"   📉 Expected LR schedule (lr_init={lr_init:.2e}, eta_min={eta_min:.2e}):")
                    for ep in key_epochs:
                        lr = eta_min + (lr_init - eta_min) * (1 + math.cos(math.pi * ep / num_epochs)) / 2
                        logger.info(f"      Epoch {ep:5d}: LR = {lr:.6e}")

        # Create scheduler using unified factory function
        scheduler = create_scheduler(
            scheduler_type=scheduler_type,
            optimizer=self.optimizer,
            config=scheduler_config
        )

        return scheduler


    def _print_initialization_summary(self):
        """print initialization summary"""
        logger.info("\n🎯 Trainer Initialization Complete!")
        logger.info("=" * 60)
        logger.info(f"📋 Configuration Summary:")
        logger.info(f"   Model: {type(self.simulator.model).__name__}")
        logger.info(f"   Loss: {type(self.simulator.loss).__name__}")
        logger.info(f"   Optimizer: {type(self.optimizer).__name__}")
        logger.info(f"   Scheduler: {type(self.scheduler).__name__ if self.scheduler else 'None'}")
        logger.info(f"   Device: {self.device}")
        logger.info(f"   Parameters: {self.get_parameter_count():,}")

        if self.train_loader:
            logger.info(f"   Train batches: {len(self.train_loader)}")

        logger.info(f"   Mixed precision: {'Enabled' if self.scaler else 'Disabled'}")
        logger.info("=" * 60)

    def _setup_logger(self) -> Logger:
        """Setup integrated Logger with TensorBoard support"""
        # Create logger directly using Logger class
        forced_saving_path = os.environ.get("GTC_RUN_DIR")

        # Determine logger root directory
        logging_config = self.config.training_config.get('logging_config', {})
        logger_root = logging_config.get('logger_root')
        if logger_root is not None:
            # Use custom logger root (e.g., 'cases/Alibaba-Shanghai/Logger')
            logger_head = logger_root
        else:
            # Use default 'Logger' in project root
            logger_head = "Logger"

        # Check if we're resuming from a checkpoint
        if self.resume_checkpoint_path is not None:
            # Extract the original logger directory from checkpoint path
            checkpoint_path = Path(self.resume_checkpoint_path)

            if checkpoint_path.is_dir():
                # If it's a directory, use it directly
                original_logger_path = str(checkpoint_path)
            else:
                # If it's a file, get the parent directory (should be states dir)
                # Go up two levels: states -> timestamp -> model_name
                original_logger_path = str(checkpoint_path.parent.parent)

            if self.is_main_process and not self.distributed:
                logger.info(f"🔄 Resuming training, using existing logger directory: {original_logger_path}")

            integrated_logger = Logger(
                name=self.config.training_config.get('experiment_name'),
                head=logger_head,
                use_tensorboard=True,
                params=self.config.to_dict(),
                saving_path=original_logger_path,
                copy_code=False,  # 恢复训练时不复制代码（避免覆盖原有代码）
                rank=self.rank  # 传递rank信息
            )
        elif forced_saving_path:
            integrated_logger = Logger(
                name=self.config.training_config.get('experiment_name'),
                head=logger_head,
                use_tensorboard=True,
                params=self.config.to_dict(),
                saving_path=forced_saving_path,
                copy_code=True,
                backup_paths=self.backup_paths,
                skip_dirs=self.skip_dirs,
                rank=self.rank
            )
        else:
            # Normal case: create new logger with timestamp
            # Logger will create path: {logger_head}/{experiment_name}/{timestamp}
            integrated_logger = Logger(
                name=self.config.training_config.get('experiment_name'),
                head=logger_head,
                use_tensorboard=True,
                params=self.config.to_dict(),
                copy_code=True,  # 开启代码备份功能
                backup_paths=self.backup_paths,
                skip_dirs=self.skip_dirs,
                rank=self.rank  # 传递rank信息
            )

        # Logger类已经自动配置了全局logging系统，包括console和file handlers
        # 所有模块的logger都会自动输出到console和training.log文件
        logger.info(f"✅ Integrated Logger initialized: {integrated_logger.saving_path}")

        return integrated_logger

    def train(
        self
    ) -> Dict[str, Any]:
        """
        Main training loop.

        Returns:
            Dict containing training history and final metrics
        """
        # Resume from checkpoint if specified in config
        checkpoint_path = self.config.training_config.get('checkpoint_path')
        if checkpoint_path is not None and checkpoint_path != "":
            self._resume_from_checkpoint(checkpoint_path)

        # Create data loader using Runner's unified method
        logger.info("📂 Creating training data loader...")
        self.train_loader = self._create_dataloader(
            data_root=self.config.training_config.get('dataset_path'),
            split="train",
        )
        if self.is_main_process:
            logger.info(f"   ✅ Train loader: {len(self.train_loader)} batches")

        # Get training parameters from config
        num_epochs = self.config.training_config.get('num_epochs', 100)
        save_best = self.config.training_config.get('save_best', True)

        if self.is_main_process and not self.distributed:
            logger.info(f"🚀 Starting training for {num_epochs} epochs")
            logger.info(f"   Training batches: {len(self.train_loader)}")

        start_time = time.time()

        for epoch in range(self.current_epoch, num_epochs):
            epoch_start_time = time.time()
            # Reset so train_peak_mem_gib below measures THIS epoch's peak (cost-table column).
            if torch.cuda.is_available():
                torch.cuda.reset_peak_memory_stats()

            # Training phase - use Runner's unified training loop
            train_metrics = self._run_train_loop(
                train_loader=self.train_loader,
                optimizer=self.optimizer,
                scheduler=self.scheduler,
                current_epoch=epoch,
                global_step=self.global_step,
                log_metrics_fn=self.log_metrics
            )

            # Update global step from metrics
            self.global_step = train_metrics['global_step']

            # Track training loss history
            self.train_losses.append(train_metrics['train_loss'])

            # Prepare epoch metrics
            epoch_metrics = train_metrics
            epoch_metrics['epoch'] = epoch
            epoch_metrics['time'] = time.time() - epoch_start_time
            epoch_metrics['lr'] = self.optimizer.param_groups[0]['lr']
            # Train peak GPU memory (GB) for the AeroGTO/GAOT-style cost table.
            if torch.cuda.is_available():
                epoch_metrics['train_peak_mem_gib'] = torch.cuda.max_memory_allocated() / 1024 ** 3

            # Update training history
            self.training_history.append(epoch_metrics)
            self.current_epoch = epoch

            # Log metrics (only on main process)
            self.log_metrics(epoch_metrics, step=epoch, log_to_csv=True)

            # logger.info progress (only on main process)
            if not self.distributed or self.is_main_process:
                 self._print_epoch_progress(epoch, epoch_metrics)

            # Synchronize processes
            self._sync_processes()

            # 🔧 Learning rate scheduling (per-epoch for all schedulers)
            # Changed from per-step to per-epoch to avoid very slow LR decay
            # when batches_per_epoch is small (e.g., 2 batches/epoch)
            if self.scheduler and epoch_metrics.get('optimizer_steps', 0) > 0:
                if isinstance(self.scheduler, torch.optim.lr_scheduler.ReduceLROnPlateau):
                    # Use training loss for ReduceLROnPlateau
                    self.scheduler.step(epoch_metrics['train_loss'])
                else:
                    # For other schedulers (CosineAnnealingLR, StepLR, etc.)
                    # Call step() once per epoch
                    self.scheduler.step()

                # Log LR change if significant
                new_lr = self.optimizer.param_groups[0]['lr']
                if abs(new_lr - epoch_metrics['lr']) > 1e-7:
                    if not self.distributed or self.is_main_process:
                        logger.debug(f"   📉 LR updated: {epoch_metrics['lr']:.6e} → {new_lr:.6e}")

            # Save checkpoint (every 10 epochs by default)
            if epoch % 10 == 0:
                self.save_checkpoint(epoch)

            # Save best model based on training loss
            if save_best:
                current_loss = epoch_metrics['train_loss']

                if current_loss < self.best_train_loss:
                    self.best_train_loss = current_loss
                    self.save_checkpoint(epoch, is_best=True)
                    if not self.distributed or self.is_main_process:
                        logger.info(f"🏆 New best train loss: {current_loss:.6f}")

            if self.is_main_process:
                self._log_embedding_health(epoch + 1)

        total_time = time.time() - start_time
        logger.info(f"✅ Training completed in {total_time:.1f}s")

        # Save final results
        self._save_final_results()

        return {
            'training_history': self.training_history,
            'best_train_loss': self.best_train_loss,
            'total_epochs': self.current_epoch + 1,
            'total_time': total_time,
            'final_metrics': self.training_history[-1] if self.training_history else {}
        }

    def save_checkpoint(self, epoch: int, is_best: bool = False):
        """Save training checkpoint using Runner's unified API"""
        # Only save on main process in distributed training
        if self.distributed and not self.is_main_process:
            return

        # Determine checkpoint index
        index = "best" if is_best else f"epoch_{epoch+1}"
        checkpoint_path = f"{self.logger.saving_path}/states/{index}.state"

        # Use Runner's unified save method
        self._save_checkpoint(
            checkpoint_path,
            optimizer_state_dict=self.optimizer.state_dict() if self.optimizer else None,
            scheduler_state_dict=self.scheduler.state_dict() if self.scheduler else None,
            scaler_state_dict=self.scaler.state_dict() if self.scaler else None,
            epoch=epoch,
            global_step=self.global_step,
            best_train_loss=self.best_train_loss,
            training_history=self.training_history,
            current_epoch=self.current_epoch
        )

        # Clean up old checkpoints if configured
        if not is_best:
            self._cleanup_old_checkpoints()

    def load_checkpoint(self, checkpoint_path: str) -> Dict[str, Any]:
        """Load training checkpoint using Simulator's load method"""
        # Load using Simulator's method
        checkpoint = self.simulator.load_model(checkpoint_path)

        if self.optimizer:
            if checkpoint.get('optimizer_state_dict') is not None:
                self.optimizer.load_state_dict(checkpoint['optimizer_state_dict'])
            elif checkpoint.get('optimizer') is not None:
                self.optimizer.load_state_dict(checkpoint['optimizer'].state_dict())

        if self.scheduler:
            if checkpoint.get('scheduler_state_dict') is not None:
                self.scheduler.load_state_dict(checkpoint['scheduler_state_dict'])
            elif checkpoint.get('scheduler') is not None:
                self.scheduler.load_state_dict(checkpoint['scheduler'].state_dict())

        if self.scaler:
            if checkpoint.get('scaler_state_dict') is not None:
                self.scaler.load_state_dict(checkpoint['scaler_state_dict'])
            elif checkpoint.get('scaler') is not None:
                self.scaler.load_state_dict(checkpoint['scaler'].state_dict())

        # Restore training state
        resume_epoch = checkpoint.get('current_epoch', checkpoint.get('epoch', 0))
        self.current_epoch = resume_epoch + 1
        self.global_step = checkpoint.get('global_step', 0)
        self.best_train_loss = checkpoint.get('best_train_loss', float('inf'))
        self.training_history = checkpoint.get('training_history', [])

        logger.info(f"📥 Checkpoint loaded: {checkpoint_path}")
        logger.info(f"   Resuming from epoch {self.current_epoch}")
        if self.scaler and 'scaler_state_dict' in checkpoint:
            logger.info(f"   Mixed precision scaler state restored")

        return checkpoint

    def _resume_from_checkpoint(self, checkpoint_path: str):
        """
        Resume training from a checkpoint directory or specific checkpoint file.

        Args:
            checkpoint_path: Path to checkpoint directory or specific checkpoint file
        """
        from pathlib import Path

        checkpoint_path = Path(checkpoint_path)

        # Store the original checkpoint path for logger setup
        self.resume_checkpoint_path = str(checkpoint_path)

        # If it's a directory, find the latest checkpoint
        if checkpoint_path.is_dir():
            # Look for states subdirectory
            states_dir = checkpoint_path / "states"
            if states_dir.exists():
                checkpoint_files = list(states_dir.glob("*.state"))
                if not checkpoint_files:
                    raise FileNotFoundError(f"No checkpoint files found in {states_dir}")

                # Sort by modification time to get the latest checkpoint
                checkpoint_files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
                latest_checkpoint = checkpoint_files[0]

                logger.info(f"📂 Found {len(checkpoint_files)} checkpoints in {states_dir}")
                logger.info(f"📥 Loading latest checkpoint: {latest_checkpoint.name}")

            else:
                raise FileNotFoundError(f"States directory not found in {checkpoint_path}")
        else:
            # It's a specific file
            if not checkpoint_path.exists():
                raise FileNotFoundError(f"Checkpoint file not found: {checkpoint_path}")
            latest_checkpoint = checkpoint_path
            logger.info(f"📥 Loading specified checkpoint: {latest_checkpoint}")

        # Load the checkpoint
        checkpoint = self.load_checkpoint(str(latest_checkpoint))

        # Extract epoch information from scheduler state
        actual_epoch = None
        epoch_source = None
        if checkpoint.get('current_epoch') is not None:
            actual_epoch = checkpoint['current_epoch']
            epoch_source = 'current_epoch'
        elif checkpoint.get('epoch') is not None:
            actual_epoch = checkpoint['epoch']
            epoch_source = 'epoch'
        elif 'scheduler_state_dict' in checkpoint and checkpoint['scheduler_state_dict'] is not None and 'last_epoch' in checkpoint['scheduler_state_dict']:
            actual_epoch = checkpoint['scheduler_state_dict']['last_epoch']
            epoch_source = 'scheduler_state_dict.last_epoch'
        elif checkpoint.get('scheduler') is not None:
            actual_epoch = getattr(checkpoint['scheduler'], 'last_epoch', None)
            if actual_epoch is not None:
                epoch_source = 'scheduler.last_epoch'

        if actual_epoch is not None:
            # Update current epoch to the next epoch to train
            self.current_epoch = actual_epoch + 1
            logger.info(f"📈 从{epoch_source}获取epoch: {actual_epoch}")
        else:
            logger.info("⚠️  Warning: Could not find epoch information in scheduler_state_dict")
            return

        # Calculate remaining epochs
        total_epochs = self.config.training_config.get('num_epochs', 100)
        remaining_epochs = total_epochs - self.current_epoch

        logger.info(f"🔄 Resuming training:")
        logger.info(f"   Checkpoint epoch: {actual_epoch}")
        logger.info(f"   Next epoch to train: {self.current_epoch}")
        logger.info(f"   Total epochs: {total_epochs}")
        logger.info(f"   Remaining epochs: {remaining_epochs}")
        logger.info(f"   Best training loss: {self.best_train_loss:.6f}")

        if remaining_epochs <= 0:
            logger.info("⚠️  Warning: Training already completed according to config!")
            logger.info(f"   Consider increasing num_epochs in config (current: {total_epochs})")

    def log_metrics(self, metrics: Dict[str, float], step: int, log_to_csv: bool = False):
        """Log metrics using integrated Logger"""
        # Only log on main process in distributed training
        if self.distributed and not self.is_main_process:
            return

        # Log to TensorBoard via Logger (only on main process)
        if self.logger is not None:
            self.logger.log_metrics(metrics, step)

            # Optionally log residuals to CSV for detailed tracking
            if log_to_csv:
                self.logger.log_residuals(iteration=step, **metrics)

            # Flush TensorBoard writer
            self.logger.flush_tensorboard()

    def get_parameter_count(self) -> int:
        """Get total number of trainable parameters"""
        return sum(p.numel() for p in self.simulator.parameters() if p.requires_grad)

    def get_training_summary(self) -> Dict[str, Any]:
        """Get summary of training progress"""
        summary = {
            'current_epoch': self.current_epoch,
            'global_step': self.global_step,
            'best_train_loss': self.best_train_loss,
            'train_losses': self.train_losses,
            'model_parameters': self.get_parameter_count(),
            'device': str(self.device),
            'mixed_precision': self.scaler is not None,
            'model_type': type(self.simulator.model).__name__,
            'loss_type': type(self.simulator.loss).__name__
        }

        if self.train_losses:
            summary['current_train_loss'] = self.train_losses[-1]

        return summary

    def _print_epoch_progress(self, epoch: int, metrics: Dict[str, float]):
        """log epoch progress"""
        # Log every epoch by default
        should_log = self.config.training_config.get('should_log_this_epoch', lambda x: True)
        if callable(should_log) and not should_log(epoch):
            return

        train_loss = metrics.get('train_loss', 0.0)
        lr = metrics.get('lr', 0.0)
        epoch_time = metrics.get('time', 0.0)
        avg_nodes_after = metrics.get('train/avg_nodes_after_trunc')
        trunc_ratio = metrics.get('train/truncated_batch_ratio')
        avg_pivots = metrics.get('train/avg_sampled_pivots')

        progress_str = f"Epoch {epoch+1:3d}/{self.config.training_config.get('num_epochs', 100)}"

        # Always show train loss (even if 0), with scientific notation for very small values
        if train_loss == 0.0:
            progress_str += f" | Train Loss: {train_loss:.1f}"
        elif train_loss < 1e-4:
            progress_str += f" | Train Loss: {train_loss:.2e}"
        else:
            progress_str += f" | Train Loss: {train_loss:.6f}"

        progress_str += f" | LR: {lr:.2e} | Time: {epoch_time:.1f}s"
        if avg_nodes_after is not None and avg_pivots is not None:
            progress_str += f" | Nodes: {avg_nodes_after:.0f} | Pivots: {avg_pivots:.0f}"
        if trunc_ratio is not None:
            progress_str += f" | Trunc: {trunc_ratio:.1%}"
        if self.is_main_process:
            logger.info(progress_str)

    def _save_final_results(self):
        """Save final training results (only on main process)"""
        # Only save results on main process
        if self.logger is None or self.log_dir is None:
            return

        results = {
            'config': self.config.to_dict(),
            'training_history': self.training_history,
            'best_train_loss': self.best_train_loss,
            'total_epochs': self.current_epoch + 1,
            'model_parameters': self.get_parameter_count(),
            'final_metrics': self.training_history[-1] if self.training_history else {}
        }

        results_path = self.log_dir / "training_results.json"
        with open(results_path, 'w') as f:
            json.dump(results, f, indent=2)

        logger.info(f"📊 Results saved: {results_path}")

    def _cleanup_old_checkpoints(self):
        """Clean up old checkpoint files"""
        # Use default value if max_checkpoints_to_keep is not configured
        max_checkpoints = self.config.training_config.get('max_checkpoints_to_keep', 5)
        if not max_checkpoints:
            return

        # Get all checkpoint files (only on main process)
        if self.logger is None:
            return

        from pathlib import Path
        ckp_dir = Path(self.logger.saving_path) / "states"
        checkpoint_files = list(ckp_dir.glob("*.state"))
        
        # exclude the best.state so that the best.state will not be deleted
        checkpoint_files = [file for file in checkpoint_files if file.name != "best.state"]

        # Sort by modification time (newest first)
        checkpoint_files.sort(key=lambda x: x.stat().st_mtime, reverse=True)

        # Remove old checkpoints
        for old_checkpoint in checkpoint_files[max_checkpoints:]:
            old_checkpoint.unlink()
