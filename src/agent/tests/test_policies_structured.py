"""Physical inputs, complete reference, checkpoint and joint path mechanics of the structured policy.

Ported from the frozen ``tests/test_structured_policy.py``. The frozen test
drew its task from the controlled-2D catalogue; the trunk policy declares its
fields, so a plain dataclass task with the same ten physical fields stands in.
"""
from dataclasses import dataclass, replace

import pytest
import torch

from agent.policies.losses import joint_policy_loss
from agent.policies.structured import StructuredDesignPolicy, task_feature_values

FIELDS = ("width_m", "height_m", "depth_m", "hot_width_fraction", "nu_m2_s", "beta_1_K", "delta_T_K",
          "control_weight", "Pr", "T_reference_K")
SCALES = (1., 1., .1, .2, .005, .00333, 1., .01, .71, 300.)


@dataclass(frozen=True)
class Task:
    task_id: str = "train-square"
    width_m: float = 1.
    height_m: float = 1.
    depth_m: float = .1
    hot_width_fraction: float = .2
    nu_m2_s: float = .005
    beta_1_K: float = .00333
    delta_T_K: float = 1.
    control_weight: float = .01
    Pr: float = .71
    T_reference_K: float = 300.

    def validate(self):
        if self.width_m <= 0:
            raise ValueError("width must be positive")


def policy():
    return StructuredDesignPolicy(((.06, .22), (.25, .75)), fields=FIELDS, scales=SCALES)


def test_physical_features_ignore_task_id_and_preserve_true_requirement_changes():
    task = Task()
    assert torch.equal(task_feature_values([task], FIELDS, SCALES),
                       task_feature_values([replace(task, task_id="different-name")], FIELDS, SCALES))
    assert not torch.equal(task_feature_values([task], FIELDS, SCALES),
                           task_feature_values([replace(task, width_m=task.width_m * 1.1)], FIELDS, SCALES))
    assert not torch.equal(task_feature_values([task], FIELDS, SCALES),
                           task_feature_values([replace(task, control_weight=.005)], FIELDS, SCALES))
    with pytest.raises(ValueError):
        task_feature_values([replace(task, width_m=-1.)], FIELDS, SCALES)
    mapping = {name: getattr(task, name) for name in FIELDS}
    assert torch.equal(task_feature_values([mapping], FIELDS, SCALES), task_feature_values([task], FIELDS, SCALES))


def test_complete_reference_stays_exactly_frozen_after_current_update():
    torch.manual_seed(42)
    task = Task()
    current = policy()
    current.freeze_reference()
    ref = current.reference_sha256()
    assert ref == current.current_sha256()
    sample, hidden, logits, _ = current.propose([task], samples=4, generator=torch.Generator().manual_seed(4))
    initial, _ = current.reference_kl([task], hidden, logits, sample.actions, samples=4)
    assert abs(float(initial.detach())) < 1e-6
    loss = logits[0, 0] + current.numeric.log_density(hidden, sample.actions[:1], sample.values[:1].detach()).sum()
    loss.backward()
    torch.optim.SGD([p for p in current.parameters() if p.requires_grad], lr=.01).step()
    assert current.reference_sha256() == ref and current.current_sha256() != ref
    assert all(p.grad is None for p in current.frozen_encoder.parameters())
    sample, hidden, logits, _ = current.propose([task], samples=4)
    kl, _ = current.reference_kl([task], hidden, logits, sample.actions, samples=4)
    assert float(kl.detach()) > 0


def test_checkpoint_restores_same_random_density_and_exclusive_destination(tmp_path):
    torch.manual_seed(42)
    task = Task()
    current = policy()
    target = tmp_path / "policy"
    current.save_policy(target)
    restored = policy()
    restored.load_policy(target)
    assert current.current_sha256() == restored.current_sha256()
    first, *_ = current.propose([task], samples=4, generator=torch.Generator().manual_seed(13))
    second, *_ = restored.propose([task], samples=4, generator=torch.Generator().manual_seed(13))
    assert torch.equal(first.actions, second.actions) and torch.equal(first.values, second.values)
    assert torch.equal(first.log_prob, second.log_prob)
    with pytest.raises(FileExistsError):
        current.save_policy(target)
    other = StructuredDesignPolicy(((.06, .22), (.25, .75)), fields=FIELDS[:-1], scales=SCALES[:-1])
    with pytest.raises(ValueError, match="configuration"):
        other.load_policy(target)


def test_shared_loss_has_encoder_action_and_conditional_numeric_gradient():
    torch.manual_seed(42)
    task = Task()
    current = policy()
    sample, *_ = current.propose([task], samples=4, generator=torch.Generator().manual_seed(3))
    speed = sample.values[:, 0].detach()
    known = -task.control_weight * (speed / .15).square()
    known_gradient = torch.zeros_like(sample.values)
    known_gradient[:, 0] = -2 * task.control_weight * speed / .15 ** 2
    loss, _ = joint_policy_loss(sample, method="continuous-lax", hf_rewards=[-.2, float("nan"), -.3, float("nan")],
                                lf_rewards=[-.18, -.15, -.29, -.17], selected=[True, False, True, False],
                                probabilities=[.5] * 4, proxy_gradients=[[.1, -.2]] * 4, known_rewards=known,
                                known_gradients=known_gradient)
    loss.backward()
    for module in current.current_modules().values():
        norm = sum(float(p.grad.square().sum()) for p in module.parameters() if p.grad is not None)
        assert norm > 0


def test_three_structural_actions_are_supported():
    current = StructuredDesignPolicy(((0., 1.),), fields=FIELDS, scales=SCALES, action_count=3)
    sample, hidden, logits, _ = current.propose([Task()], samples=6, generator=torch.Generator().manual_seed(1))
    assert logits.shape == (1, 3) and set(sample.actions.tolist()) <= {0, 1, 2}
    current.freeze_reference()
    kl, meta = current.reference_kl([Task()], hidden, logits, sample.actions, samples=6)
    assert abs(float(kl.detach())) < 1e-6 and "3-action" in meta["continuous_KL_expectation"]
