"""
TransFVGN model factory.

Keep imports lazy so the v2 path does not depend on optional/experimental
TransFVGNplus files being present in the workspace.

Reference: T. Li, Y. Zou, S. Zou, X. Chang, L. Zhang and X. Deng, "Learning to solve PDEs
with finite volume-informed neural networks in a data-free approach," Journal of
Computational Physics 530:113919 (2025). doi:10.1016/j.jcp.2025.113919 -- the
compound-cell graph construction and the finite-volume graph trunk implemented here
follow that work.
"""


def create_transfvgn(config):
    """
    Factory function for TransFVGN model.

    Args:
        config: Configuration object or dict with attributes:
            - message_passing_num: Number of message passing layers
            - node_input_size: Input size for nodes
            - edge_input_size: Input size for edges
            - node_output_size: Output size for nodes
            - hidden_size: Hidden layer size
            - dropout: Whether to use dropout
            - attn_processor_num: Number of attention processors
            - patch_slice_num: Number of slice tokens in patch cross-attention encoder
            - boundary_input_dim: Input dimension for boundary patch features

    Returns:
        TransFVGN model instance
    """
    from .TransFVGN_v2 import Simulator as TransFVGN

    return TransFVGN(
        message_passing_num=config.message_passing_num,
        node_input_size=config.node_input_size,
        edge_input_size=config.edge_input_size,
        node_output_size=config.node_output_size,
        drop_out=config.dropout,
        hidden_size=config.hidden_size,
        params=config,  # Pass the entire config as params for attn_processor_num
        # Patch cross-attention encoder parameters
        patch_slice_num=getattr(config, 'patch_slice_num', 32),
        boundary_input_dim=getattr(config, 'boundary_input_dim', 11),
    )


def create_transfvgnplus(config):
    """
    Factory function for TransFVGNplus model.

    Args:
        config: Configuration object or dict with attributes:
            - message_passing_num: Number of message passing layers
            - node_input_size: Input size for nodes
            - edge_input_size: Input size for edges
            - node_output_size: Output size for nodes
            - hidden_size: Hidden layer size
            - dropout: Whether to use dropout
            - attn_processor_num: Number of attention processors
            - patch_slice_num: Number of slice tokens in patch cross-attention encoder
            - boundary_input_dim: Input dimension for boundary patch features

    Returns:
        TransFVGNplus model instance
    """
    try:
        from .TransFVGNplus import Simulator as TransFVGNplus
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "TransFVGNplus is not available in the current workspace. "
            "Use model_name=TransFVGN_v2 or restore the TransFVGNplus dependencies."
        ) from exc

    return TransFVGNplus(
        message_passing_num=config.message_passing_num,
        node_input_size=config.node_input_size,
        edge_input_size=config.edge_input_size,
        node_output_size=config.node_output_size,
        drop_out=config.dropout,
        hidden_size=config.hidden_size,
        params=config,  # Pass the entire config as params for attn_processor_num
        # Patch cross-attention encoder parameters
        patch_slice_num=getattr(config, 'patch_slice_num', 32),
        boundary_input_dim=getattr(config, 'boundary_input_dim', 11),
    )


def create_textcond_transfvgn(config):
    """
    Factory function for text-conditioned TransFVGN model.
    """
    from .TextCondTransFVGN_v1 import Simulator as TextCondTransFVGN

    return TextCondTransFVGN(
        message_passing_num=config.message_passing_num,
        node_output_size=config.node_output_size,
        drop_out=config.dropout,
        hidden_size=config.hidden_size,
        params=config,
        patch_slice_num=getattr(config, 'patch_slice_num', 32),
    )


def create_transfvgn_ss(config):
    """Factory for the single-stream TransFVGN control arm (`DNE-SS-TransFVGN`).

    Deliberately mirrors create_textcond_transfvgn's argument list: the two arms must be built from
    the same trunk hyper-parameters, differing only in that this one has no boundary K/V stream.
    """
    from .TransFVGN_SS_v1 import Simulator as TransFVGN_SS

    return TransFVGN_SS(
        message_passing_num=config.message_passing_num,
        node_output_size=config.node_output_size,
        drop_out=config.dropout,
        hidden_size=config.hidden_size,
        params=config,
        patch_slice_num=getattr(config, 'patch_slice_num', 32),
    )


def create_globalcond_transfvgn(config):
    """Factory function for global-conditioned TransFVGN model (method D)."""
    from .GlobalCondTransFVGN_v1 import Simulator as GlobalCondTransFVGN

    return GlobalCondTransFVGN(
        message_passing_num=config.message_passing_num,
        node_output_size=config.node_output_size,
        drop_out=config.dropout,
        hidden_size=config.hidden_size,
        params=config,
    )
