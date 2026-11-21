"""Which OpenFOAM ``etc/bashrc`` the post-processor sources for foamToVTK.

The server is launched with the dcdtuni interpreter by path, from whatever
shell the user has open — and that shell's ``CONDA_PREFIX`` comes along. When
that shell had ``base`` active, the resolver trusted ``CONDA_PREFIX`` first,
found no ``etc/bashrc`` there, returned "" and the conversion ran
``source "" && foamToVTK`` — which fails with ``line 1: : No such file or
directory``, the message the user saw as "post-processing fails to start" on
Base2-9. Measured on the running server: ``CONDA_PREFIX=.../miniconda3``,
``CONDA_DEFAULT_ENV=base``, interpreter ``.../envs/dcdtuni/bin/python3.10``.
"""

from pathlib import Path

import pytest

from backend.aircooling.runner import postprocess as pp


def _env_with_bashrc(tmp_path: Path, name: str) -> Path:
    prefix = tmp_path / name
    (prefix / "etc").mkdir(parents=True)
    (prefix / "etc" / "bashrc").write_text("# openfoam\n")
    (prefix / "bin").mkdir()
    (prefix / "bin" / "python3.10").write_text("")
    return prefix


def _clear(monkeypatch):
    for key in ("FOAM_BASHRC", "WM_PROJECT_DIR", "CONDA_PREFIX"):
        monkeypatch.delenv(key, raising=False)


def test_interpreter_prefix_beats_a_conda_prefix_without_openfoam(tmp_path, monkeypatch):
    _clear(monkeypatch)
    mine = _env_with_bashrc(tmp_path, "dcdtuni")
    base = tmp_path / "miniconda3"
    base.mkdir()
    monkeypatch.setenv("CONDA_PREFIX", str(base))
    monkeypatch.setattr(pp.sys, "executable", str(mine / "bin" / "python3.10"))
    assert pp._resolve_foam_bashrc() == str(mine / "etc" / "bashrc")


def test_foam_bashrc_override_wins(tmp_path, monkeypatch):
    _clear(monkeypatch)
    mine = _env_with_bashrc(tmp_path, "dcdtuni")
    other = _env_with_bashrc(tmp_path, "other")
    monkeypatch.setenv("FOAM_BASHRC", str(other / "etc" / "bashrc"))
    monkeypatch.setattr(pp.sys, "executable", str(mine / "bin" / "python3.10"))
    assert pp._resolve_foam_bashrc() == str(other / "etc" / "bashrc")


def test_wm_project_dir_beats_conda_prefix(tmp_path, monkeypatch):
    _clear(monkeypatch)
    foam = _env_with_bashrc(tmp_path, "openfoam")
    conda = _env_with_bashrc(tmp_path, "conda")
    monkeypatch.setenv("WM_PROJECT_DIR", str(foam))
    monkeypatch.setenv("CONDA_PREFIX", str(conda))
    monkeypatch.setattr(pp.sys, "executable", str(tmp_path / "nowhere" / "bin" / "python"))
    assert pp._resolve_foam_bashrc() == str(foam / "etc" / "bashrc")


def test_conda_prefix_is_still_used_when_it_is_the_openfoam_env(tmp_path, monkeypatch):
    _clear(monkeypatch)
    conda = _env_with_bashrc(tmp_path, "dcdtuni")
    monkeypatch.setenv("CONDA_PREFIX", str(conda))
    monkeypatch.setattr(pp.sys, "executable", "/usr/bin/python3")
    assert pp._resolve_foam_bashrc() == str(conda / "etc" / "bashrc")


def test_nothing_found_is_empty_and_the_conversion_says_why(tmp_path, monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setattr(pp.sys, "executable", "/usr/bin/python3")
    assert pp._resolve_foam_bashrc() == ""
    with pytest.raises(RuntimeError, match="FOAM_BASHRC"):
        pp._foam_source_prefix()
