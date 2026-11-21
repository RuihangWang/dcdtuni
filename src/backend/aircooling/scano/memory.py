"""GPU memory of an AI solve, and which deployment (infer.MODES) a request gets.

The peak of one prediction is linear in two counts (fit on c118, RTX 4090, docs/benchmarks/scano-tta-2026-10-29):

    peak = node_bytes x N + row_bytes x rows

N is the room's node count (cells plus boundary faces: the room the data class holds on the GPU), rows the rows of
the largest forward: N for the whole-room forward, a chunk's subgraph plus its anchors for TTA. A card's "memory"
entry holds the coefficients and the TTA rows as rows0 + rows_per_node x N (the anchors of a run whose
context.anchor_ratio is a fraction of the room grow with it; a fixed anchor count does not). A work directory
remembers what its runs measured (base/memory.json), which replaces the estimate for that room.

A card whose anchors are a fraction of the room ("memory": {"anchor_fraction": true}) has them capped in TTA at a
training room's count (infer.anchor_scale_for), and its anchor rows scale with it.

A card's "inference": {"auto": "tta"} makes auto take TTA whenever it fits (a model whose whole-room forward is
far slower than TTA at the same error: AB-UPT, whose every query attends to every node of the room).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional

AUTO = 'auto'
REQUESTS = ('auto', 'whole', 'tta')
MARGIN = 1.10                # estimate x margin must fit the free memory
MIN_CHUNK_SHARE = 0.125      # TTA chunks shrink to fit, to no less than this share of a training draw

DEFAULT = {'node_bytes': 216, 'row_bytes': 7680, 'tta_rows0': 315_000, 'tta_rows_per_node': 0.1232}


def coefficients(card: Dict) -> Dict:
    return {**DEFAULT, **(card.get('memory') or {})}


def measured(base: Optional[Path]) -> Dict:
    """{mode: {'peak_bytes', 'rows', 'chunk'}} a work directory's runs recorded (record_peak)."""
    try:
        return json.loads((Path(base) / 'memory.json').read_text()) if base else {}
    except (OSError, ValueError):
        return {}


def record_peak(base: Path, mode: str, peak_bytes: Optional[int], rows: int, chunk: Optional[int]) -> None:
    if not peak_bytes:
        return
    seen = measured(base)
    seen[mode] = {'peak_bytes': int(peak_bytes), 'rows': int(rows), 'chunk': chunk}
    (Path(base) / 'memory.json').write_text(json.dumps(seen, indent=1) + '\n')


def estimate(card: Dict, n_nodes: int, mode: str, base: Optional[Path] = None, chunk_share: float = 1.0,
             n_boundary: Optional[int] = None) -> int:
    """Bytes the prediction of an n_nodes room peaks at in `mode` (a measured peak of the same room when there is
    one, for a full-size chunk). n_boundary: its boundary faces, which the boundary-record law needs."""
    seen = measured(base).get(mode)
    if seen and (mode == 'whole' or chunk_share == 1.0):
        return int(seen['peak_bytes'])
    c = coefficients(card)
    if 'query_row_bytes' in c:
        return _records_law(c, n_nodes, mode, chunk_share, n_boundary)
    if mode == 'whole':
        rows = n_nodes
    else:
        anchors = c['tta_rows_per_node'] * n_nodes
        if c.get('anchor_fraction'):
            from .infer import anchor_scale_for
            anchors *= anchor_scale_for(n_nodes)
        rows = min(n_nodes, (c['tta_rows0'] * chunk_share) + anchors)
    return int(c['node_bytes'] * n_nodes + c['row_bytes'] * rows)


def _records_law(c: Dict, n_nodes: int, mode: str, chunk_share: float, n_boundary: Optional[int]) -> int:
    """A model whose every query row attends to the room's boundary records (SCANO's BQA): a query row costs
    query_row_bytes + query_row_bytes_per_mface x (boundary faces / 1e6), an anchor (context-only) row
    anchor_row_bytes. Fit on c118 (RTX 4090): DCTS_0112 (0.49 M faces), Base2-9 (2.18 M), Alibaba-hongchou
    (4.08 M; one chunk of 534 k query and 141 k anchor rows: 11.2 GiB above the room's data)."""
    from .infer import TRAINING_NODES, anchor_scale_for
    faces = n_boundary if n_boundary is not None else int(c.get('boundary_share', 0.3) * n_nodes)
    per_query = c['query_row_bytes'] + c['query_row_bytes_per_mface'] * faces / 1e6
    if mode == 'whole':
        return int(c['node_bytes'] * n_nodes + per_query * n_nodes)
    pivots = TRAINING_NODES * c['sample_ratio'] * chunk_share
    query = min(n_nodes, c['rows_per_pivot'] * pivots)
    scale = anchor_scale_for(n_nodes) if c.get('anchor_fraction') else 1.0
    anchors = min(n_nodes, c['rows_per_pivot'] * c['anchor_ratio'] * n_nodes * scale)
    return int(c['node_bytes'] * n_nodes + per_query * query + c['anchor_row_bytes'] * anchors)


def choose(card: Dict, n_nodes: int, free_bytes: Optional[int], request: str = AUTO,
           base: Optional[Path] = None, n_boundary: Optional[int] = None) -> Dict:
    """{'mode', 'chunk_share', 'estimate_bytes', 'fits', 'reason'} of a request ("auto", "whole" or "tta") on a
    device with free_bytes (None: unknown, e.g. the CPU; auto then keeps the whole-room forward)."""
    if request not in REQUESTS:
        raise ValueError(f'inference mode {request!r} (one of {REQUESTS})')
    whole = estimate(card, n_nodes, 'whole', base, n_boundary=n_boundary)
    tta = estimate(card, n_nodes, 'tta', base, n_boundary=n_boundary)

    def fits(b):
        return free_bytes is None or b * MARGIN <= free_bytes
    prefers_tta = (card.get('inference') or {}).get('auto') == 'tta'
    if request == AUTO and prefers_tta and fits(tta):
        return {'mode': 'tta', 'chunk_share': 1.0, 'estimate_bytes': tta, 'fits': True, 'reason': 'model prefers TTA',
                'whole_estimate_bytes': whole}
    if request == 'whole' or (request == AUTO and fits(whole)):
        return {'mode': 'whole', 'chunk_share': 1.0, 'estimate_bytes': whole, 'fits': fits(whole),
                'reason': 'requested' if request == 'whole' else 'fits'}
    share = 1.0
    while not fits(estimate(card, n_nodes, 'tta', base, share, n_boundary)) and share / 2 >= MIN_CHUNK_SHARE:
        share /= 2
    est = estimate(card, n_nodes, 'tta', base, share, n_boundary)
    return {'mode': 'tta', 'chunk_share': share, 'estimate_bytes': est, 'fits': fits(est),
            'reason': 'requested' if request == 'tta' else 'whole does not fit', 'whole_estimate_bytes': whole}
