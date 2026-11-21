"""
FVGN Model Module

This module contains the FVGN (Finite Volume Graph Network) implementation.
Configuration is passed as dict or object with attributes.

Reference: T. Li, Y. Zou, S. Zou, X. Chang, L. Zhang and X. Deng, "Learning to solve PDEs
with finite volume-informed neural networks in a data-free approach," Journal of
Computational Physics 530:113919 (2025). doi:10.1016/j.jcp.2025.113919 -- the
compound-cell graph construction and the finite-volume graph trunk implemented here
follow that work.
"""

from .FVGN import FVGN

def create_fvgn(config):
    """
    Factory function for FVGN model.

    Args:
        config: Configuration object or dict with attributes:
            - message_passing_num: Number of message passing layers
            - node_input_size: Input size for nodes
            - edge_input_size: Input size for edges
            - hidden_size: Hidden layer size
            - dropout: Whether to use dropout

    Returns:
        FVGN model instance
    """
    return FVGN(
        message_passing_num=config.message_passing_num,
        node_input_size=config.node_input_size,
        edge_input_size=config.edge_input_size,
        node_output_size=config.node_input_size,
        drop_out=config.dropout,
        hidden_size=config.hidden_size
    )
