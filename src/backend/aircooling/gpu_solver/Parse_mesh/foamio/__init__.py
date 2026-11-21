"""Fast numpy readers/writers for OpenFOAM files (dictionaries, volFields, polyMesh), ASCII and binary.

Used by the dctwin case import (``Parse_mesh/dctwin_import.py``) and the OpenFOAM writer
(``Post_process/to_foam.py``). Geometry follows OpenFOAM's own definitions (checked against
``postProcess -func writeCellCentres/writeCellVolumes`` to 1e-17 on the cavity tutorial).
"""
from .foam_dict import (FoamDict, FoamString, DimensionSet, FoamHeader, read_foam_file, read_foam_list,
                        parse_foam_bytes, write_foam_file, format_value)
from .polymesh import Patch, Zone, PolyMesh, read_polymesh
from .fields import VolField, read_vol_field, write_vol_field, expand_value
