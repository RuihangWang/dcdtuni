"""Discover legacy and runtime liquid artifacts without importing the solver."""
from pathlib import Path
import re

_RUNTIME = re.compile(r'^run-[A-Za-z0-9_-]{1,64}$')


def liquid_result_file(directory: Path) -> Path | None:
    legacy = directory / 'liquid_result.json'
    if legacy.is_file():
        return legacy
    runtime = directory / 'result.json'
    if _RUNTIME.fullmatch(directory.name) and runtime.is_file():
        return runtime
    return None


def liquid_result_files(case_dir: Path) -> list[Path]:
    files = []
    for root in (case_dir / 'Liquid' / 'log', case_dir / 'log'):
        if root.is_dir():
            for directory in root.iterdir():
                if directory.is_dir() and directory.resolve().parent == root.resolve():
                    result = liquid_result_file(directory)
                    if result is not None:
                        files.append(result)
    return sorted(files, key=lambda p: (p.stat().st_mtime_ns, str(p)), reverse=True)


def selected_liquid_result(case_dir: Path, requested: str | None) -> Path | None:
    files = liquid_result_files(case_dir)
    if not requested:
        return files[0] if files else None
    selected = Path(requested)
    for result in files:
        if (selected.is_absolute() and selected.resolve() == result.parent.resolve()) or (
            not selected.is_absolute() and selected.name == requested and requested == result.parent.name
        ):
            return result
    return None
