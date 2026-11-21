"""TDD: empty/processor patches must get matching field types.

Bug: createBaffles produces an 'oldInternalFaces' patch with type 'empty'.
The field generator assigns zeroGradient/fixedValue instead of 'empty',
causing solver crash: "inconsistent patch and patchField types for
patch type empty and patchField type zeroGradient".

The fix: _is_cyclic_patch_type (or a broader check) must also match
'empty' and 'processor' patch types, so the field writer emits
matching patchField types for these special patches.
"""
import inspect

from backend.aircooling.runner.fields import FieldGenerator


def test_empty_patch_type_recognized():
    """empty patch type must be treated as a special patch (like cyclic)."""
    assert FieldGenerator._is_cyclic_patch_type("empty") or \
           hasattr(FieldGenerator, '_is_special_patch_type') and \
           FieldGenerator._is_special_patch_type("empty"), \
        "empty patch type not recognized as special — will get wrong field BC"


def test_processor_patch_type_recognized():
    """processor patch type must be treated as a special patch."""
    assert FieldGenerator._is_cyclic_patch_type("processor") or \
           hasattr(FieldGenerator, '_is_special_patch_type') and \
           FieldGenerator._is_special_patch_type("processor"), \
        "processor patch type not recognized as special"


def test_all_field_writers_handle_special_patches():
    """Every _write_*_field function must call _is_cyclic_patch_type."""
    field_writers = [
        FieldGenerator._write_u_field,
        FieldGenerator._write_t_field,
        FieldGenerator._write_p_field,
        FieldGenerator._write_p_rgh_field,
        FieldGenerator._write_turbulence_field,
        FieldGenerator._write_simple_scalar_field,
    ]
    for fn in field_writers:
        source = inspect.getsource(fn)
        assert "_is_cyclic_patch_type" in source, (
            f"{fn.__name__} does not call _is_cyclic_patch_type — "
            f"empty/processor patches will get wrong BC and crash the solver"
        )
