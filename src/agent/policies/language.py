"""LoRA language-conditioned discrete/continuous design policy (transformers and peft imported lazily).

The frozen ``training/language_policy.py``, ``training/multi_structure_policy.py``
and the loaders of ``training/ddl_agent/model.py``, unchanged in meaning:

- the structural choice is one restricted next token among declared
  single-token alternatives, followed by the shared stochastic
  :class:`~agent.policies.numeric.NumericalDesignHead` (continuous values are
  emitted last and never fed back into later tokens);
- the KL reference is the complete frozen SFT policy -- a second, frozen LoRA
  adapter plus a frozen copy of the numeric head (or the base model with the
  adapter disabled before any freeze);
- a checkpoint is a new directory (``lora/``, ``numeric_head.pt``,
  ``model_identity.json``) restored only into the same base model, rank,
  alpha and action tokens.

Two differences from the frozen file: the constructor no longer calls
``configure_runtime_caches()`` (call :func:`agent.records.configure_runtime_caches`
first if the caches must stay in a given root), and checkpoint paths are any
directory, not only the repository.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import time
from contextlib import contextmanager
from pathlib import Path

import torch
from torch import nn

from agent.policies.numeric import NumericalDesignHead, categorical_kl, enumerated_continuous_kl


def load_tokenizer(path):
    from transformers import AutoTokenizer
    tok = AutoTokenizer.from_pretrained(path, trust_remote_code=True)
    if tok.pad_token_id is None:
        tok.pad_token = tok.eos_token
    return tok


def load_base(path, dtype=torch.bfloat16, device_map="cuda:0", attn="sdpa"):
    from transformers import AutoModelForCausalLM
    kw = dict(torch_dtype=dtype, device_map=device_map, trust_remote_code=True)
    try:
        model = AutoModelForCausalLM.from_pretrained(path, attn_implementation=attn, **kw)
    except Exception:  # multimodal checkpoints expose their text path through ImageTextToText
        from transformers import AutoModelForImageTextToText
        model = AutoModelForImageTextToText.from_pretrained(path, attn_implementation=attn, **kw)
    model.config.use_cache = True
    return model


def lora_target_modules(model, include_mlp=True):
    """The language-model linear projections (vision towers skipped)."""
    names = set()
    pattern = re.compile(r"(q_proj|k_proj|v_proj|o_proj" + (r"|gate_proj|up_proj|down_proj" if include_mlp else "")
                         + r"|in_proj_qkvz|in_proj_ba|out_proj)$")
    for name, module in model.named_modules():
        if isinstance(module, torch.nn.Linear) and pattern.search(name) and "visual" not in name and "vision" not in name:
            names.add(name)
    return sorted(names)


def attach_lora(model, r=16, alpha=32, dropout=0.05, include_mlp=True):
    from peft import LoraConfig, get_peft_model
    config = LoraConfig(r=r, lora_alpha=alpha, lora_dropout=dropout, target_modules=lora_target_modules(model, include_mlp),
                        bias="none", task_type="CAUSAL_LM")
    return get_peft_model(model, config)


class LanguageDesignPolicy(nn.Module):
    def __init__(self, model_path, bounds, *, device='cuda:0', rank=8, alpha=16, max_prompt_tokens=512,
                 choices=("A", "B")):
        super().__init__()
        self.model_path = Path(model_path).resolve()
        self.max_prompt_tokens = max_prompt_tokens
        self.tokenizer = load_tokenizer(str(self.model_path))
        self.tokenizer.padding_side = 'right'
        self.action_token_ids = self._single_tokens(choices)
        start = time.perf_counter()
        base = load_base(str(self.model_path), dtype=torch.bfloat16, device_map=device, attn='sdpa')
        base.config.use_cache = False
        self.model = attach_lora(base, r=rank, alpha=alpha, dropout=0.0, include_mlp=False)
        self.model.eval()
        config = getattr(base.config, 'text_config', base.config)
        self.numeric = NumericalDesignHead(int(config.hidden_size), bounds,
                                           action_count=len(self.action_token_ids)).to(device=device)
        self.reference_numeric = copy.deepcopy(self.numeric)
        self.reference_numeric.requires_grad_(False)
        self.reference_adapter = None
        self.identity = {
            'path': str(self.model_path), 'model_type': base.config.model_type,
            'architectures': base.config.architectures, 'loaded_class': base.__class__.__name__,
            'config_sha256': hashlib.sha256((self.model_path / 'config.json').read_bytes()).hexdigest(),
            'weight_index_sha256': hashlib.sha256((self.model_path / 'model.safetensors.index.json').read_bytes()).hexdigest(),
            'total_parameters': sum(p.numel() for p in base.parameters()),
            'trainable_lora_parameters': sum(p.numel() for p in self.model.parameters() if p.requires_grad),
            'rank': rank, 'alpha': alpha, 'dropout': 0.0, 'action_token_ids': self.action_token_ids,
            'structural_choices': list(choices),
            'sampling': 'restricted single structural token plus conditional stochastic numerical head',
            'continuous_serialization': 'unrounded float64, latent and joint density recorded',
            'load_wall_seconds': time.perf_counter() - start,
        }

    def _single_tokens(self, choices):
        if len(choices) < 2 or len(set(choices)) != len(choices):
            raise ValueError("at least two distinct structural tokens required")
        ids = []
        for choice in choices:
            encoded = self.tokenizer.encode(choice, add_special_tokens=False)
            if len(encoded) != 1:
                raise ValueError('structural alternatives must be single tokens')
            ids.append(encoded[0])
        if len(set(ids)) != len(ids):
            raise ValueError("structural token identities collide")
        return ids

    def features(self, prompts, *, reference=False):
        messages = [self.tokenizer.apply_chat_template([{'role': 'user', 'content': p}], tokenize=False,
                                                       add_generation_prompt=True, enable_thinking=False)
                    for p in prompts]
        encoded = self.tokenizer(messages, return_tensors='pt', padding=True, truncation=False)
        if encoded['input_ids'].shape[1] > self.max_prompt_tokens:
            raise ValueError('prompt exceeds declared context; do not silently truncate physical requirements')
        device = next(self.model.parameters()).device
        encoded = {k: v.to(device) for k, v in encoded.items()}
        self.model.eval()
        if reference:
            with torch.no_grad(), self._reference_context():
                output = self.model(**encoded, output_hidden_states=True, use_cache=False, return_dict=True)
        else:
            output = self.model(**encoded, output_hidden_states=True, use_cache=False, return_dict=True)
        if output.hidden_states is None:
            raise RuntimeError('text hidden states unavailable')
        positions = encoded['attention_mask'].sum(-1) - 1
        row = torch.arange(len(prompts), device=device)
        hidden = output.hidden_states[-1][row, positions]
        logits = output.logits[row, positions][:, self.action_token_ids].float()
        return hidden, logits, {'prompt_tokens': encoded['attention_mask'].sum(-1).tolist()}

    def propose(self, prompts, *, samples=4, generator=None):
        start = time.perf_counter()
        hidden, logits, tokens = self.features(prompts)
        sample = self.numeric.sample(hidden, logits, samples=samples, generator=generator)
        return sample, hidden, logits, {**tokens, 'proposal_wall_seconds': time.perf_counter() - start}

    def reference_kl(self, prompts, hidden, logits, actions, *, samples):
        start = time.perf_counter()
        ref_hidden, ref_logits, _ = self.features(prompts, reference=True)
        count = self.numeric.action_count
        all_actions = torch.arange(count, device=hidden.device).repeat(len(hidden))
        expanded = hidden[:, None, :].expand(-1, count, -1).reshape(-1, hidden.shape[-1])
        expanded_ref = ref_hidden[:, None, :].expand(-1, count, -1).reshape(-1, ref_hidden.shape[-1])
        conditional = self.numeric.normal_kl(expanded, all_actions, self.reference_numeric,
                                             expanded_ref).reshape(len(hidden), count)
        continuous = enumerated_continuous_kl(logits, conditional)
        discrete = categorical_kl(logits, ref_logits).mean()
        return continuous + discrete, {'reference_wall_seconds': time.perf_counter() - start,
                                       'kl_continuous': float(continuous.detach()),
                                       'kl_discrete': float(discrete.detach()),
                                       'continuous_KL_expectation': f'exact {count}-action enumeration including '
                                                                    'action-probability derivative'}

    @contextmanager
    def _reference_context(self):
        if self.reference_adapter is None:
            with self.model.disable_adapter():
                yield
            return
        self.model.set_adapter(self.reference_adapter)
        try:
            yield
        finally:
            self.model.set_adapter("default")
            for name, parameter in self.model.named_parameters():
                if f".{self.reference_adapter}." in name:
                    parameter.requires_grad_(False)

    def freeze_reference(self):
        """Freeze the complete SFT policy, including its LoRA and numerical head."""
        if self.reference_adapter is not None:
            raise RuntimeError("reference is immutable; create a new policy for another study")
        from peft import get_peft_model_state_dict, set_peft_model_state_dict
        state = {name: value.detach().clone() for name, value in
                 get_peft_model_state_dict(self.model, adapter_name="default").items()}
        name = "frozen_sft"
        self.model.add_adapter(name, copy.deepcopy(self.model.peft_config["default"]))
        set_peft_model_state_dict(self.model, state, adapter_name=name)
        self.model.set_adapter("default")
        for parameter_name, parameter in self.model.named_parameters():
            if f".{name}." in parameter_name:
                parameter.requires_grad_(False)
        self.reference_numeric = copy.deepcopy(self.numeric)
        self.reference_numeric.requires_grad_(False)
        self.reference_adapter = name
        self.identity["reference"] = "frozen complete SFT adapter and numerical head"

    def load_policy(self, path):
        """Restore a common SFT starting point before creating its reference."""
        if self.reference_adapter is not None:
            raise RuntimeError("load before freezing reference")
        from peft import set_peft_model_state_dict
        from safetensors.torch import load_file
        path = Path(path)
        identity = json.loads((path / 'model_identity.json').read_text())
        for key in ("config_sha256", "weight_index_sha256", "rank", "alpha", "action_token_ids"):
            if identity[key] != self.identity[key]:
                raise ValueError(f"starting model identity mismatch: {key}")
        state = torch.load(path / 'numeric_head.pt', map_location=self.numeric.lower.device, weights_only=True)
        for name in ("lower", "width"):
            if not torch.equal(state[name], self.numeric.state_dict()[name]):
                raise ValueError("checkpoint numerical support differs")
        set_peft_model_state_dict(self.model, load_file(str(path / 'lora/adapter_model.safetensors')),
                                  adapter_name="default")
        self.numeric.load_state_dict(state)
        self.identity["starting_checkpoint"] = {
            "path": str(path),
            "lora_sha256": hashlib.sha256((path / 'lora/adapter_model.safetensors').read_bytes()).hexdigest(),
            "numeric_sha256": hashlib.sha256((path / 'numeric_head.pt').read_bytes()).hexdigest()}

    def save_policy(self, path):
        path = Path(path)
        if path.exists():
            raise FileExistsError(path)
        path.mkdir(parents=True)
        self.model.save_pretrained(path / 'lora', selected_adapters=["default"])
        torch.save(self.numeric.state_dict(), path / 'numeric_head.pt')
        (path / 'model_identity.json').write_text(json.dumps(self.identity, indent=2) + '\n')
