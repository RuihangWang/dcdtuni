"""
Neural Network Models Module

This module provides a unified interface for creating neural network models
based on configuration. It directly instantiates models without using a registry.

Usage:
    from FVmodel.NNmodels import create_model

    # Create model from config dict
    model = create_model(config.model_config)
"""

import torch.nn as nn
from typing import Dict, Any, Union
import logging
logger = logging.getLogger(__name__)

# Imports
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def create_model(config: Union[Dict[str, Any], Any]) -> nn.Module:
    """
    Create a neural network model based on configuration.

    Args:
        config: Model configuration (dict or config object) containing:
               - name: Model type name (e.g., 'FVGN', 'TransFVGN')
               - Other model-specific parameters

    Returns:
        nn.Module: Instantiated neural network model

    Raises:
        ValueError: If model type is not supported or configuration is invalid

    Examples:
        # Using dictionary (recommended - simpler)
        config_dict = {
            'name': 'TransFVGN',
            'hidden_size': 128,
            'message_passing_num': 2,
            'attn_processor_num': 3,
            'node_input_size': 8,
            'node_output_size': 5,
            'edge_input_size': 12,
            'dropout': False
        }
        model = create_model(config_dict)
    """
    # Handle dictionary input (preferred) - convert to simple namespace for uniform access
    if isinstance(config, dict):
        # Create a simple namespace for dict access via attributes
        from types import SimpleNamespace
        config = SimpleNamespace(**config)

    # Validate that model name is specified
    if not hasattr(config, 'name'):
        raise ValueError("Configuration must have 'name' field specifying model type")

    model_name = config.name

    # Define model creation functions
    def create_fvgn_model():
        from .FVGN import create_fvgn
        return create_fvgn(config)

    def create_transfvgn_model():
        from .TransFVGN import create_transfvgn
        return create_transfvgn(config)

    def create_transfvgnplus_model():
        from .TransFVGN import create_transfvgnplus
        return create_transfvgnplus(config)

    def create_textcond_transfvgn_model():
        from .TransFVGN import create_textcond_transfvgn
        return create_textcond_transfvgn(config)

    def create_globalcond_transfvgn_model():
        from .TransFVGN import create_globalcond_transfvgn
        return create_globalcond_transfvgn(config)

    def create_transolver_baseline():
        from .Baselines.TransolverBaseline import TransolverBaseline
        return TransolverBaseline(**{k: v for k, v in vars(config).items() if k != "name"})

    def create_transolverpp_baseline():
        from .Baselines.TransolverPPBaseline import TransolverPPBaseline
        return TransolverPPBaseline(**{k: v for k, v in vars(config).items() if k != "name"})

    def create_meshgraphnets_baseline():
        from .Baselines.MeshGraphNetsBaseline import MeshGraphNetsBaseline
        return MeshGraphNetsBaseline(**{k: v for k, v in vars(config).items() if k != "name"})

    def create_textcond_trans_v2():
        from .TransFVGN.TextCondTrans_v2 import TextCondTrans_v2
        return TextCondTrans_v2(**{k: v for k, v in vars(config).items() if k != "name"})

    def create_textcond_transfvgn_v3():
        from .TransFVGN.TextCondTransFVGN_v3 import TextCondTransFVGN_v3
        return TextCondTransFVGN_v3(**{k: v for k, v in vars(config).items() if k != "name"})

    def create_transfvgn_ss_model():
        from .TransFVGN import create_transfvgn_ss
        return create_transfvgn_ss(config)

    def create_transssca_v1():
        from .TransFVGN.TransSSCA_v1 import TransSSCA_v1
        return TransSSCA_v1(**{k: v for k, v in vars(config).items() if k != "name"})

    def create_aerogto_sca_v1():
        from .TransFVGN.AeroGTOSCA_v1 import AeroGTOSCA_v1
        return AeroGTOSCA_v1(**{k: v for k, v in vars(config).items() if k != "name"})

    def create_aerogto_baseline():
        from .Baselines.AeroGTOBaseline import AeroGTOBaseline
        return AeroGTOBaseline(**{k: v for k, v in vars(config).items() if k != "name"})

    def create_gino_baseline():
        from .Baselines.GINOBaseline import GINOBaseline
        return GINOBaseline(**{k: v for k, v in vars(config).items() if k != "name"})

    def create_gaot_baseline():
        from .Baselines.GAOTBaseline import GAOTBaseline
        return GAOTBaseline(**{k: v for k, v in vars(config).items() if k != "name"})

    # Model creation function mapping
    model_creators = {
        'FVGN': create_fvgn_model,
        'TransFVGN_v2': create_transfvgn_model,
        'TransFVGNplus': create_transfvgnplus_model,
        'TextCondTransFVGN_v1': create_textcond_transfvgn_model,
        'TransFVGN_SS_v1': create_transfvgn_ss_model,   # DNE-SS control: TransFVGN trunk, no K/V bridge
        'GlobalCondTransFVGN_v1': create_globalcond_transfvgn_model,
        'Transolver_baseline': create_transolver_baseline,
        'TransolverPP_baseline': create_transolverpp_baseline,
        'MeshGraphNets_baseline': create_meshgraphnets_baseline,
        'TextCondTrans_v2': create_textcond_trans_v2,
        'TextCondTransFVGN_v2': create_textcond_trans_v2,  # deprecated alias (renamed 2026-06-10)
        'TextCondTransFVGN_v3': create_textcond_transfvgn_v3,
        'TransSSCA_v1': create_transssca_v1,  # A5: sliced Self-Cross Attention (DNE-SSCA)
        'AeroGTO_baseline': create_aerogto_baseline,    # AAAI'25 graph-transformer operator trunk
        'AeroGTOSCA_v1': create_aerogto_sca_v1,         # AeroGTO trunk + our two-stream SCA bridge
        'GINO_baseline': create_gino_baseline,          # NeurIPS'23 GNO->FNO->GNO latent-grid operator
        'GAOT_baseline': create_gaot_baseline,          # NeurIPS'25 MAGNO->ViT->MAGNO latent operator
    }

    # Create model using dictionary lookup
    if model_name not in model_creators:
        available_models = list(model_creators.keys())
        raise ValueError(f"Unknown model type: {model_name}. Available: {available_models}")

    model = model_creators[model_name]()

    logger.info(f"✅ Created {model_name} model with {sum(p.numel() for p in model.parameters()):,} parameters")
    return model


__all__ = [
    'create_model'
]
