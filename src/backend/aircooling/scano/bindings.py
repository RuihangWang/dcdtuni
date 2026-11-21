"""The boundary bindings of a cfdrun manifest (what every adapter reads the server / ACU parameters from)."""
from __future__ import annotations

from typing import Dict


def bindings_of(cfdrun: Dict) -> Dict[str, Dict]:
    """{patch name: binding} of a cfdrun manifest's boundaryBindings."""
    bb = cfdrun.get('boundaryBindings') or {}
    return {b['patchName']: b for b in (bb.values() if isinstance(bb, dict) else bb)}
