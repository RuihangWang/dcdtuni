"""Small non-language Transformer conditioned only on declared physical task fields.

The frozen ``training/structured_policy.py`` with its task fields injected:
there it was bound to the controlled-2D benchmark's ten fields, here the caller
declares ``fields`` and their fixed unit ``scales`` (declared before learning,
never fitted to HF data). A task is any object with those attributes (or a
mapping with those keys); a ``validate()`` method is called when present.
Everything else is unchanged in meaning: one token per field plus a CLS
token, a two-layer encoder, a categorical structural action head and the
shared :class:`~agent.policies.numeric.NumericalDesignHead`; the frozen
reference is the complete SFT policy (encoder, action and numeric heads), and
its KL is the exact enumeration over the actions.

Checkpoints are written once into a new directory (``policy.pt`` +
``model_identity.json``) and restored only into the same configuration.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import time
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

import torch
from torch import nn

from agent.policies.numeric import NumericalDesignHead, categorical_kl, enumerated_continuous_kl

POLICY_VERSION = "structured-physical-Transformer/v2 (v1 with declared fields)"


def _field(task: Any, name: str) -> float:
    value = task[name] if isinstance(task, Mapping) else getattr(task, name)
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"task field {name} must be finite")
    return value


def task_feature_values(tasks: Sequence[Any], fields: Sequence[str], scales: Sequence[float]) -> torch.Tensor:
    """``[[task.<field> / scale, ...] per task]`` as float32; validates each task when it can."""
    if len(fields) != len(scales) or not fields or any(not math.isfinite(s) or s == 0 for s in scales):
        raise ValueError("one finite nonzero fixed scale per declared field")
    rows = []
    for task in tasks:
        validate = getattr(task, "validate", None)
        if callable(validate):
            validate()
        rows.append([_field(task, name) / scale for name, scale in zip(fields, scales)])
    if not rows:
        raise ValueError("at least one physical task required")
    return torch.tensor(rows, dtype=torch.float32)


def tensor_state_sha256(modules: Mapping[str, nn.Module]) -> str:
    digest = hashlib.sha256()
    for prefix, module in modules.items():
        for name, value in sorted(module.state_dict().items()):
            value = value.detach().cpu().contiguous()
            digest.update((prefix + "." + name + str(value.dtype) + str(tuple(value.shape))).encode())
            digest.update(value.numpy().tobytes())
    return digest.hexdigest()


class PhysicalTokenEncoder(nn.Module):
    def __init__(self, field_count: int, width: int = 64, layers: int = 2, heads: int = 4):
        super().__init__()
        self.scalar = nn.Linear(1, width)
        self.field = nn.Embedding(field_count, width)
        self.cls = nn.Parameter(torch.zeros(1, 1, width))
        block = nn.TransformerEncoderLayer(width, heads, dim_feedforward=width * 2, dropout=0., batch_first=True,
                                           activation="gelu", norm_first=False)
        self.transformer = nn.TransformerEncoder(block, layers, enable_nested_tensor=False)
        self.norm = nn.LayerNorm(width)

    def forward(self, values):
        tokens = self.scalar(values[:, :, None]) + self.field.weight[None, :, :]
        sequence = torch.cat((self.cls.expand(len(values), -1, -1), tokens), 1)
        return self.norm(self.transformer(sequence)[:, 0])


class StructuredDesignPolicy(nn.Module):
    def __init__(self, bounds, *, fields: Sequence[str], scales: Sequence[float], action_count: int = 2,
                 width: int = 64, layers: int = 2, heads: int = 4, context: Mapping[str, Any] | None = None):
        super().__init__()
        fields, scales = tuple(fields), tuple(float(s) for s in scales)
        task_feature_values([dict(zip(fields, scales))], fields, scales)  # validates the declaration
        self.fields, self.scales = fields, scales
        self.config = {"bounds": [list(b) for b in bounds], "fields": list(fields), "scales": list(scales),
                       "action_count": action_count, "width": width, "layers": layers, "heads": heads}
        self.encoder = PhysicalTokenEncoder(len(fields), width, layers, heads)
        self.action_layer = nn.Linear(width, action_count)
        self.numeric = NumericalDesignHead(width, bounds, action_count=action_count)
        self.frozen_encoder = None
        self.frozen_action = None
        self.reference_numeric = None
        self.identity = {"version": POLICY_VERSION,
                         "architecture": f"{action_count}-action physical-token Transformer plus shared stochastic "
                                         "numerical head",
                         "config": self.config, "fields": list(fields), "fixed_unit_scales": list(scales),
                         "context": dict(context or {}),
                         "input_exclusions": ["task ids", "natural language", "HF rewards/fields/optimized labels"],
                         "total_current_parameters": sum(p.numel() for p in self.parameters()),
                         "continuous_density": "same NumericalDesignHead and unrounded float64 as language policy",
                         "dropout": 0.}

    def current_modules(self):
        return {"encoder": self.encoder, "action": self.action_layer, "numeric": self.numeric}

    def current_sha256(self):
        return tensor_state_sha256(self.current_modules())

    def reference_sha256(self):
        if self.frozen_encoder is None:
            raise RuntimeError("reference unavailable")
        return tensor_state_sha256({"encoder": self.frozen_encoder, "action": self.frozen_action,
                                    "numeric": self.reference_numeric})

    def features(self, tasks, *, reference=False):
        values = task_feature_values(tasks, self.fields, self.scales).to(self.encoder.scalar.weight.device)
        if reference:
            if self.frozen_encoder is None:
                raise RuntimeError("freeze complete SFT reference first")
            with torch.no_grad():
                hidden = self.frozen_encoder(values)
                logits = self.frozen_action(hidden)
        else:
            hidden = self.encoder(values)
            logits = self.action_layer(hidden)
        return hidden, logits, {"structured_fields": list(self.fields)}

    def propose(self, tasks, *, samples=4, generator=None):
        start = time.perf_counter()
        hidden, logits, metadata = self.features(tasks)
        sample = self.numeric.sample(hidden, logits, samples=samples, generator=generator)
        return sample, hidden, logits, {**metadata, "proposal_wall_seconds": time.perf_counter() - start}

    def freeze_reference(self):
        if self.frozen_encoder is not None:
            raise RuntimeError("complete reference is immutable")
        self.frozen_encoder = copy.deepcopy(self.encoder).requires_grad_(False).eval()
        self.frozen_action = copy.deepcopy(self.action_layer).requires_grad_(False).eval()
        self.reference_numeric = copy.deepcopy(self.numeric).requires_grad_(False).eval()
        self.identity["reference"] = "frozen complete SFT encoder/action/conditional numeric head"
        self.identity["frozen_reference_sha256"] = self.reference_sha256()

    def reference_kl(self, tasks, hidden, logits, actions, *, samples):
        start = time.perf_counter()
        ref_hidden, ref_logits, _ = self.features(tasks, reference=True)
        count = self.numeric.action_count
        all_actions = torch.arange(count, device=hidden.device).repeat(len(hidden))
        expanded = hidden[:, None, :].expand(-1, count, -1).reshape(-1, hidden.shape[-1])
        expanded_ref = ref_hidden[:, None, :].expand(-1, count, -1).reshape(-1, ref_hidden.shape[-1])
        conditional = self.numeric.normal_kl(expanded, all_actions, self.reference_numeric,
                                             expanded_ref).reshape(len(hidden), count)
        continuous = enumerated_continuous_kl(logits, conditional)
        discrete = categorical_kl(logits, ref_logits).mean()
        return continuous + discrete, {"reference_wall_seconds": time.perf_counter() - start,
                                       "kl_continuous": float(continuous.detach()),
                                       "kl_discrete": float(discrete.detach()),
                                       "continuous_KL_expectation": f"exact {count}-action enumeration including "
                                                                    "action-probability derivative"}

    def save_policy(self, path):
        path = Path(path)
        if path.exists():
            raise FileExistsError(path)
        path.mkdir(parents=True)
        torch.save({name: module.state_dict() for name, module in self.current_modules().items()}, path / "policy.pt")
        (path / "model_identity.json").write_text(json.dumps({**self.identity, "current_state_sha256":
                                                              self.current_sha256()}, indent=2) + "\n")

    def load_policy(self, path):
        if self.frozen_encoder is not None:
            raise RuntimeError("restore before freezing reference")
        path = Path(path)
        identity = json.loads((path / "model_identity.json").read_text())
        if identity["version"] != POLICY_VERSION or identity["config"] != self.config:
            raise ValueError("checkpoint configuration mismatch")
        state = torch.load(path / "policy.pt", map_location="cpu", weights_only=True)
        for name, module in self.current_modules().items():
            module.load_state_dict(state[name])
        if self.current_sha256() != identity["current_state_sha256"]:
            raise ValueError("restored tensor state mismatch")
        self.identity["starting_checkpoint"] = {
            "path": str(path), "policy_file_sha256": hashlib.sha256((path / "policy.pt").read_bytes()).hexdigest(),
            "tensor_state_sha256": self.current_sha256()}
