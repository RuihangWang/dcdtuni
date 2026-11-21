"""Acquisition: register q and the predictions first, draw the purchases, and only then accept outcomes.

The ledger the research protocol asks for (``docs/research/ispo``: "record
the predictions and inclusion probabilities before the target outcome is
seen"). For one group of proposals:

1. :meth:`Acquisition.register` writes ``registration.json`` -- proposal ids,
   inclusion probabilities ``q`` (given, or from
   :func:`agent.optim.estimators.allocation_probabilities`), every predicted
   quantity, costs and the policy identity -- exclusively, before anything is
   bought;
2. :meth:`Acquisition.draw` makes one independent Bernoulli draw per proposal
   with exactly those ``q`` from a recorded seed and writes ``draw.json``
   (bound to the registration's SHA-256);
3. :meth:`Acquisition.record_outcome` appends an outcome only for a purchased
   proposal, only after both files exist unchanged, once; a purchased failure
   must carry a finite target value and its measured cost (nothing is dropped
   or imputed);
4. :meth:`Acquisition.close` checks every purchase has its outcome and writes
   ``closed.json``.

Files live under ``<store>/acquisition/<name>/<group>/`` of a
:class:`agent.records.RunStore`, so nothing is ever overwritten.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

import numpy as np

from agent.records.store import RunStore, checked_id, json_bytes, timestamp_sgt

REGISTRATION_FORMAT = "agent-acquisition-registration/v1"
DRAW_FORMAT = "agent-acquisition-draw/v1"
CLOSE_FORMAT = "agent-acquisition-close/v1"


class AcquisitionError(RuntimeError):
    """An outcome or draw out of the registered order."""


@dataclass
class AcquisitionGroup:
    name: str
    path: Path  # relative to the store root
    proposals: tuple
    q: np.ndarray
    registration_sha256: str
    selected: Optional[np.ndarray] = None
    outcomes: Dict[str, Dict[str, Any]] = field(default_factory=dict)


class Acquisition:
    """A "register, then buy" ledger of HF purchases on one :class:`RunStore`."""

    def __init__(self, store: RunStore, name: str, *, policy_identity: Mapping[str, Any]):
        if not policy_identity:
            raise ValueError("the proposal policy's identity is required (fixed before proposals)")
        self.store = store
        self.name = checked_id(name, "acquisition name")
        self.policy_identity = dict(policy_identity)
        self.root = Path("acquisition") / self.name

    def _group_path(self, group_id: str) -> Path:
        return self.root / checked_id(group_id, "group id")

    def register(self, group_id: str, proposals: Sequence[str], *, predictions: Mapping[str, Sequence[float]],
                 q: Optional[Sequence[float]] = None, discrepancy: Optional[Sequence[float]] = None,
                 hf_cost: Any = None, expected_hf_count: Optional[float] = None,
                 expected_hf_cost_budget: Optional[float] = None, q_min: float = .05,
                 **fields: Any) -> AcquisitionGroup:
        """Write the registration of one group; ``q`` given or allocated from ``discrepancy``/``hf_cost``."""
        proposals = tuple(str(p) for p in proposals)
        if not proposals or len(set(proposals)) != len(proposals):
            raise ValueError("a group needs distinct proposal ids")
        n = len(proposals)
        allocation: Dict[str, Any] = {}
        if q is None:
            if discrepancy is None or hf_cost is None:
                raise ValueError("give q, or the predicted squared discrepancy and HF cost to allocate it")
            from agent.optim.estimators import allocation_probabilities
            q = allocation_probabilities(discrepancy, hf_cost, expected_hf_count=expected_hf_count,
                                         expected_hf_cost_budget=expected_hf_cost_budget, q_min=q_min).numpy()
            allocation = {"rule": "allocation_probabilities (q proportional to sqrt(a/c), floor q_min)",
                          "discrepancy": [float(v) for v in discrepancy],
                          "hf_cost": np.broadcast_to(np.asarray(hf_cost, dtype=float), (n,)).tolist(),
                          "expected_hf_count": expected_hf_count,
                          "expected_hf_cost_budget": expected_hf_cost_budget, "q_min": q_min}
        q = np.asarray(q, dtype=float)
        if q.shape != (n,) or not np.isfinite(q).all() or (q <= 0).any() or (q > 1).any():
            raise ValueError("one inclusion probability in (0, 1] per proposal")
        predicted: Dict[str, list] = {}
        for key, values in dict(predictions).items():
            values = np.asarray(values, dtype=float)
            if values.shape != (n,) or not np.isfinite(values).all():
                raise ValueError(f"prediction {key!r} needs one finite value per proposal")
            predicted[str(key)] = values.tolist()
        record = {"format": REGISTRATION_FORMAT, "acquisition": self.name, "group": group_id,
                  "registered_at_sgt": timestamp_sgt(), "proposals": list(proposals), "q": q.tolist(),
                  "predictions": predicted, "allocation": allocation, "policy_identity": self.policy_identity,
                  "outcomes_read": False, **fields}
        payload = json_bytes(record)
        path = self._group_path(group_id)
        self.store.make_directory("acquisition", exclusive=False)
        self.store.make_directory(self.root, exclusive=False)
        self.store.make_directory(path)
        self.store.write_bytes(path / "registration.json", payload)
        self.store.write_bytes(path / "outcomes.jsonl", b"")
        return AcquisitionGroup(group_id, path, proposals, q, RunStore.sha256(payload))

    def _check_registration(self, group: AcquisitionGroup) -> None:
        if RunStore.sha256(self.store.read_bytes(group.path / "registration.json")) != group.registration_sha256:
            raise AcquisitionError("registration changed after it was written")

    def draw(self, group: AcquisitionGroup, *, seed: int) -> np.ndarray:
        """One independent Bernoulli(q_i) per proposal from ``numpy.random.default_rng(seed)``; saved first."""
        self._check_registration(group)
        selected = np.random.default_rng(seed).random(len(group.q)) < group.q
        self.store.write_json(group.path / "draw.json", {
            "format": DRAW_FORMAT, "drawn_at_sgt": timestamp_sgt(), "seed": int(seed),
            "rule": "numpy.random.default_rng(seed).random(n) < q",
            "registration_sha256": group.registration_sha256, "selected": [bool(s) for s in selected]})
        group.selected = selected
        return selected

    def record_outcome(self, group: AcquisitionGroup, proposal: str, *, outcome: float, costs: Mapping[str, Any],
                       status: str, **fields: Any) -> Dict[str, Any]:
        """Append the outcome of one purchased proposal (failures with a finite target value)."""
        if group.selected is None or not (self.store.root / group.path / "draw.json").exists():
            raise AcquisitionError("no saved draw for this group: register and draw before reading outcomes")
        self._check_registration(group)
        if proposal not in group.proposals:
            raise AcquisitionError(f"{proposal!r} is not a registered proposal")
        index = group.proposals.index(proposal)
        if not group.selected[index]:
            raise AcquisitionError(f"{proposal!r} was not purchased by the recorded draw")
        if proposal in group.outcomes:
            raise AcquisitionError(f"{proposal!r} already has an outcome")
        if isinstance(outcome, bool) or not isinstance(outcome, (int, float)) or not math.isfinite(outcome):
            raise AcquisitionError("a purchased outcome must be a finite target value, failures included")
        if not costs:
            raise AcquisitionError("a purchased outcome needs its measured cost")
        record = {"proposal": proposal, "index": index, "q": float(group.q[index]), "outcome": float(outcome),
                  "status": status, "costs": dict(costs), "recorded_at_sgt": timestamp_sgt(), **fields}
        self.store.append_bytes(group.path / "outcomes.jsonl",
                                (json.dumps(record, sort_keys=True, allow_nan=False) + "\n").encode("utf-8"))
        group.outcomes[proposal] = record
        return record

    def close(self, group: AcquisitionGroup) -> Dict[str, Any]:
        """Every purchase has its outcome; write ``closed.json`` once."""
        if group.selected is None:
            raise AcquisitionError("nothing drawn")
        bought = [p for p, s in zip(group.proposals, group.selected) if s]
        missing = [p for p in bought if p not in group.outcomes]
        if missing:
            raise AcquisitionError(f"purchased proposals missing an outcome: {missing}")
        wall = sum(float(row["costs"].get("wall_seconds", 0.)) for row in group.outcomes.values())
        summary = {"format": CLOSE_FORMAT, "closed_at_sgt": timestamp_sgt(), "proposals": len(group.proposals),
                   "purchased": len(bought), "failed": sum(row["status"] != "succeeded"
                                                            for row in group.outcomes.values()),
                   "charged_wall_seconds": wall, "expected_purchases": float(group.q.sum()),
                   "registration_sha256": group.registration_sha256}
        self.store.write_json(group.path / "closed.json", summary)
        return summary

    def group(self, group_id: str) -> AcquisitionGroup:
        """Reopen a saved group (registration, draw and outcomes so far), checking they still agree.

        The draw must name this registration's SHA-256 and hold one draw per
        proposal; every saved outcome must be a purchased proposal, once.
        """
        path = self._group_path(group_id)
        payload = self.store.read_bytes(path / "registration.json")
        record = json.loads(payload)
        group = AcquisitionGroup(group_id, path, tuple(record["proposals"]), np.asarray(record["q"], dtype=float),
                                 RunStore.sha256(payload))
        if (self.store.root / path / "draw.json").exists():
            draw = self.store.read_json(path / "draw.json")
            if draw.get("registration_sha256") != group.registration_sha256:
                raise AcquisitionError(f"{group_id}: the saved draw is not bound to this registration")
            selected = np.asarray(draw.get("selected") or [], dtype=bool)
            if selected.shape != (len(group.proposals),):
                raise AcquisitionError(f"{group_id}: the saved draw does not hold one draw per proposal")
            group.selected = selected
        for line in self.store.read_bytes(path / "outcomes.jsonl").decode("utf-8").splitlines():
            row = json.loads(line)
            proposal = row.get("proposal")
            if group.selected is None or proposal not in group.proposals \
                    or not group.selected[group.proposals.index(proposal)]:
                raise AcquisitionError(f"{group_id}: saved outcome for {proposal!r}, which no saved draw purchased")
            if proposal in group.outcomes:
                raise AcquisitionError(f"{group_id}: {proposal!r} has an outcome twice")
            group.outcomes[proposal] = row
        return group
