"""Continuing a DAE run from its checkpoint in the SAME case (browser resume).

A checkpoint replays into a new run: the original keeps its id and artifact
directory (before 2026-10-25 the replay took the checkpoint's own id and was
refused there as 'run artifact directory already exists').
"""
import json

from backend.liquidcooling.run_manager import LiquidRunManager
from backend.tests.test_liquid.test_dae_runtime import case


def test_resume_in_the_same_case_is_a_new_run_that_continues_the_old_one(tmp_path):
    manager = LiquidRunManager()
    first = manager.create(case(), case_dir=tmp_path)['run_id']
    manager.set_inputs(first, heat_multiplier=1.2)
    manager.operate(first, 'step')
    original = tmp_path / 'Liquid' / 'log' / f'run-{first}'
    text = (original / 'checkpoint.json').read_text()
    before = sorted(p.name for p in original.iterdir())

    # What the browser sends: only the checkpoint, its liquidrun inside.
    checkpoint = json.loads(text)
    resumed = manager.create(checkpoint['liquidrun'], case_dir=tmp_path, checkpoint=checkpoint)
    assert resumed['run_id'] != first
    assert resumed['metadata']['resumed_from'] == first
    assert resumed['frame']['seq'] == checkpoint['seq']
    assert resumed['frame']['primary_return_K'] == manager.get(first)['frame']['primary_return_K']
    # The resumed run can go on; the original's files are untouched.
    manager.set_inputs(resumed['run_id'], heat_multiplier=.9)
    assert manager.operate(resumed['run_id'], 'step')['frame']['seq'] == checkpoint['seq'] + 1
    assert sorted(p.name for p in original.iterdir()) == before
    assert (original / 'checkpoint.json').read_text() == text
