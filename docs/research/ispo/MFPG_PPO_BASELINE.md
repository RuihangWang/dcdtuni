# Faithful one-step MFPG-PPO baseline -- preparation2026-10-08

Primary source: https://arxiv.org/html/2610.02505v1
This baseline remains independent from ISPO randomized acquisition/path controls.
Existing scalar-AIPW/LAX pilots must never be relabeled MFPG-PPO.

The one-step design action is the joint structure and physical numerical vector.
HF and coupled LF consume exactly that same hard engineering project. Additional
LF designs come from independent noise under the same behavior policy/context.
Terminal GAE reduces to reward minus a pre-action critic; retain separate HF,
coupled-LF and independent-LF critics. PPO ratios use stored behavior joint density
and fixed physical x, not the reparameterized path of newly proposed x.

Every PPO epoch re-evaluates clipped statistics, fits the scalar-surrogate control,
smooths it and applies evidence/variance fallback. LF mean terms retain actor
gradients; coefficient/critic advantages are detached. BA uses all-LF mean and
pre-collection past correlation/cost with the primary integer repair. Frozen cost
prediction does not make realized wall budget exact; record all full costs.

Prepared code mfpg_ppo.py covers only these scalar/autograd/monitor/allocation
mechanics and five CPU tests. Full actor-critic runner/comparative execution,
equal tuning allocation and complete experimental acceptance remain pending.
