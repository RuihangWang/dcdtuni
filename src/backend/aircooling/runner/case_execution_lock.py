"""Cross-process exclusion for one CFD case's mutable inputs and execution.

The lock file belongs to the case root, outside ``model`` and ``Mesh`` so
upload transactions and mesh resets cannot replace its inode. Both the HTTP
server and ``run_cfd`` must use this context around their full write/run scope.
"""

from contextlib import contextmanager
import fcntl
from pathlib import Path


CASE_LOCK_FILENAME = ".dcdtuni-case.lock"


class CaseExecutionBusyError(RuntimeError):
    """Another process is uploading to or running this case."""


@contextmanager
def case_execution_lock(case_dir: Path, *, blocking: bool = False):
    case_dir = Path(case_dir).expanduser().resolve()
    case_dir.mkdir(parents=True, exist_ok=True)
    with (case_dir / CASE_LOCK_FILENAME).open("a+b") as lock_file:
        flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
        try:
            fcntl.flock(lock_file, flags)
        except BlockingIOError as exc:
            raise CaseExecutionBusyError(f"Case is busy: {case_dir}") from exc
        try:
            yield
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)
