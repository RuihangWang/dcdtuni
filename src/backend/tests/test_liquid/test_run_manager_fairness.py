"""A command/pause queued during a fastest solve owns the next step boundary."""
from pathlib import Path
import threading

import pytest

from backend.liquidcooling.run_manager import LiquidRunManager, _RunRecord, _RunLock


class GatedSimulation:
    """Block one in-flight numerical step while HTTP mutations queue."""
    run_id = 'step-boundary-fairness'
    config_hash = 'test-only'
    metadata = {'dt_s': 1., 'execution': {'max_threads': 1}}

    def __init__(self):
        self.entered = threading.Event()
        self.release_step = threading.Event()
        self.seq = 0
        self.pending = {}
        self.accepted = []

    @property
    def done(self):
        return self.seq == 3

    @property
    def latest_frame(self):
        return {'seq': self.seq, 'time_s': float(self.seq)}

    def step(self):
        if self.seq == 0:
            self.entered.set()
            assert self.release_step.wait(5), 'test failed to release numerical step'
        self.seq += 1
        self.accepted.append(dict(self.pending))

    def set_heat_multiplier(self, value):
        self.set_inputs(heat_multiplier=value)

    def set_inputs(self, **commands):
        assert not self.done, 'experiment has no future step'
        self.pending.update({key: value for key, value in commands.items() if value is not None})


def running(monkeypatch, tmp_path):
    manager = LiquidRunManager()
    simulation = GatedSimulation()
    record = _RunRecord(simulation, tmp_path, tmp_path / 'run', None)
    manager._records[simulation.run_id] = record
    monkeypatch.setattr(manager, '_persist', lambda record: None)
    manager.operate(simulation.run_id, 'start')  # fastest, without a timed wait
    assert simulation.entered.wait(5)
    return manager, simulation, record


def queued(record):
    # Observe arrival while the first numerical step still owns the lock.
    # The test does not assume CPython's scheduler gives a waiter preference.
    with record.lock._condition:
        assert record.lock._condition.wait_for(lambda: len(record.lock._waiters) > 0, 5)


@pytest.mark.parametrize('command', [
    {'heat_multiplier': 1.2}, {'pump_rpm_ratios': {'pump': .95}},
    {'valve_openings': {'valve': .8}}, {'primary_inlet_K': {'hx': 295.}},
])
def test_fastest_worker_hands_next_boundary_to_waiting_input(monkeypatch, tmp_path, command):
    manager, simulation, record = running(monkeypatch, tmp_path)
    answers, errors = [], []
    def submit():
        try:
            answers.append(manager.set_inputs(simulation.run_id, **command))
        except BaseException as error:
            errors.append(error)
    request = threading.Thread(target=submit)
    request.start()
    queued(record)
    simulation.release_step.set()
    request.join(5)
    worker = record.worker
    if worker is not None:
        worker.join(5)
    assert not request.is_alive()
    assert not errors
    assert answers[0]['frame']['seq'] == 1
    assert simulation.accepted[0] == {}
    assert simulation.accepted[1] == command
    assert simulation.accepted[2] == command


@pytest.mark.parametrize('operation,status', [('pause', 'paused'), ('cancel', 'cancelled')])
def test_fastest_worker_honours_waiting_stop_at_first_boundary(monkeypatch, tmp_path, operation, status):
    manager, simulation, record = running(monkeypatch, tmp_path)
    answers = []
    request = threading.Thread(target=lambda: answers.append(manager.operate(simulation.run_id, operation)))
    request.start()
    queued(record)
    simulation.release_step.set()
    request.join(5)
    assert not request.is_alive()
    assert answers[0]['status'] == status
    assert answers[0]['frame']['seq'] == 1
    assert not manager.worker_alive(simulation.run_id)


def test_run_lock_is_reentrant_and_rejects_foreign_release():
    lock = _RunLock()
    errors = []
    with lock:
        with lock:
            def wrong_owner():
                try:
                    lock.release()
                except RuntimeError as error:
                    errors.append(str(error))
            thread = threading.Thread(target=wrong_owner)
            thread.start()
            thread.join(5)
            assert not thread.is_alive()
        assert lock._owner == threading.get_ident()
    assert lock._owner is None
    assert errors == ['cannot release a run lock owned by another thread']