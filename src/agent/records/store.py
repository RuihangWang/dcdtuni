"""RunStore: immutable, validated files under one injected root.

The write discipline of the frozen ``harness/artifacts.py`` (CaseArtifacts),
with the root as a parameter instead of the repository:

- every path is relative to ``root``; ``..``, an absolute path outside the
  root and a symbolic link anywhere below the root are refused, even a link
  that points back inside;
- directories are walked with ``O_NOFOLLOW`` from a descriptor of the root, so
  a link planted between the check and the write is refused too;
- files are created with ``O_EXCL``: nothing written is ever replaced;
- JSON is sorted, finite (``allow_nan=False``) and checked before the first
  file of a record is created.

Projects are validated by an injected ``validator(project) -> report`` that
raises ``ValueError`` for an invalid document. The default is
``agent.tools.project.validate_project``, which goes through ``agent.scripted_api``
(the trunk keeps no Node lookup or schema copy of its own).

``RunStore.for_case(case_id)`` and ``RunStore.create_case`` keep the old
``case/agent/<case-id>/`` layout as the default root; any other root works the
same way, so an external repository can keep its records where it likes.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Union

try:  # pragma: no cover - zoneinfo ships with Python >= 3.9; tzdata may be missing
    from zoneinfo import ZoneInfo
    SGT = ZoneInfo("Asia/Singapore")
except Exception:  # pragma: no cover
    from datetime import timedelta, timezone
    SGT = timezone(timedelta(hours=8), "SGT")

PathLike = Union[str, os.PathLike]
Validator = Callable[[Dict[str, Any]], Dict[str, Any]]

#: src/agent/records/store.py -> repository root (only used for default roots).
REPO_ROOT = Path(__file__).resolve().parents[3]
CASE_AGENT = Path("case") / "agent"
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$")
_RUN = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{6}_SGT__[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$")
CANDIDATE_FORMAT = "agent-project-artifacts/v1"
SELECTION_FORMAT = "agent-final-selection/v1"


def timestamp_sgt() -> str:
    return datetime.now(SGT).isoformat(timespec="seconds")


def checked_id(value: str, kind: str = "id") -> str:
    """One safe path component: letters, digits, ``_.-``; not starting with a separator."""
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError(f"invalid {kind}: expected one safe path component, got {value!r}")
    return value


def checked_run_id(value: str) -> str:
    """``YYYY-MM-DD_HHMMSS_SGT__<label>`` with a real calendar date and time."""
    if not isinstance(value, str) or not _RUN.fullmatch(value):
        raise ValueError("run_id must be YYYY-MM-DD_HHMMSS_SGT__label")
    datetime.strptime(value.split("_SGT__", 1)[0], "%Y-%m-%d_%H%M%S")
    return value


def json_bytes(value: object) -> bytes:
    """The one serialisation of every record: sorted, indented, finite, UTF-8."""
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def default_case_root(case_id: str, *, base: Optional[PathLike] = None) -> Path:
    """``<base>/<case_id>``; ``base`` defaults to the repository's ``case/agent``."""
    return Path(base if base is not None else REPO_ROOT / CASE_AGENT) / checked_id(case_id, "case_id")


def repo_relative_or_absolute(path: Path) -> str:
    """A repository-relative POSIX path when ``path`` is inside the repository, else absolute."""
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(resolved)


def default_validator(project: Dict[str, Any]) -> Dict[str, Any]:
    """``agent.tools.project.validate_project`` (agent.scripted_api); raises ``ValueError`` when not ok."""
    from agent.tools.project import validate_project

    report = validate_project(project)
    if not report.get("ok"):
        raise ValueError("invalid .cfdproj: " + json.dumps(report.get("errors"), ensure_ascii=False, default=str))
    return report


class RunStore:
    """Files under ``root``: no traversal, no symlinks, no overwrite."""

    sha256 = staticmethod(sha256_bytes)

    def __init__(self, root: PathLike, *, create: bool = False, validator: Optional[Validator] = None):
        root = Path(root)
        if create:
            root.mkdir(parents=True, exist_ok=True)
        if root.is_symlink():
            raise ValueError(f"store root must not be a symlink: {root}")
        if not root.is_dir():
            raise FileNotFoundError(f"store root does not exist: {root}")
        self.root = root.resolve()
        self._validator = validator

    def __repr__(self) -> str:
        return f"RunStore({str(self.root)!r})"

    # -- roots ----------------------------------------------------------------
    @classmethod
    def for_case(cls, case_id: str, *, base: Optional[PathLike] = None, create: bool = False,
                 validator: Optional[Validator] = None) -> "RunStore":
        """The store of ``<base>/<case_id>`` (default base ``case/agent``)."""
        return cls(default_case_root(case_id, base=base), create=create, validator=validator)

    # -- paths ----------------------------------------------------------------
    def relative(self, path: PathLike) -> Path:
        """``path`` relative to the root; refuses traversal, escapes and symlinks."""
        path = Path(path)
        if ".." in path.parts:
            raise ValueError("parent traversal is forbidden")
        if path.is_absolute():
            try:
                relative = path.relative_to(self.root)
            except ValueError as exc:
                raise ValueError(f"store path must be inside {self.root}: {path}") from exc
        else:
            relative = path
        if not relative.parts or any(part in ("", ".", "..") for part in relative.parts):
            raise ValueError("expected a nonempty path relative to the store root")
        current = self.root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise ValueError(f"symlink paths are forbidden: {relative}")
        try:
            current.resolve().relative_to(self.root)
        except ValueError as exc:  # pragma: no cover - only reachable through a race
            raise ValueError("store path escapes the root") from exc
        return relative

    def path(self, relative: PathLike) -> Path:
        return self.root / self.relative(relative)

    def _open_dir(self, relative: Path, *, create: bool = False, exclusive_leaf: bool = False) -> int:
        fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for index, part in enumerate(relative.parts):
                if create:
                    try:
                        os.mkdir(part, mode=0o750, dir_fd=fd)
                    except FileExistsError:
                        if exclusive_leaf and index == len(relative.parts) - 1:
                            raise
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
            return fd
        except OSError as exc:
            os.close(fd)
            if exc.errno == 40:  # ELOOP: a link appeared below the root
                raise ValueError(f"symlink paths are forbidden: {relative}") from exc
            raise
        except BaseException:
            os.close(fd)
            raise

    def _parent_fd(self, relative: Path) -> int:
        parent = relative.parent
        if parent == Path("."):
            return os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        return self._open_dir(parent)

    # -- primitives -------------------------------------------------------------
    def make_directory(self, relative: PathLike, *, exclusive: bool = True) -> Path:
        relative = self.relative(relative)
        os.close(self._open_dir(relative, create=True, exclusive_leaf=exclusive))
        return self.root / relative

    def read_bytes(self, path: PathLike) -> bytes:
        relative = self.relative(path)
        parent = self._parent_fd(relative)
        try:
            fd = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
            with os.fdopen(fd, "rb") as stream:
                return stream.read()
        finally:
            os.close(parent)

    def read_json(self, path: PathLike) -> Any:
        return json.loads(self.read_bytes(path))

    def write_bytes(self, path: PathLike, payload: bytes) -> Path:
        relative = self.relative(path)
        parent = self._parent_fd(relative)
        try:
            fd = os.open(relative.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o640, dir_fd=parent)
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            os.close(parent)
        return self.root / relative

    def append_bytes(self, path: PathLike, payload: bytes) -> None:
        relative = self.relative(path)
        parent = self._parent_fd(relative)
        try:
            fd = os.open(relative.name, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW, dir_fd=parent)
            with os.fdopen(fd, "ab") as stream:
                stream.write(payload)
                stream.flush()
        finally:
            os.close(parent)

    def write_json(self, path: PathLike, value: object) -> Path:
        return self.write_bytes(path, json_bytes(value))

    # -- projects ---------------------------------------------------------------
    def validate(self, project: Dict[str, Any]) -> Dict[str, Any]:
        """The injected validator's report; ``ValueError`` when the project is invalid."""
        if not isinstance(project, dict) or not project:
            raise ValueError("invalid .cfdproj: expected a nonempty project object")
        json_bytes(project)  # nonfinite numbers are invalid before any validator runs
        return (self._validator or default_validator)(project)

    def read_project(self, path: PathLike) -> Dict[str, Any]:
        project = json.loads(self.read_bytes(path))
        self.validate(project)
        return project

    @staticmethod
    def _relocated(project: Dict[str, Any], directory: Path, filename: str) -> Dict[str, Any]:
        """Only ``meta.casePath`` / ``meta.exportFileName`` move; geometry is untouched."""
        project = copy.deepcopy(project)
        meta = project.setdefault("meta", {})
        meta["casePath"] = repo_relative_or_absolute(directory)
        meta["exportFileName"] = Path(filename).stem
        return project

    @classmethod
    def create_case(cls, case_id: str, source_project: PathLike, *, base: Optional[PathLike] = None,
                    validator: Optional[Validator] = None) -> "RunStore":
        """A new ``<base>/<case_id>/`` with ``reference/`` (the source bytes) and ``project.cfdproj``."""
        root = default_case_root(case_id, base=base)
        source = Path(source_project)
        if source.suffix != ".cfdproj":
            raise ValueError("reference source must be an actual .cfdproj file")
        original = source.read_bytes()
        project = json.loads(original)
        probe = cls.__new__(cls)
        probe._validator = validator
        report = probe.validate(project)
        adopted = cls._relocated(project, root, "project.cfdproj")
        probe.validate(adopted)
        root.parent.mkdir(parents=True, exist_ok=True)
        root.mkdir(exist_ok=False)  # exclusive: a case id is never reused
        store = cls(root, validator=validator)
        store.make_directory("reference")
        store.write_bytes("reference/original.cfdproj", original)
        store.write_json("reference/source_identity.json", {
            "source": repo_relative_or_absolute(source), "sha256": sha256_bytes(original),
            "adopted_project": "project.cfdproj", "created_at_sgt": timestamp_sgt(), "validator": report,
            "adoption": "Only meta.casePath/exportFileName relocated; geometry unchanged."})
        store.write_json("project.cfdproj", adopted)
        return store

    def save_candidate(self, run_id: str, agent_id: str, project: Dict[str, Any], *, structured_output: object,
                       raw_output: str, identity: Dict[str, Any]) -> Path:
        """``agent_outputs/<run_id>/<agent_id>/``: candidate, structured and raw output, manifest."""
        run_id, agent_id = checked_run_id(run_id), checked_id(agent_id, "agent_id")
        if not isinstance(identity, dict) or not identity:
            raise ValueError("a nonempty Agent/source identity is required")
        if not isinstance(raw_output, str):
            raise ValueError("raw_output must be the unmodified output text")
        relative = Path("agent_outputs") / run_id / agent_id
        self.validate(project)
        candidate = self._relocated(project, self.root / relative, "candidate.cfdproj")
        report = self.validate(candidate)
        candidate_data, structured_data = json_bytes(candidate), json_bytes(structured_output)
        raw_data = raw_output.encode("utf-8")
        manifest = {
            "format": CANDIDATE_FORMAT, "store_root": repo_relative_or_absolute(self.root),
            "run_id": run_id, "agent_id": agent_id, "created_at_sgt": timestamp_sgt(), "identity": identity,
            "project": {"path": (relative / "candidate.cfdproj").as_posix(), "sha256": sha256_bytes(candidate_data),
                        "uid": candidate.get("uid"), "validator": report},
            "structured_output": {"path": (relative / "structured_output.json").as_posix(),
                                  "sha256": sha256_bytes(structured_data)},
            "raw_output": {"path": (relative / "raw_output.txt").as_posix(), "sha256": sha256_bytes(raw_data)},
        }
        json_bytes(manifest)  # refuse an invalid identity before its directory exists
        self.make_directory(Path("agent_outputs"), exclusive=False)
        self.make_directory(Path("agent_outputs") / run_id, exclusive=False)
        self.make_directory(relative)
        self.write_bytes(relative / "candidate.cfdproj", candidate_data)
        self.write_bytes(relative / "structured_output.json", structured_data)
        self.write_bytes(relative / "raw_output.txt", raw_data)
        self.write_json(relative / "manifest.json", manifest)
        return self.root / relative

    def select_candidate(self, run_id: str, agent_id: str, *, reason: str) -> Path:
        """Copy one saved candidate to ``final_candidate.cfdproj`` (once) after checking its manifest."""
        run_id, agent_id = checked_run_id(run_id), checked_id(agent_id, "agent_id")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("selection requires an explicit reason")
        selected = Path("agent_outputs") / run_id / agent_id
        candidate_data = self.read_bytes(selected / "candidate.cfdproj")
        manifest = self.read_json(selected / "manifest.json")
        if sha256_bytes(candidate_data) != manifest["project"]["sha256"]:
            raise ValueError("candidate project no longer matches its saved manifest")
        project = json.loads(candidate_data)
        self.validate(project)
        final = self._relocated(project, self.root, "final_candidate.cfdproj")
        report = self.validate(final)
        final_data = json_bytes(final)
        destination = Path("final_candidate.cfdproj")
        self.write_bytes(destination, final_data)
        self.write_json("final_candidate.manifest.json", {
            "format": SELECTION_FORMAT, "selected_at_sgt": timestamp_sgt(), "reason": reason,
            "source_manifest": (selected / "manifest.json").as_posix(),
            "source_candidate_sha256": sha256_bytes(candidate_data),
            "project": destination.as_posix(), "sha256": sha256_bytes(final_data), "validator": report})
        return self.root / destination
