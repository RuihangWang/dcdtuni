"""Pure Adam ODIL solver — all variables optimised simultaneously."""

import time
from dataclasses import asdict
from pathlib import Path
from typing import Callable

import torch

from backend.liquidcooling.schema import SolverConfig
from backend.liquidcooling.solver.common import SolveLog, print_log, save_loss_csv


def solve(
    model,
    config: SolverConfig,
    log_dir: Path = None,
    callback: Callable = None,
    print_every: int = 100,
    bc_enforcer=None,
) -> list:
    """Run Adam optimisation to minimise physics residuals.

    Features
    --------
    - ReduceLROnPlateau scheduler (patience=200, factor=0.5, min_lr=1e-6)
    - Gradient clipping (max_norm=1.0)
    - Convergence check: total_loss < convergence_tol -> break early
    """
    optimizer = torch.optim.Adam(model.parameters(), lr=config.lr)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, patience=200, factor=0.5, min_lr=1e-6,
    )

    if bc_enforcer is not None:
        bc_enforcer.capture_init_flow_signs(model.state)

    logs: list[SolveLog] = []
    t_start = time.time()

    for epoch in range(config.max_epochs):
        optimizer.zero_grad()
        losses = model()
        losses["total"].backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
        optimizer.step()
        if bc_enforcer is not None:
            bc_enforcer.enforce(model.state)
        scheduler.step(losses["total"])

        total_val = losses["total"].item()

        if epoch % print_every == 0 or epoch == config.max_epochs - 1:
            log = SolveLog(
                epoch=epoch,
                total=total_val,
                losses={k: v.item() for k, v in losses.items()
                        if k != "total"},
                wall_time_s=time.time() - t_start,
            )
            logs.append(log)
            if callback:
                callback(asdict(log))
            else:
                print_log(log)

        if total_val < config.convergence_tol:
            break

    if log_dir is not None:
        log_dir = Path(log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        save_loss_csv(logs, log_dir / "loss_history.csv")
        torch.save(model.state_dict(), log_dir / "model_state.pt")

    return logs
