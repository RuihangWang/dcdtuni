import torch
import torch.nn as nn
from torch_geometric.nn import global_add_pool


class LpLoss(nn.Module):
    """
    Lp Loss function for data-driven training.

    This loss function computes the Lp norm between predictions and targets,
    with support for both absolute and relative error computation.
    """

    def __init__(self, size_average=True, reduction=True):
        super(LpLoss, self).__init__()

        self.reduction = reduction
        self.size_average = size_average

    def rel(self, x, y, batch):
        """
        Compute relative L2 loss per-graph per-channel (vectorized).

        Args:
            x: Prediction tensor [N, C]
            y: Target tensor [N, C]
            batch: Batch indices [N]

        Returns:
            Scalar loss value (average relative L2 across graphs and channels)
        """
        # Compute squared differences and squared targets [N, C]
        diff_sq = torch.pow(x - y, 2)
        y_sq = torch.pow(y, 2)

        # Sum per graph for each channel [num_graphs, C]
        diff_norm_sq = global_add_pool(diff_sq, batch)
        y_norm_sq = global_add_pool(y_sq, batch)

        # Compute per-graph per-channel relative L2 [num_graphs, C]
        rel_l2 = (torch.sqrt(diff_norm_sq + 1e-8) / torch.sqrt(y_norm_sq + 1e-8)).sum(dim=-1,keepdim=True)

        # Average across graphs and channels
        if self.reduction:
            if self.size_average:
                return torch.mean(rel_l2)
            else:
                return torch.sum(rel_l2)

        return rel_l2

    def rel_batch(self, x, y, batch):
        """
        Legacy method for backward compatibility.
        Computes relative L2 aggregated across all channels.
        """
        diff = x - y

        batch_diff_norm = torch.sqrt(global_add_pool(torch.pow(diff, 2), batch) + 1e-8)

        y_norms = torch.sqrt(global_add_pool(torch.pow(y, 2), batch) + 1e-8)

        rel_l2 = batch_diff_norm / y_norms

        if rel_l2.numel() == 0:
            return torch.tensor(0.0, device=x.device)

        if self.reduction:
            if self.size_average:
                return torch.mean(rel_l2)
            else:
                return torch.sum(rel_l2)

        return rel_l2

    def __call__(self, x, y, batch):
        """
        Call method for backward compatibility.

        Args:
            x: Either data object with prediction/target or prediction tensor
            y: Target tensor (optional if x contains both prediction and target)
            batch: Batch indices (optional, for per-graph computation)

        Returns:
            torch.Tensor: Computed loss value
        """
        # Data object with prediction and target
        return self.rel(x, y, batch)
