"""
Unified Configuration

This module defines the main Config class that combines all configuration components
into a single, unified interface for training and inference scripts.
"""

from dataclasses import dataclass, field
from typing import Dict, Any, Optional


@dataclass
class Config:
    """
    Unified configuration class that drives all training and inference scripts.

    This class integrates all specialized configuration components:
    - Model configurations: Multiple model configs defined at top level (model_configs)
    - Loss configurations: Multiple loss configs defined at top level (loss_configs)
    - Training configuration: Training parameters (data, epochs, optimizer, scheduler, logging, etc.)
    - Validate configuration: Validation parameters (data, mixed_precision, etc.)
    - Inference configuration: Inference parameters (data, mixed_precision, etc.)

    New structure:
    - model_configs: Dict of named model configurations (e.g., {"TransFVGN_v2": {...}, "SimpleGNN": {...}})
    - loss_configs: Dict of named loss configurations (e.g., {"lp_norm": {...}, "mse": {...}})
    - simulator_config: References model and loss by name (model_name, loss_name)
    - training_config: Contains optimizer_config, scheduler_config, and logging_config

    Note: Data configuration has been merged into mode-specific configs:
    - training_config: Contains training data parameters (data_root, batch_size, sample_ratio, etc.)
                      and optimizer_config, scheduler_config, logging_config
    - validate_config: Contains validation data parameters (tta_sample_ratio, tta_batch_size, etc.)
    - inference_config: Contains inference data parameters (tta_sample_ratio, tta_batch_size, etc.)

    GPU selection and mixed precision are configured per-mode in the respective configs.
    """

    # ============================================================================
    # DATASET PROCESSING CONFIGURATION
    # ============================================================================
    # Dataset configuration for data processing (used by process_dataset.py)
    dataset_config: Dict[str, Any] = field(default_factory=dict)

    # ============================================================================
    # TOP-LEVEL MODEL AND LOSS CONFIGURATIONS
    # ============================================================================
    # Multiple model and loss configurations can be defined here
    # and referenced by name in simulator_config
    model_configs: Dict[str, Dict[str, Any]] = field(default_factory=dict)  # Named model configurations
    loss_configs: Dict[str, Dict[str, Any]] = field(default_factory=dict)    # Named loss configurations (dicts)

    # ============================================================================
    # CORE CONFIGURATION COMPONENTS
    # ============================================================================
    # Active model and loss configs (resolved from simulator_config or legacy single config)
    model_config: Optional[Dict[str, Any]] = None  # Active model config
    loss_config: Optional[Dict[str, Any]] = None    # Active loss config (dict)

    # Mode-specific configurations (now dicts instead of dataclasses)
    training_config: Dict[str, Any] = field(default_factory=dict)  # Training + optimizer + scheduler + logging
    inference_config: Dict[str, Any] = field(default_factory=dict)  # Inference + data configuration

    # Top-level logging configuration
    logging_config: Dict[str, Any] = field(default_factory=dict)  # Logging configuration

    def __post_init__(self):
        """Initialize configuration components after initialization"""
        # Resolve model and loss configs from training_config or inference_config
        # Priority: training_config > inference_config

        # Determine which config to use for resolution
        config_source = self.training_config if self.training_config else self.inference_config

        if config_source:
            # Resolve model config from model_name
            if 'model_name' in config_source and config_source['model_name']:
                model_name = config_source['model_name']
                if model_name not in self.model_configs:
                    raise ValueError(
                        f"Model '{model_name}' not found in model_configs. "
                        f"Available models: {list(self.model_configs.keys())}"
                    )
                self.model_config = self.model_configs[model_name]

                # Set experiment_name to model_name for logger
                if self.training_config:
                    self.training_config['experiment_name'] = model_name

            # Resolve loss config from loss_name
            # First check training_config.loss_configs (nested), then top-level loss_configs
            loss_name = config_source.get('loss_name')
            if loss_name:
                # Check nested loss_configs in training_config first
                if 'loss_configs' in self.training_config and loss_name in self.training_config['loss_configs']:
                    self.loss_config = self.training_config['loss_configs'][loss_name]
                # Then check top-level loss_configs
                elif loss_name in self.loss_configs:
                    self.loss_config = self.loss_configs[loss_name]
                else:
                    raise ValueError(
                        f"Loss '{loss_name}' not found in loss_configs. "
                        f"Available losses: {list(self.loss_configs.keys())}"
                    )

    @property
    def net(self) -> Optional[str]:
        """Get model name"""
        return getattr(self.model_config, 'net', None) if self.model_config else None

    @property
    def batch_size(self) -> int:
        """Get batch size from training config"""
        return self.training_config.get('batch_size', 4)

    @batch_size.setter
    def batch_size(self, value: int):
        """Set batch size in training config"""
        self.training_config['batch_size'] = value

    @property
    def learning_rate(self) -> float:
        """Get learning rate from optimizer config"""
        optimizer_config = self.training_config.get('optimizer_config', {})
        return optimizer_config.get('learning_rate', 1e-4)

    @learning_rate.setter
    def learning_rate(self, value: float):
        """Set learning rate in optimizer config"""
        if 'optimizer_config' not in self.training_config:
            self.training_config['optimizer_config'] = {}
        self.training_config['optimizer_config']['learning_rate'] = value

    @property
    def num_epochs(self) -> int:
        """Get number of epochs"""
        return self.training_config.get('num_epochs', 100)

    @num_epochs.setter
    def num_epochs(self, value: int):
        """Set number of epochs"""
        self.training_config['num_epochs'] = value

    @property
    def data_root(self) -> str:
        """Get data root directory from training config"""
        return self.training_config.get('dataset_path', '')

    @data_root.setter
    def data_root(self, value: str):
        """Set data root directory in training config"""
        self.training_config['dataset_path'] = value

    @property
    def checkpoint_dir(self) -> str:
        """Get checkpoint directory from logging config"""
        return self.training_config.get('checkpoint_dir', 'checkpoints')

    @checkpoint_dir.setter
    def checkpoint_dir(self, value: str):
        """Set checkpoint directory in logging config"""
        self.training_config['checkpoint_dir'] = value

    # Legacy properties for backward compatibility
    @property
    def optimizer_config(self) -> Optional[Dict[str, Any]]:
        """Get optimizer config from training config"""
        return self.training_config.get('optimizer_config')

    @property
    def scheduler_config(self) -> Optional[Dict[str, Any]]:
        """Get scheduler config from training config"""
        return self.training_config.get('scheduler_config')

    @property
    def logger_root(self) -> Optional[str]:
        """Get logger root from logging config or training config"""
        # Check top-level logging_config first, then fall back to training_config
        if self.logging_config and 'logger_root' in self.logging_config:
            return self.logging_config['logger_root']
        return self.training_config.get('logger_root')

    def get_gpu_for_rank(self, rank: int) -> int:
        """
        Get GPU ID for a specific rank in distributed training.

        Args:
            rank: Process rank (0-indexed)

        Returns:
            GPU ID for this rank
        """
        gpu_ids = self.training_config.get('gpu_ids', [0])

        if gpu_ids is None or len(gpu_ids) == 0:
            return 0

        # Single GPU or rank 0
        if len(gpu_ids) == 1 or rank == 0:
            return gpu_ids[0]

        if rank >= len(gpu_ids):
            raise ValueError(f"Rank {rank} exceeds available GPUs {len(gpu_ids)}")

        return gpu_ids[rank]

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert configuration to dictionary.

        Returns:
            Dict[str, Any]: Dictionary representation of the configuration
        """

        result = {}

        # Add top-level logging config
        if self.logging_config:
            result['logging_config'] = self.logging_config

        # Add top-level model and loss configs
        if self.model_configs:
            # model_configs are already dicts
            result['model_configs'] = self.model_configs

        if self.loss_configs:
            result['loss_configs'] = self.loss_configs  # loss_configs are already dicts

        # Add active model config (for backward compatibility)
        if self.model_config:
            # model_config is now a dict
            result['model_config'] = self.model_config

        # Add active loss config (for backward compatibility)
        if self.loss_config:
            result['loss_config'] = self.loss_config  # loss_config is already a dict

        # Add mode-specific configs (already dicts)
        if self.training_config:
            result['training_config'] = self.training_config

        if self.inference_config:
            result['inference_config'] = self.inference_config

        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Config':
        """
        Create configuration from dictionary.

        Args:
            data: Dictionary containing configuration parameters

        Returns:
            Config: Configuration instance
        """
        # ========================================================================
        # Parse top-level model_configs and loss_configs dictionaries
        # ========================================================================
        model_configs = {}
        if 'model_configs' in data:
            for name, model_data in data['model_configs'].items():
                # Keep model config as dict
                model_configs[name] = model_data

        loss_configs = {}
        if 'loss_configs' in data:
            for name, loss_data in data['loss_configs'].items():
                loss_configs[name] = loss_data  # Keep as dict for simplicity

        # ========================================================================
        # Parse top-level logging_config
        # ========================================================================
        logging_config = data.get('logging_config', {}).copy() if 'logging_config' in data else {}

        # ========================================================================
        # Parse training_config and inference_config first
        # ========================================================================
        training_config = data.get('training_config', {}).copy() if 'training_config' in data else {}
        inference_config = data.get('inference_config', {}).copy() if 'inference_config' in data else {}

        # ========================================================================
        # Resolve active model_config and loss_config
        # ========================================================================
        model_config = None
        loss_config = None

        # Determine config source: training_config > inference_config
        config_source = training_config if training_config else inference_config

        # Resolve model config from model_name in training_config or inference_config
        if config_source and 'model_name' in config_source and config_source['model_name']:
            model_name = config_source['model_name']
            if model_name in model_configs:
                model_config = model_configs[model_name]
            else:
                raise ValueError(
                    f"Model '{model_name}' not found in model_configs. "
                    f"Available models: {list(model_configs.keys())}"
                )

        # Resolve loss config from loss_name
        # Check nested loss_configs in training_config first, then top-level
        if config_source and 'loss_name' in config_source and config_source['loss_name']:
            loss_name = config_source['loss_name']
            # Check nested loss_configs in training_config
            if 'loss_configs' in training_config and loss_name in training_config['loss_configs']:
                loss_config = training_config['loss_configs'][loss_name]
            # Check top-level loss_configs
            elif loss_name in loss_configs:
                loss_config = loss_configs[loss_name]
            else:
                raise ValueError(
                    f"Loss '{loss_name}' not found in loss_configs. "
                    f"Available losses: {list(loss_configs.keys())}"
                )

        # Fallback to legacy approach if no model/loss found yet
        if model_config is None:
            # Fallback to legacy approach: parse model and loss configs separately
            # This method supports both flat and two-level structure
            # Check for model type in both locations
            model_name = None
            model_data = {}

            if 'model_config' in data and isinstance(data['model_config'], dict):
                # Two-level structure: model parameters under 'model_config' key
                model_data = data['model_config']
                model_name = model_data.get('name')
            elif 'model' in data and isinstance(data['model'], dict):
                # Two-level structure: model parameters under 'model' key
                model_data = data['model']
                model_name = model_data.get('name')
            elif 'net' in data:
                # Flat structure: model parameters at top level
                model_name = data.get('net') or data.get('name')
                model_data = data

            # Create model configuration directly as dict
            if model_data:
                model_config = model_data  # Keep as dict

            # Handle loss configuration (can be nested under 'loss_config' or 'loss' key or flat) - keep as dict
            if loss_config is None:
                loss_data = data.get('loss_config', data.get('loss', {})) if ('loss_config' in data or 'loss' in data) else data
                loss_config = loss_data  # Keep as dict for simplicity

        # ========================================================================
        # Ensure optimizer_config and scheduler_config are present in training_config
        # ========================================================================
        if 'optimizer_config' not in training_config and 'optimizer_config' in data:
            # Backward compatibility: optimizer_config at top level
            training_config['optimizer_config'] = data['optimizer_config']
        if 'optimizer_config' not in training_config:
            # Default optimizer
            training_config['optimizer_config'] = {'optimizer_type': 'Adam', 'learning_rate': 1e-4}

        if 'scheduler_config' not in training_config and 'scheduler_config' in data:
            # Backward compatibility: scheduler_config at top level
            training_config['scheduler_config'] = data['scheduler_config']
        if 'scheduler_config' not in training_config:
            # Default scheduler
            training_config['scheduler_config'] = {'scheduler_type': 'StepLR', 'step_size': 10}

        # Merge logging_config into training_config if not already present
        if 'logging_config' not in training_config and logging_config:
            # logging_config is at top level, merge it into training_config
            training_config['logging_config'] = logging_config
        elif 'logging_config' not in training_config:
            # Default logging config
            training_config['logging_config'] = {'logger_root': 'Logger', 'log_level': 'INFO'}

        # ========================================================================
        # Create unified configuration
        # ========================================================================
        dataset_config = data.get('dataset_config', {}).copy() if 'dataset_config' in data else {}

        return cls(
            model_configs=model_configs,
            loss_configs=loss_configs,
            model_config=model_config,
            loss_config=loss_config,
            training_config=training_config,
            inference_config=inference_config,
            logging_config=logging_config,
            dataset_config=dataset_config,
        )

    def get_model_parameters(self) -> Dict[str, Any]:
        """
        Get model-specific parameters.
        
        Returns:
            Dict[str, Any]: Model parameters
        """
        if self.model_config:
            if hasattr(self.model_config, 'to_dict'):
                return self.model_config.to_dict()
            else:
                # Fallback for dictionary-like objects
                return self.model_config.copy()
        return {}

    @classmethod
    def _extract_config_params(cls, params: Dict[str, Any], config_class) -> Dict[str, Any]:
        """
        Extract parameters that belong to a specific configuration class.

        Args:
            params: All parameters
            config_class: Configuration class to extract parameters for

        Returns:
            Dict[str, Any]: Parameters that belong to the config class
        """
        from dataclasses import fields

        # Get valid field names for this config class
        valid_keys = {field.name for field in fields(config_class)}

        # Filter parameters to only include valid ones
        return {k: v for k, v in params.items() if k in valid_keys}

    def get_data_parameters(self) -> Dict[str, Any]:
        """
        Get data loading parameters from training_config.

        Returns:
            Dict[str, Any]: Data parameters including batch_size, data_root, etc.

        Note: This method is kept for backward compatibility.
              Data parameters are now in training_config.
        """
        # Extract only data-related parameters
        data_keys = ['dataset_path', 'batch_size', 'num_workers', 'pin_memory',
                     'shuffle_train', 'sample_ratio', 'khop', 'boundary_budget_ratio',
                     'force_reload', 'num_processes', 'case_names']
        return {k: v for k, v in self.training_config.items() if k in data_keys}

    def get_training_parameters(self) -> Dict[str, Any]:
        """
        Get training parameters.

        Returns:
            Dict[str, Any]: Training parameters (including data parameters)
        """
        return self.training_config.copy()

    def get_scheduler_parameters(self) -> Dict[str, Any]:
        """
        Get scheduler parameters.

        Returns:
            Dict[str, Any]: Scheduler parameters
        """
        return self.training_config.get('scheduler_config', {})

    def get_logging_parameters(self) -> Dict[str, Any]:
        """
        Get logging parameters.

        Returns:
            Dict[str, Any]: Logging parameters (from logging_config or training_config)
        """
        # Merge logging_config and training_config, with logging_config taking precedence
        result = {
            'log_level': self.logging_config.get('log_level') or self.training_config.get('log_level', 'INFO'),
            'experiment_name': self.training_config.get('experiment_name'),
            'logger_root': self.logging_config.get('logger_root') or self.training_config.get('logger_root'),
            'checkpoint_dir': self.training_config.get('checkpoint_dir', 'checkpoints')
        }
        return result
