import torch
from torch.nn import Module
from torch import tensor, zeros, sum, max, sqrt

class Z_ScoreNormalizer(Module):
    def __init__(self, size, max_accumulations=10**7, epsilon=1e-8, device=None):
        """
        Online normalization module

        size: feature dimension
        max_accumulation: maximum number of batches
        epsilon: std cutoff for constant variable
        device: pytorch device
        """

        super(Z_ScoreNormalizer, self).__init__()

        self.max_accumulations = max_accumulations
        self.epsilon = epsilon

        # self.register_buffer('acc_count', tensor(0, dtype=float, device=device))
        # self.register_buffer('num_accumulations', tensor(0, dtype=float, device=device))
        # self.register_buffer('acc_sum', zeros(size, dtype=float, device=device))
        # self.register_buffer('acc_sum_squared', zeros(size, dtype=float, device=device))

        self.register_buffer("acc_count", tensor(1.0, dtype=float, device=device))
        self.register_buffer(
            "num_accumulations", tensor(1.0, dtype=float, device=device)
        )
        self.register_buffer("acc_sum", zeros(size, dtype=float, device=device))
        self.register_buffer("acc_sum_squared", zeros(size, dtype=float, device=device))

    def forward(self, batched_data, accumulate=True):
        """
        Updates mean/standard deviation and normalizes input data

        batched_data: batch of data
        accumulate: if True, update accumulation statistics
        """
        if accumulate and self.num_accumulations < self.max_accumulations:
            self._accumulate(batched_data)

        return (batched_data - self._mean().to(batched_data.device)) / self._std().to(
            batched_data.device
        )

    def inverse(self, normalized_batch_data):
        """
        Unnormalizes input data
        """

        return normalized_batch_data * self._std().to(
            normalized_batch_data.device
        ) + self._mean().to(normalized_batch_data.device)

    def _accumulate(self, batched_data):
        """
        Accumulates statistics for mean/standard deviation computation
        """
        count = tensor(batched_data.shape[0], dtype=torch.float32)
        data_sum = sum(batched_data, dim=0)
        squared_data_sum = sum(batched_data**2, dim=0)

        self.acc_sum += data_sum.to(self.acc_sum.device)
        self.acc_sum_squared += squared_data_sum.to(self.acc_sum_squared.device)
        self.acc_count += count.to(self.acc_count.device)
        self.num_accumulations += 1

    def _mean(self):
        """
        Returns accumulated mean
        """
        safe_count = max(self.acc_count, tensor(1.0, dtype=torch.float32))

        return self.acc_sum / safe_count

    def _std(self):
        """
        Returns accumulated standard deviation
        """
        safe_count = max(self.acc_count, tensor(1.0, dtype=torch.float32))
        std = sqrt(self.acc_sum_squared / safe_count - self._mean() ** 2)
        std[(torch.isclose(std, tensor(0.0, dtype=std.dtype, device=std.device), rtol=1e-4, atol=1e-6) | (~torch.isfinite(std)))] = 1.0

        return std


class MinMaxNormalizer(Module):
    def __init__(self, size, max_accumulations=10**7, epsilon=1e-8, device=None):
        """
        Online min-max normalization module

        size: feature dimension
        max_accumulations: maximum number of batches to accumulate statistics
        epsilon: small value to avoid division by zero when max == min
        device: pytorch device
        """

        super(MinMaxNormalizer, self).__init__()

        self.max_accumulations = max_accumulations
        self.epsilon = epsilon

        # Initialize with extreme values for proper accumulation
        # acc_min starts with positive infinity, acc_max starts with negative infinity
        self.register_buffer("num_accumulations", torch.tensor(0.0, dtype=torch.int64, device=device))
        self.register_buffer("acc_min", torch.full((size,), float('inf'), dtype=torch.float, device=device))
        self.register_buffer("acc_max", torch.full((size,), float('-inf'), dtype=torch.float, device=device))

    def forward(self, batched_data, accumulate=True):
        """
        Updates min/max statistics and normalizes input data

        batched_data: batch of data with shape [N, size]
        accumulate: if True, update accumulation statistics
        """
        if accumulate and self.num_accumulations < self.max_accumulations:
            self._accumulate(batched_data)

        return self._normalize(batched_data)

    def inverse(self, normalized_batch_data):
        """
        Unnormalizes input data from [0, 1] back to original scale
        """
        min_vals = self._min().to(normalized_batch_data.device)
        max_vals = self._max().to(normalized_batch_data.device)
        range_vals = max_vals - min_vals

        return normalized_batch_data * range_vals + min_vals

    def _accumulate(self, batched_data):
        """
        Accumulates min/max statistics across dim=1 (feature dimension)
        """
        # Compute min and max along batch dimension (dim=0)
        batch_min, _ = torch.min(batched_data, dim=0)
        batch_max, _ = torch.max(batched_data, dim=0)

        # Update accumulated min/max
        self.acc_min = torch.min(self.acc_min, batch_min.to(self.acc_min.device))
        self.acc_max = torch.max(self.acc_max, batch_max.to(self.acc_max.device))
        self.num_accumulations += 1

    def _min(self):
        """
        Returns accumulated minimum values
        """
        return self.acc_min

    def _max(self):
        """
        Returns accumulated maximum values
        """
        return self.acc_max

    def _normalize(self, batched_data):
        """
        Normalizes data to [0, 1] range using accumulated min/max
        """
        min_vals = self._min().to(batched_data.device)
        max_vals = self._max().to(batched_data.device)
        range_vals = max_vals - min_vals

        # Avoid division by zero when min == max
        mask_range_vals = torch.isclose(range_vals, torch.tensor(0.0, device=range_vals.device), rtol=1e-4, atol=1e-6)
        range_vals[mask_range_vals] = 1.
        
        return (batched_data - min_vals) / range_vals


class StandardDeviationNormalizer(Module):
    """
    Normalizes data using mean and standard deviation.
    """
    def __init__(
        self,
        mean: torch.Tensor,
        std: torch.Tensor
    ):
        super(StandardDeviationNormalizer, self).__init__()
        self.register_buffer('mean', mean)
        self.register_buffer('std', std)
    
    def normalize(self, x: torch.Tensor):
        return (x - self.mean) / self.std
    
    def denormalize(self, x: torch.Tensor):
        return x * self.std + self.mean


class KeyBasedNormalizer(Module):
    """
    Normalizer that handles a list of Keys, applying their individual normalizers in a vectorized manner.
    """
    def __init__(self, keys: list):
        super(KeyBasedNormalizer, self).__init__()
        self.keys = keys
        
        # Aggregate mean and std from all keys
        means = []
        stds = []
        
        for key in self.keys:
            if hasattr(key, 'normalizer') and key.normalizer is not None:
                # Extract mean and std from the key's normalizer
                m = key.normalizer.mean
                s = key.normalizer.std
                
                # Ensure they are at least 1D
                if m.dim() == 0: m = m.unsqueeze(0)
                if s.dim() == 0: s = s.unsqueeze(0)
                
                means.append(m)
                stds.append(s)
            else:
                # Default to identity normalization (mean=0, std=1)
                # Use dimension from key to create correct shape
                dim = key.dimension if hasattr(key, 'dimension') else 1
                means.append(torch.zeros(dim))
                stds.append(torch.ones(dim))
        
        # Stack into unified tensors [1, total_channels]
        # We use register_buffer to ensure they are saved/loaded and moved to device
        self.register_buffer('mean', torch.cat(means).unsqueeze(0))
        self.register_buffer('std', torch.cat(stds).unsqueeze(0))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        Apply normalization to input tensor x.
        """
        if x is None:
            return None
            
        # Vectorized normalization
        return (x - self.mean) / self.std

    def inverse(self, x: torch.Tensor) -> torch.Tensor:
        """
        Apply denormalization to input tensor x.
        """
        if x is None:
            return None
            
        # Vectorized denormalization
        return x * self.std + self.mean


class PatchNumericalNormalizer(Module):
    """Z-score normalizer using patch-specific boundary statistics."""

    def __init__(self, patch_stats: dict):
        super().__init__()
        self.register_buffer("mean", torch.tensor(patch_stats["mean"], dtype=torch.float32).unsqueeze(0))
        self.register_buffer("std", torch.tensor(patch_stats["std"], dtype=torch.float32).unsqueeze(0))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        if x is None:
            return None
        return (x - self.mean) / self.std

    def inverse(self, x: torch.Tensor) -> torch.Tensor:
        if x is None:
            return None
        return x * self.std + self.mean

    @classmethod
    def from_json(cls, json_path: str) -> "PatchNumericalNormalizer":
        import json
        with open(json_path, "r") as f:
            stats = json.load(f)
        return cls(stats)
