"""Design policies (optional extra ``agent``: torch; ``agent-llm`` adds transformers/peft for the language policy).

- :mod:`~agent.policies.numeric`: the bounded stochastic numerical head and KL helpers;
- :mod:`~agent.policies.losses`: joint discrete/continuous policy losses and the information loss;
- :mod:`~agent.policies.structured`: the physical-token Transformer policy;
- :mod:`~agent.policies.language`: the LoRA language policy (transformers/peft imported on use).

Importing this package needs torch and raises an ``ImportError`` that says
which extra to install when torch is missing.
"""
from __future__ import annotations

try:
    import torch  # noqa: F401
except ImportError as exc:  # pragma: no cover - depends on the environment
    raise ImportError("agent.policies needs torch: pip install -e '.[agent]' "
                      "(the language policy also needs '.[agent-llm]')") from exc

from agent.policies.losses import LOSS_VERSION, METHODS, information_policy_loss, joint_policy_loss
from agent.policies.numeric import DesignSamples, NumericalDesignHead, categorical_kl, enumerated_continuous_kl
from agent.policies.structured import StructuredDesignPolicy, task_feature_values

__all__ = ["NumericalDesignHead", "DesignSamples", "categorical_kl", "enumerated_continuous_kl",
           "joint_policy_loss", "information_policy_loss", "LOSS_VERSION", "METHODS",
           "StructuredDesignPolicy", "task_feature_values", "LanguageDesignPolicy"]


def __getattr__(name):
    if name == "LanguageDesignPolicy":
        from agent.policies.language import LanguageDesignPolicy
        return LanguageDesignPolicy
    raise AttributeError(f"module 'agent.policies' has no attribute {name!r}")
