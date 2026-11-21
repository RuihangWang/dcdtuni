"""pyproject.toml ships the slim trunk ``agent`` (never ``agent_legacy``) with its heavy dependencies as extras."""
from __future__ import annotations

from pathlib import Path

import pytest

try:
    import tomllib
except ImportError:  # Python 3.10
    tomllib = pytest.importorskip("tomli")

PYPROJECT = Path(__file__).resolve().parents[3] / "pyproject.toml"


def test_agent_is_packaged_and_agent_legacy_is_not():
    poetry = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["tool"]["poetry"]
    included = {(row["include"], row.get("from")) for row in poetry["packages"]}
    assert ("agent", "src") in included and ("backend", "src") in included
    assert not any("agent_legacy" in row["include"] for row in poetry["packages"])


def test_torch_and_the_llm_stack_are_optional_extras():
    poetry = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["tool"]["poetry"]
    dependencies, extras = poetry["dependencies"], poetry["extras"]
    for name in ("torch", "scipy", "transformers", "peft", "safetensors"):
        assert dependencies[name]["optional"] is True
    assert set(extras["agent"]) == {"torch", "scipy"}
    assert set(extras["agent-llm"]) == {"torch", "transformers", "peft", "safetensors"}
