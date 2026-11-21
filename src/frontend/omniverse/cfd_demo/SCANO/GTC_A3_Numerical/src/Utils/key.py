from typing import Union, List, Optional
from .normalization import StandardDeviationNormalizer


class Key(object):
    """
    Class describing keys used for physical quantities in graph neural networks.

    Each key represents a physical quantity with its name, dimension, and optional normalizer.

    Parameters
    ----------
    name : str
        String used to refer to the physical quantity (e.g. 'u', 'v', 'w', 'p', 'T').
    dimension : int=1
        Dimension of the physical quantity (e.g., U=3 for velocity, p=1 for pressure, T=1 for temperature).
    normalizer : Optional[StandardDeviationNormalizer]=None
        Normalizer for this physical quantity. If None, no normalization is applied.
    """

    def __init__(self, name: str, dimension: int = 1, normalizer: Optional[StandardDeviationNormalizer] = None):
        super(Key, self).__init__()
        self.name = name
        self.dimension = dimension
        self.normalizer = normalizer

    @property
    def size(self):
        """Alias for dimension to maintain backward compatibility"""
        return self.dimension

    @size.setter
    def size(self, value):
        """Setter for size to maintain backward compatibility"""
        self.dimension = value

    @classmethod
    def from_str(cls, name: str):
        """Create a Key from a string name with dimension=1"""
        return cls(name, dimension=1)

    @classmethod
    def from_tuple(cls, name_dimension_tuple):
        """Create a Key from a tuple (name, dimension)"""
        if len(name_dimension_tuple) == 2:
            name, dimension = name_dimension_tuple
            return cls(name, dimension=dimension)
        else:
            raise ValueError("Tuple must contain exactly 2 elements: (name, dimension)")

    @classmethod
    def convert(cls, name_or_tuple):
        """Convert string, tuple, or Key object to Key"""
        if isinstance(name_or_tuple, str):
            key = cls.from_str(name_or_tuple)
        elif isinstance(name_or_tuple, tuple):
            key = cls.from_tuple(name_or_tuple)
        elif isinstance(name_or_tuple, cls):
            key = name_or_tuple
        else:
            raise ValueError("can only convert string, tuple, or Key to Key")
        return key

    @staticmethod
    def convert_list(ls):
        """Convert a list of strings/tuples/Keys to a list of Keys"""
        keys = []
        for name_or_tuple in ls:
            keys.append(Key.convert(name_or_tuple))
        return keys

    @staticmethod
    def convert_config(key_cfg: Union[List, str]):
        """Converts a config input/output key string/list into a list of Keys

        Parameters
        ----------
        key_cfg : Union[List, str]
            Config list or string

        Returns
        -------
        List[Key]
            List of keys generated

        Examples
        --------
        Single key with dimension 1:
        >>> Key.convert_config("u")  # [Key("u", dimension=1)]

        Single key with specified dimension:
        >>> Key.convert_config(["u", 3])  # [Key("u", dimension=3)]

        Multiple keys:
        >>> Key.convert_config(["u", "v", "w"])  # [Key("u"), Key("v"), Key("w")]

        Multiple keys with dimensions:
        >>> Key.convert_config([["u", 3], ["p", 1]])  # [Key("u", 3), Key("p", 1)]
        """
        # Single key name
        if isinstance(key_cfg, str):
            keys = [Key.from_str(key_cfg.strip())]
        # Multiple keys
        elif isinstance(key_cfg, list):
            keys = []
            for cfg_obj in key_cfg:
                if isinstance(cfg_obj, str):
                    key = Key.from_str(cfg_obj)
                    keys.append(key)
                elif isinstance(cfg_obj, int) and len(keys) > 0:
                    # Previous key gets this dimension
                    keys[-1].dimension = cfg_obj
                elif isinstance(cfg_obj, list) and len(cfg_obj) >= 2:
                    # [name, dimension] format
                    key_name = cfg_obj[0]
                    key_dimension = int(cfg_obj[1]) if len(cfg_obj) > 1 else 1
                    key = Key(key_name, dimension=key_dimension)
                    keys.append(key)
                elif isinstance(cfg_obj, Key):
                    # Already a Key object
                    keys.append(cfg_obj)
                else:
                    raise ValueError(f"Invalid key parameter in config: {cfg_obj}")
        else:
            raise ValueError(f"Invalid key config format: {key_cfg}")
        return keys

    def normalize(self, x):
        """Apply normalization if normalizer is available"""
        if self.normalizer is not None:
            # Ensure normalizer is on the same device as input tensor
            if hasattr(x, 'device') and hasattr(self.normalizer, 'to'):
                self.normalizer = self.normalizer.to(x.device)
            return self.normalizer.normalize(x)
        return x

    def denormalize(self, x):
        """Apply denormalization if normalizer is available"""
        if self.normalizer is not None:
            return self.normalizer.denormalize(x)
        return x

    def has_normalizer(self):
        """Check if this key has a normalizer"""
        return self.normalizer is not None

    def __str__(self):
        return f"{self.name}({self.dimension})"

    def __repr__(self):
        normalizer_info = "with normalizer" if self.has_normalizer() else "no normalizer"
        return f"Key(name='{self.name}', dimension={self.dimension}, {normalizer_info})"

    def __eq__(self, obj):
        return isinstance(obj, Key) and self.name == obj.name and self.dimension == obj.dimension

    def __lt__(self, obj):
        assert isinstance(obj, Key)
        return self.name < obj.name

    def __gt__(self, obj):
        assert isinstance(obj, Key)
        return self.name > obj.name

    def __hash__(self):
        return hash((self.name, self.dimension))


def get_total_dimension(key_list):
    """Calculate total dimension from a list of keys"""
    total_dim = 0
    for key in key_list:
        total_dim += key.dimension
    return total_dim


def create_key_with_normalizer(name: str, dimension: int, normalizer: Optional[StandardDeviationNormalizer] = None):
    """Convenience function to create a Key with normalizer"""
    return Key(name=name, dimension=dimension, normalizer=normalizer)