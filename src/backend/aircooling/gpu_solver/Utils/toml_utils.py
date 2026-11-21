"""
Utility functions for converting tomlkit objects to native Python types.

This ensures torch.compile compatibility by avoiding tomlkit's custom types
(Integer, Float, String, etc.) which can cause graph breaks and warnings.
"""

from typing import Any, Dict, List, Union


def toml_to_native(obj: Any) -> Any:
    """
    Recursively convert tomlkit objects to native Python types.

    Args:
        obj: Object to convert (can be tomlkit types, dict, list, or primitive)

    Returns:
        Converted object with only native Python types

    Example:
        >>> from tomlkit import parse
        >>> doc = parse('x = 42\\ny = "hello"\\nz = [1, 2, 3]')
        >>> native = toml_to_native(doc)
        >>> type(native['x'])  # int, not tomlkit.items.Integer
        <class 'int'>
    """
    # Handle None
    if obj is None:
        return None

    # Get the actual type name
    obj_type = type(obj).__name__

    # Convert tomlkit primitive types to native Python types
    if obj_type in ('Integer', 'Bool'):
        return int(obj)
    elif obj_type == 'Float':
        return float(obj)
    elif obj_type in ('String', 'Key'):
        return str(obj)
    elif obj_type == 'Bool':
        return bool(obj)
    elif obj_type == 'Date':
        # Convert tomlkit.Date to string representation
        return str(obj)
    elif obj_type == 'DateTime':
        # Convert tomlkit.DateTime to string representation
        return str(obj)
    elif obj_type == 'Time':
        # Convert tomlkit.Time to string representation
        return str(obj)

    # Handle collections (recursively)
    elif isinstance(obj, dict):
        # Convert dict-like objects (including tomlkit.Document and Table)
        return {toml_to_native(k): toml_to_native(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        # Convert list-like objects (including tomlkit.Array)
        converted = [toml_to_native(item) for item in obj]
        return tuple(converted) if isinstance(obj, tuple) else converted

    # Already a native type, return as-is
    elif isinstance(obj, (int, float, str, bool)):
        return obj

    # Fallback: try to convert to appropriate native type
    else:
        # Try conversion based on value
        try:
            # Check if it's a number
            if hasattr(obj, '__int__'):
                return int(obj)
            elif hasattr(obj, '__float__'):
                return float(obj)
            elif hasattr(obj, '__str__'):
                return str(obj)
        except (ValueError, TypeError):
            pass

        # If all else fails, return as-is and warn
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"Could not convert tomlkit object of type {obj_type} to native type, returning as-is")
        return obj


def load_toml_native(file_path: str) -> Dict[str, Any]:
    """
    Load TOML file and convert all objects to native Python types.

    This is a drop-in replacement for tomlkit.load() that ensures
    torch.compile compatibility.

    Args:
        file_path: Path to TOML file

    Returns:
        Dictionary with native Python types

    Example:
        >>> config = load_toml_native('config.toml')
        >>> type(config['value'])  # Native Python int/float/str
        <class 'int'>
    """
    import tomlkit

    with open(file_path, 'r', encoding='utf-8') as f:
        doc = tomlkit.load(f)

    return toml_to_native(doc)


def parse_toml_native(toml_string: str) -> Dict[str, Any]:
    """
    Parse TOML string and convert all objects to native Python types.

    This is a drop-in replacement for tomlkit.parse() that ensures
    torch.compile compatibility.

    Args:
        toml_string: TOML content as string

    Returns:
        Dictionary with native Python types
    """
    import tomlkit

    doc = tomlkit.parse(toml_string)
    return toml_to_native(doc)
