"""agent.policies.language imports without the LLM stack; its token checks run on a stub tokenizer.

The PEFT mechanics themselves are in ``test_policies_language.py`` (needs the
``agent-llm`` extra). These checks run on the core install with torch only.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("torch")

SRC = Path(__file__).resolve().parents[2]


def test_importing_the_language_policy_loads_neither_transformers_nor_peft():
    code = ("import json,sys;import agent.policies.language as m;"
            "print(json.dumps(sorted(n for n in sys.modules if n.split('.')[0] in ('transformers','peft','safetensors'))))")
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    done = subprocess.run([sys.executable, "-c", code], cwd=SRC, capture_output=True, text=True, env=env,
                          timeout=120, check=True)
    assert json.loads(done.stdout.strip().splitlines()[-1]) == []


class StubTokenizer:
    """``encode`` as a byte-pair tokenizer would: one id per known letter, two for anything else."""
    table = {"A": 32, "B": 33, "C": 34, "D": 32}

    def encode(self, text, add_special_tokens=False):
        return [self.table[text]] if text in self.table else [1, 2]


def test_structural_choices_must_be_distinct_single_tokens():
    from agent.policies.language import LanguageDesignPolicy
    policy = LanguageDesignPolicy.__new__(LanguageDesignPolicy)
    policy.tokenizer = StubTokenizer()
    assert policy._single_tokens(("A", "B", "C")) == [32, 33, 34]
    with pytest.raises(ValueError, match="single tokens"):
        policy._single_tokens(("A", "BB"))
    with pytest.raises(ValueError, match="collide"):
        policy._single_tokens(("A", "D"))
    with pytest.raises(ValueError, match="two distinct"):
        policy._single_tokens(("A",))
    with pytest.raises(ValueError, match="two distinct"):
        policy._single_tokens(("A", "A"))
