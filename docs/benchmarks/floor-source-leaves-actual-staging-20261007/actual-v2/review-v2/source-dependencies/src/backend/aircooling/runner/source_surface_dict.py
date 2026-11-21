"""Render checked .cfdrun source-region identities for the dcCart dictionary."""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Mapping
import re

from backend.aircooling.runner.cfdrun_schema import GeometryExport


_FOAM_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
_SOURCE_UID = re.compile(r"^[A-Za-z0-9_./:-]+$")


def render_source_surfaces_dict(
    geometry_export: dict,
    mesh_stl_solids: Mapping[str, Collection[str]] | None = None,
) -> str:
    """Return an optional OpenFOAM dictionary block; never infer wall pairs.

    The containing .cfdrun was already validated against the project and STL
    hashes by the caller. Reject unsafe names rather than sanitizing away the
    stable identity that must survive into the native mesher.
    """
    geometry = GeometryExport.model_validate(geometry_export)
    if not geometry.sourceSurfaces:
        return ""
    actual_by_solid: dict[str, list[str]] = defaultdict(list)
    if mesh_stl_solids is not None:
        for filename, solids in mesh_stl_solids.items():
            for solid in solids:
                actual_by_solid[solid].append(filename)
    by_file: dict[str, list] = defaultdict(list)
    seen_targets: dict[tuple[str, str], str] = {}
    for source_key, binding in geometry.sourceSurfaces.items():
        filename = geometry.stlFiles[binding.stlFileId].path
        if mesh_stl_solids is not None:
            matches = actual_by_solid.get(binding.solidName, [])
            if len(matches) != 1:
                raise ValueError(f"source STL solid {binding.solidName!r} needs one unique mesh-stage file; found {matches}")
            filename = matches[0]
        if not isinstance(filename, str) or not _FOAM_NAME.fullmatch(filename):
            raise ValueError(f"unsafe source STL filename {filename!r}")
        if not _FOAM_NAME.fullmatch(binding.solidName):
            raise ValueError(f"unsafe source STL solidName {binding.solidName!r}")
        for field, value in (("sourceObjectUid", binding.sourceObjectUid),
                             ("sourceFaceUid", binding.sourceFaceUid)):
            if not _SOURCE_UID.fullmatch(value):
                raise ValueError(f"unsafe {field} for {filename}:{binding.solidName}")
        target = (filename, binding.solidName)
        previous = seen_targets.setdefault(target, source_key)
        if previous != source_key:
            raise ValueError(
                f"duplicate mesh-stage source region {filename}:{binding.solidName} "
                f"from {previous} and {source_key}"
            )
        by_file[filename].append(binding)
    lines = ["dcdtuniSourceSurfaces", "{"]
    for filename in sorted(by_file):
        lines.extend((f"    {filename}", "    {"))
        for binding in sorted(by_file[filename], key=lambda value: value.solidName):
            lines.extend((f"        {binding.solidName}", "        {",
                          f'            sourceObjectUid "{binding.sourceObjectUid}";',
                          f'            sourceFaceUid "{binding.sourceFaceUid}";',
                          f"            representation {binding.representation};",
                          "        }"))
        lines.append("    }")
    lines.extend(("}", ""))
    return "\n".join(lines)
