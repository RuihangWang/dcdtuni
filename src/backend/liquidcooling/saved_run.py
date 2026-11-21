"""A finished DAE run rebuilt from the files LiquidRunManager persisted.

A server restart or a page reload loses the in-memory run record, but the run
directory keeps everything the record served: the original input
(``input.liquidrun``), the record header (``input.json``), the replay
checkpoint, the result, and the accepted input with its receipt
(``executed.liquidrun`` + ``executed-inputs.json``). ``SavedDAERun.load``
re-checks every identity link between them and then serves the same read
interface as ``DAESimulation`` (result, frames, checkpoint, executed manifest).

Nothing is re-solved. A run made on a GPU therefore restores on a host
without one, and the restored numbers are the saved numbers the user saw.
The trust boundary is the case directory: the result's values are not
re-derived, but the hybrid handoff still re-runs its full heat audit
(per-server closure, accepted-input and original hashes, project hash) on
them, exactly as for a live run.

Only terminal runs (completed, cancelled, failed) restore, and only
read-only: continuing a transient would need the solver state, which a JSON
checkpoint can only replay (``DAESimulation.from_checkpoint``) on the device
the run declared.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from backend.liquidcooling.liquidrun_dae_schema import parse_dae_manifest
from backend.liquidcooling.runtime import _canonical_hash

TERMINAL_STATUSES = ('completed', 'cancelled', 'failed')
ARTIFACTS = ('input.liquidrun', 'input.json', 'checkpoint.json', 'result.json',
             'executed.liquidrun', 'executed-inputs.json')
DAE_CHECKPOINT_FORMAT = 'liquid-dae-replay-checkpoint'
DAE_SOLVER = 'differentiable_newton_dae'
RECEIPT_FORMAT = 'liquid-accepted-inputs/v1'

# Frame key -> result.json key of the same [component][sample] matrix.
_FRAME_SERIES = {
    'node_pressure_Pa': 'node_pressure_Pa', 'edge_flow_kg_s': 'edge_flow_kg_s',
    'node_temperature_K': 'node_temperature_K', 'cell_temperature_K': 'cell_temp_K',
    'primary_return_K': 'primary_return_K', 'secondary_supply_K': 'secondary_supply_K',
    'chip_temp_K': 'chip_temp_K', 'edge_outlet_temp_K': 'edge_outlet_temp_K',
}


# The constitutive laws of a run saved before its provenance named them.
# Each solver_laws key entered the provenance in the same 2026-10-21 commit as
# the law change it records (pump_law e59f53259, hydrostatics 5444a1151,
# hx_models 161c77488), so a missing key means the run used the only law the
# solver had before: a fact about the code, not a guess about the run. The
# friction cap (a10090ba8) came five minutes after the first record and its key
# much later, so friction_law is inferred only when pump_law is missing too.
_LAWS_BEFORE_VERSIONING = {
    'pump_law': 'affinity_N2_times_H0_minus_aQ2/v1',
    'hydrostatics': 'pipes_only_rho_g_dh/v1',
    'friction_law': 'laminar64_swamee_jain_blend_uncapped/v1',
}
# tee_law entered the provenance with the v2 rule (2026-10-25); every run saved
# without it was compiled by the only rule the compiler had before.
_TEE_LAW_BEFORE_VERSIONING = 'tee_K0p9_per_pipe_endpoint/v1'


def _with_solver_laws(provenance, hx_ids) -> dict:
    """``provenance`` with every inferable missing solver law filled in and
    listed under ``solver_laws.inferred``; unchanged when nothing is missing."""
    if not isinstance(provenance, dict):
        return provenance
    recorded = provenance.get('solver_laws')
    laws = dict(recorded) if isinstance(recorded, dict) else {}
    predates = 'pump_law' not in laws
    inferred = []
    for key, value in _LAWS_BEFORE_VERSIONING.items():
        if key not in laws and (predates or key != 'friction_law'):
            laws[key] = value
            inferred.append(key)
    if 'hx_models' not in laws:
        laws['hx_models'] = {hx: 'mixed-volumes' for hx in hx_ids}
        inferred.append('hx_models')
    if 'tee_law' not in laws:
        laws['tee_law'] = _TEE_LAW_BEFORE_VERSIONING
        inferred.append('tee_law')
    if not inferred:
        return provenance
    laws['inferred'] = sorted(inferred)
    return {**provenance, 'solver_laws': laws}


class SavedRunInvalid(ValueError):
    """The saved artifacts are missing, unreadable or do not belong together."""


class SavedRunNotTerminal(RuntimeError):
    """The run was still ready/running/paused when it was last saved."""


def _read(run_dir: Path, name: str):
    path = run_dir / name
    if not path.is_file():
        raise SavedRunInvalid(f'saved run {run_dir.name} lacks {name}')
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise SavedRunInvalid(f'{name} of saved run {run_dir.name} is not readable JSON: {exc}') from exc


def _object(value, label: str) -> dict:
    if not isinstance(value, dict):
        raise SavedRunInvalid(f'{label} must be a JSON object')
    return value


def _column(matrix, sample: int, label: str) -> list:
    if not isinstance(matrix, list):
        raise SavedRunInvalid(f'result.json {label} must be a [component][sample] list')
    out = []
    for row in matrix:
        if not isinstance(row, list) or sample >= len(row):
            raise SavedRunInvalid(f'result.json {label} does not cover sample {sample}')
        out.append(row[sample])
    return out


class SavedDAERun:
    """Read-only stand-in for ``DAESimulation`` over verified saved files."""

    read_only = True

    def __init__(self, *, run_id: str, config_hash: str, metadata: dict, result: dict,
                 checkpoint: dict, executed: dict, status: str, error, project_revision):
        self.run_id = run_id
        self.config_hash = config_hash
        self._metadata = metadata
        self._result = result
        self._checkpoint = checkpoint
        self._executed = executed
        self.status = status
        self.error = error
        self.project_revision = project_revision
        self._frames = None

    # ------------------------------------------------------------------ load
    @classmethod
    def load(cls, run_dir: Path, run_id: str) -> 'SavedDAERun':
        run_dir = Path(run_dir)
        checkpoint = _object(_read(run_dir, 'checkpoint.json'), 'checkpoint.json')
        if checkpoint.get('format') != DAE_CHECKPOINT_FORMAT:
            raise SavedRunInvalid(
                f'saved run {run_id} is not a DAE run (checkpoint format {checkpoint.get("format")!r}); '
                'only differentiable Newton DAE runs restore')
        status = checkpoint.get('run_status')
        if status not in TERMINAL_STATUSES:
            raise SavedRunNotTerminal(
                f'saved run {run_id} was {status!r} when last saved; only completed, cancelled or '
                'failed runs restore (read-only)')
        missing = [name for name in ARTIFACTS if not (run_dir / name).is_file()]
        if missing:
            raise SavedRunInvalid(f'saved run {run_id} lacks {", ".join(missing)}')
        if checkpoint.get('run_id') != run_id:
            raise SavedRunInvalid(f'checkpoint.json names run id {checkpoint.get("run_id")!r}, not {run_id!r}')
        config_hash = checkpoint.get('config_hash')
        if not isinstance(checkpoint.get('liquidrun'), dict) or \
                _canonical_hash(checkpoint['liquidrun'], []) != config_hash:
            raise SavedRunInvalid('checkpoint.json config hash does not match its own manifest')
        seq = checkpoint.get('seq')
        if isinstance(seq, bool) or not isinstance(seq, int) or seq < 0:
            raise SavedRunInvalid('checkpoint.json seq must be a nonnegative integer')
        multipliers = checkpoint.get('heat_multipliers')
        if not isinstance(multipliers, list) or len(multipliers) != seq + 1:
            raise SavedRunInvalid('checkpoint.json heat multipliers do not cover its accepted samples')

        original = _object(_read(run_dir, 'input.liquidrun'), 'input.liquidrun')
        try:
            public = parse_dae_manifest(original).model_dump(by_alias=True, mode='json', exclude_none=True)
        except ValueError as exc:
            raise SavedRunInvalid(f'input.liquidrun is not a valid DAE manifest: {exc}') from exc
        if _canonical_hash(public, []) != config_hash:
            raise SavedRunInvalid('input.liquidrun config hash does not match the checkpoint')

        header = _object(_read(run_dir, 'input.json'), 'input.json')
        metadata = _object(header.get('metadata'), 'input.json metadata')
        if header.get('run_id') != run_id:
            raise SavedRunInvalid(f'input.json names run id {header.get("run_id")!r}, not {run_id!r}')
        if header.get('config_hash') != config_hash or metadata.get('config_hash') != config_hash:
            raise SavedRunInvalid('input.json config hash does not match the checkpoint')
        revision = header.get('project_revision')
        if revision is not None and (isinstance(revision, bool) or not isinstance(revision, int)):
            raise SavedRunInvalid('input.json project_revision must be an integer or null')

        result = _object(_read(run_dir, 'result.json'), 'result.json')
        if result.get('run_id') != run_id:
            raise SavedRunInvalid(f'result.json names run id {result.get("run_id")!r}, not {run_id!r}')
        if result.get('config_hash') != config_hash:
            raise SavedRunInvalid('result.json config hash does not match the checkpoint')
        if result.get('solver') != DAE_SOLVER:
            raise SavedRunInvalid(f'result.json solver is {result.get("solver")!r}, not {DAE_SOLVER}')
        if result.get('status') != status or result.get('completed') is not (status == 'completed'):
            raise SavedRunInvalid(f'result.json status {result.get("status")!r} does not match the '
                                  f'checkpoint run status {status!r}')
        if result.get('error') != checkpoint.get('error'):
            raise SavedRunInvalid('result.json error does not match the checkpoint error')
        times = result.get('time_s')
        if not isinstance(times, list) or len(times) != seq + 1:
            raise SavedRunInvalid(f'result.json has {len(times) if isinstance(times, list) else 0} samples, '
                                  f'the checkpoint accepted {seq + 1}')
        if result.get('heat_multipliers') != multipliers:
            raise SavedRunInvalid('result.json heat multipliers differ from the checkpoint')
        if result.get('node_ids') != metadata.get('node_ids') or result.get('edge_ids') != metadata.get('edge_ids'):
            raise SavedRunInvalid('result.json node/edge ids differ from the run metadata')
        controls = result.get('controls')
        diagnostics = result.get('diagnostics')
        if not isinstance(controls, list) or not isinstance(diagnostics, list) or \
                len(controls) != seq + 1 or len(diagnostics) != seq + 1:
            raise SavedRunInvalid('result.json controls/diagnostics do not cover every sample')

        receipt = _object(_read(run_dir, 'executed-inputs.json'), 'executed-inputs.json')
        if receipt.get('format') != RECEIPT_FORMAT or receipt.get('input_file') != 'executed.liquidrun':
            raise SavedRunInvalid('executed-inputs.json has an unknown format or input_file')
        if receipt.get('run_id') != run_id:
            raise SavedRunInvalid('executed-inputs.json names a different run id')
        if receipt.get('original_config_hash') != config_hash:
            raise SavedRunInvalid('executed-inputs.json original_config_hash does not match the original input')
        executed = _object(_read(run_dir, 'executed.liquidrun'), 'executed.liquidrun')
        if _canonical_hash(executed, []) != receipt.get('executed_config_hash'):
            raise SavedRunInvalid('executed.liquidrun does not hash to the executed_config_hash of its receipt')
        if receipt.get('accepted_sample_count') != seq + 1:
            raise SavedRunInvalid('executed-inputs.json accepted sample count differs from the checkpoint')
        if executed.get('source_project') != public.get('source_project'):
            raise SavedRunInvalid('executed.liquidrun source_project differs from the original input')

        # A run saved before provenance.solver_laws names the laws it was
        # solved with all the same (see _LAWS_BEFORE_VERSIONING).
        hx_ids = metadata.get('hx_edge_ids') or result.get('hx_edge_ids') or []
        for document in (metadata, result):
            if isinstance(document.get('parameter_provenance'), dict):
                document['parameter_provenance'] = _with_solver_laws(document['parameter_provenance'], hx_ids)

        stored_checkpoint = {k: v for k, v in checkpoint.items() if k not in ('run_status', 'error')}
        run = cls(run_id=run_id, config_hash=config_hash, metadata=metadata, result=result,
                  checkpoint=stored_checkpoint, executed=executed, status=status,
                  error=checkpoint.get('error'), project_revision=revision)
        run.frames  # build and validate every frame now, not on first request
        return run

    # ------------------------------------------------- DAESimulation surface
    @property
    def metadata(self) -> dict:
        return copy.deepcopy(self._metadata)

    @property
    def done(self) -> bool:
        return len(self._result['time_s']) == self._metadata.get('total_sample_count')

    @property
    def frames(self) -> list[dict]:
        if self._frames is None:
            self._frames = [self._frame(sample) for sample in range(len(self._result['time_s']))]
        return copy.deepcopy(self._frames)

    @property
    def latest_frame(self) -> dict:
        if self._frames is None:
            self.frames
        return copy.deepcopy(self._frames[-1])

    def result(self) -> dict:
        return copy.deepcopy(self._result)

    def checkpoint(self) -> dict:
        return copy.deepcopy(self._checkpoint)

    def executed_manifest(self) -> dict:
        return copy.deepcopy(self._executed)

    def _frame(self, sample: int) -> dict:
        """The live frame of ``DAESimulation._frame`` from the saved matrices.

        Per-step Newton reports are not persisted, so a restored frame has no
        ``newton`` entry. ``edge_stagnant`` is a property of the compiled
        equation graph, the same list in every frame of a run.
        """
        result = self._result
        controls = _object(result['controls'][sample], f'result.json controls[{sample}]')
        frame = {
            'kind': 'frame', 'seq': sample, 'time_s': result['time_s'][sample],
            'heat_multiplier': result['heat_multipliers'][sample],
            'pump_rpm_ratios': copy.deepcopy(controls.get('pump_rpm_ratios', {})),
            'valve_openings': copy.deepcopy(controls.get('valve_openings', {})),
            'primary_inlet_K': copy.deepcopy(controls.get('primary_inlet_K', {})),
            **{name: _column(result.get(key), sample, key) for name, key in _FRAME_SERIES.items()},
            'diagnostics': copy.deepcopy(result['diagnostics'][sample]),
            'run_id': self.run_id,
            'controls': copy.deepcopy(controls),
            'cp_edge_ids': list(self._metadata.get('cp_edge_ids', [])),
            'cell_positions': copy.deepcopy(result.get('cell_positions', [])),
        }
        if 'node_observable' in result:
            frame['node_observable'] = _column(result['node_observable'], sample, 'node_observable')
        if 'edge_stagnant' in result:
            frame['edge_stagnant'] = list(result['edge_stagnant'])
        if 'hx_ideal_heating' in result:
            frame['hx_ideal_heating'] = _column(result['hx_ideal_heating'], sample, 'hx_ideal_heating')
        if 'frame_warnings' in result:
            frame['warnings'] = list(result['frame_warnings'][sample])
        if isinstance(result.get('controllers'), dict):
            # The PI loops' per-sample report (DAE runs saved since 2026-10-25).
            frame['controllers'] = {cid: {key: series[key][sample] for key in series}
                                    for cid, series in result['controllers'].items()}
        return frame
