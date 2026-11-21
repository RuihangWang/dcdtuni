"""Owned cfdrun input bytes for explicit in-memory consumers.

The logical artifact is metadata; this class never reads it. Existing file
entry points remain explicit and are not automatically promoted to snapshots.
"""
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Optional

MAX_CFDRUN_SNAPSHOT_BYTES = 64 * 1024 * 1024


def _refuse_constant(value: str):
    raise ValueError(f'nonfinite cfdrun JSON value: {value}')


@dataclass(frozen=True, slots=True)
class CfdrunSnapshot:
    """Immutable source with a case-bound logical replay artifact identity.

    Only bytes are authoritative. Each parse returns a new tree, retaining
    extensions without sharing caller-owned mutable dictionaries or buffers.
    Physical schema/STL validation still belongs to existing consumers.
    """
    payload: bytes
    case_dir: Path
    logical_path: Path
    expected_sha256: Optional[str] = field(default=None, repr=False)
    stl_batch_sha256: Optional[str] = field(default=None, kw_only=True)
    sha256: str = field(init=False)

    def __post_init__(self):
        if not isinstance(self.payload, (bytes, bytearray, memoryview)):
            raise TypeError('cfdrun snapshot payload must be bytes')
        size = self.payload.nbytes if isinstance(self.payload, memoryview) else len(self.payload)
        if not 0 < size <= MAX_CFDRUN_SNAPSHOT_BYTES:
            raise ValueError('cfdrun snapshot byte capacity exceeded or payload empty')
        owned = bytes(self.payload)
        if not 0 < len(owned) <= MAX_CFDRUN_SNAPSHOT_BYTES:
            raise ValueError('cfdrun snapshot byte capacity exceeded after copy or payload empty')
        actual = hashlib.sha256(owned).hexdigest()
        if self.expected_sha256 is not None:
            if (not isinstance(self.expected_sha256, str)
                    or re.fullmatch(r'[0-9a-f]{64}', self.expected_sha256) is None):
                raise ValueError('expected cfdrun SHA-256 must be 64 lowercase hex characters')
            if actual != self.expected_sha256:
                raise ValueError('cfdrun snapshot SHA-256 does not match expected input')
        if self.stl_batch_sha256 is not None and (
                not isinstance(self.stl_batch_sha256, str)
                or re.fullmatch(r'[0-9a-f]{64}', self.stl_batch_sha256) is None):
            raise ValueError('STL batch SHA-256 must be 64 lowercase hex characters')
        case = Path(self.case_dir).expanduser().resolve()
        logical = Path(self.logical_path).expanduser().resolve()
        # The normal server chooses/sanitizes the filename. This guard binds
        # the snapshot namespace and also refuses escaping artifact symlinks.
        self._validate_logical_artifact(case, logical)
        object.__setattr__(self, 'payload', owned)
        object.__setattr__(self, 'case_dir', case)
        object.__setattr__(self, 'logical_path', logical)
        object.__setattr__(self, 'sha256', actual)
        self.parse()  # bounded valid UTF-8 JSON/schema identity; no physical derivation.

    def _validate_logical_artifact(self, case: Path, logical: Path) -> None:
        if logical.suffix.lower() != '.cfdrun' or logical.parent != case / 'model':
            raise ValueError('cfdrun snapshot logical artifact must be in its case/model directory')

    def read_bytes(self) -> bytes:
        return self.payload

    def parse(self) -> dict[str, Any]:
        raw = json.loads(self.payload.decode('utf-8'), parse_constant=_refuse_constant)
        if not isinstance(raw, dict):
            raise ValueError('cfdrun snapshot must contain a JSON object')
        version = raw.get('schemaVersion')
        if type(version) is not int or version not in (2, 3, 4):
            raise ValueError(f'unsupported cfdrun snapshot schemaVersion {version!r}; re-export')
        return raw

    def require_context(self, case_dir, cfdrun_path=None) -> None:
        if Path(case_dir).expanduser().resolve() != self.case_dir:
            raise ValueError('cfdrun snapshot belongs to another case')
        if cfdrun_path is not None and Path(cfdrun_path).expanduser().resolve() != self.logical_path:
            raise ValueError('cfdrun snapshot logical artifact identity does not match')


def _same_json_identity(left: Any, right: Any) -> bool:
    """Preserve JSON value types and binary float identity in provenance."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(
            _same_json_identity(value, right[key]) for key, value in left.items())
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _same_json_identity(a, b) for a, b in zip(left, right))
    if isinstance(left, float):
        return left.hex() == right.hex()
    return left == right


@dataclass(frozen=True, slots=True, kw_only=True)
class HybridCfdrunSnapshot(CfdrunSnapshot):
    """Explicit derived heat handoff; this type does not authorize mesh quality.

    Only the normal verified hybrid finalizer may supply physics. This class
    binds its result bytes to the original source and known replay namespace.
    """
    parent_input: CfdrunSnapshot = field(repr=False)
    mesh_sha256: str

    def _validate_logical_artifact(self, case: Path, logical: Path) -> None:
        if type(self.parent_input) is not CfdrunSnapshot:
            raise ValueError('hybrid snapshot requires the original source parent')
        self.parent_input.require_context(case)
        if (not isinstance(self.mesh_sha256, str)
                or re.fullmatch(r'[0-9a-f]{64}', self.mesh_sha256) is None):
            raise ValueError('hybrid snapshot requires a verified mesh SHA-256')
        expected = case / f'hybrid-mesh-{self.mesh_sha256[:16]}.cfdrun'
        if logical != expected:
            raise ValueError('hybrid snapshot logical artifact must match its mesh replay identity')

    def __post_init__(self):
        CfdrunSnapshot.__post_init__(self)
        if self.stl_batch_sha256 != self.parent_input.stl_batch_sha256:
            raise ValueError('hybrid snapshot does not preserve parent STL batch identity')
        original, derived = self.parent_input.parse(), self.parse()
        if any(not _same_json_identity(derived.get(key), original.get(key))
               for key in ('schemaVersion', 'runId', 'sourceProject')):
            raise ValueError('hybrid snapshot does not preserve its parent source identity')
        if (not isinstance(original.get('runId'), str) or not original['runId'].strip()
                or not isinstance(original.get('sourceProject'), dict)
                or not isinstance(original['sourceProject'].get('contentHash'), str)
                or re.fullmatch(r'[0-9a-fA-F]{64}', original['sourceProject']['contentHash']) is None):
            raise ValueError('hybrid snapshot parent lacks the required source identity')

    @property
    def parent_sha256(self) -> str:
        return self.parent_input.sha256
