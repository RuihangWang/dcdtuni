"""
Configuration Module

Provides the unified Config class for managing all configuration parameters.
"""

from .config import Config
import yaml


def load_config(config_path: str) -> Config:
    """
    Load configuration from YAML file.

    Args:
        config_path: Path to YAML configuration file

    Returns:
        Config object
    """
    with open(config_path, 'r') as f:
        config_data = yaml.safe_load(f)
    return Config.from_dict(config_data)


__all__ = [
    'Config',
    'load_config'
]
