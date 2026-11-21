from typing import Any
from dataclasses import is_dataclass, fields
import copy

# omegaconf/DictConfig support stripped: we only ever pass plain dataclass configs.


def shallow_asdict(obj: Any) -> dict:
    if is_dataclass(obj):
        return {f.name: getattr(obj, f.name) for f in fields(obj)}
    raise TypeError(f"Unsupported type for shallow_asdict: {type(obj)}")


def safe_replace(obj: Any, **kwargs) -> Any:
    if is_dataclass(obj):
        field_names = {f.name for f in fields(obj)}
        new = copy.deepcopy(obj)
        for key, value in kwargs.items():
            if key in field_names:
                setattr(new, key, value)
        return new
    raise TypeError(f"Unsupported type for safe_replace: {type(obj)}")
