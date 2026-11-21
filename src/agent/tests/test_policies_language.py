"""Actual PEFT adapter/reference mechanics on a tiny CPU language model.

Ported from the frozen ``tests/test_language_reference.py``. Needs the
``agent-llm`` extra (transformers, peft, safetensors); skipped without it.
"""
import copy

import pytest
import torch
from torch import nn

transformers = pytest.importorskip("transformers")
peft = pytest.importorskip("peft")
pytest.importorskip("safetensors")

from agent.policies.language import LanguageDesignPolicy  # noqa: E402
from agent.policies.numeric import NumericalDesignHead  # noqa: E402

INPUTS = torch.tensor([[2, 3, 4, 5]])


def tiny_policy():
    policy = LanguageDesignPolicy.__new__(LanguageDesignPolicy)
    nn.Module.__init__(policy)
    base = transformers.GPT2LMHeadModel(transformers.GPT2Config(
        vocab_size=64, bos_token_id=1, eos_token_id=1, n_positions=32, n_embd=16, n_layer=1, n_head=2,
        resid_pdrop=0., embd_pdrop=0., attn_pdrop=0.))
    policy.model = peft.get_peft_model(base, peft.LoraConfig(r=2, lora_alpha=4, lora_dropout=0.,
                                                             target_modules=["c_attn"], fan_in_fan_out=True,
                                                             task_type="CAUSAL_LM"))
    policy.model.eval()
    policy.numeric = NumericalDesignHead(16, [(.06, .22), (.25, .75)])
    policy.reference_numeric = copy.deepcopy(policy.numeric)
    policy.reference_numeric.requires_grad_(False)
    policy.reference_adapter = None
    policy.identity = {"config_sha256": "tiny-config", "weight_index_sha256": "tiny-weight", "rank": 2, "alpha": 4,
                       "action_token_ids": [32, 33]}
    return policy


@pytest.fixture
def policy():
    torch.manual_seed(71)
    current = tiny_policy()
    with torch.no_grad():
        for name, p in current.model.named_parameters():
            if "lora_" in name:
                p.normal_(std=.12)
        current.numeric.mean_layer.bias.add_(.4)
    return current


def current_logits(policy):
    return policy.model(input_ids=INPUTS, output_hidden_states=True).logits.detach()


def reference_logits(policy):
    with torch.no_grad(), policy._reference_context():
        return policy.model(input_ids=INPUTS, output_hidden_states=True).logits.detach()


def test_sft_reference_includes_lora_and_remains_immutable(policy):
    before = current_logits(policy)
    policy.freeze_reference()
    torch.testing.assert_close(reference_logits(policy), before)
    with torch.no_grad():
        for name, p in policy.model.named_parameters():
            if ".default." in name:
                p.add_(.17)
        policy.numeric.mean_layer.bias.add_(.2)
    assert float((current_logits(policy) - before).abs().max()) > 1e-5
    torch.testing.assert_close(reference_logits(policy), before)
    assert all(not p.requires_grad for n, p in policy.model.named_parameters() if ".frozen_sft." in n)
    assert any(p.requires_grad for n, p in policy.model.named_parameters() if ".default." in n)
    with pytest.raises(RuntimeError):
        policy.freeze_reference()


def test_common_checkpoint_roundtrip_and_identity_mismatch(policy, tmp_path):
    checkpoint = tmp_path / "common"
    policy.save_policy(checkpoint)
    restored = tiny_policy()
    restored.model.base_model.model.load_state_dict(policy.model.base_model.model.state_dict())
    with torch.no_grad():
        for n, p in restored.model.named_parameters():
            if "lora_" in n:
                p.zero_()
    restored.load_policy(checkpoint)
    torch.testing.assert_close(current_logits(restored), current_logits(policy))
    restored.freeze_reference()
    torch.testing.assert_close(reference_logits(restored), current_logits(restored))
    with pytest.raises(FileExistsError):
        policy.save_policy(checkpoint)
    bad = tiny_policy()
    bad.identity["rank"] = 9
    with pytest.raises(ValueError):
        bad.load_policy(checkpoint)
