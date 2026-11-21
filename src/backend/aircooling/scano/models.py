"""The checkpoints the AI solve can run, each described by its model_card.json (format in
src/backend/aircooling/scano/vendored/README.md). Nothing else in the pipeline names a model or a dataset.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Dict, List, Optional

from .infer import CHECKPOINTS


def _ref(run_dir: Path, value) -> Optional[Dict]:
    """A card entry that is either an object or the path of a JSON file relative to the checkpoint directory."""
    if value is None or isinstance(value, dict):
        return value
    path = (Path(run_dir) / str(value)).resolve()
    return json.loads(path.read_text()) if path.is_file() else None


def card(model_id: str, root: Path = CHECKPOINTS) -> Dict:
    """The card of a checkpoint with its references resolved: id, run_dir, adapter, first_iteration (recipe),
    training_ranges, mesh, and the display fields."""
    run_dir = Path(root) / model_id
    if not model_id or '/' in model_id or not (run_dir / 'checkpoint.pt').is_file():
        raise ValueError(f'unknown model {model_id!r}')
    raw = json.loads((run_dir / 'model_card.json').read_text()) if (run_dir / 'model_card.json').is_file() else {}
    if not raw.get('adapter'):
        raise ValueError(f'{run_dir}/model_card.json names no adapter')
    if not raw.get('first_iteration'):
        raise ValueError(f'{run_dir}/model_card.json names no first_iteration recipe')
    out = {**raw, 'id': model_id, 'run_dir': str(run_dir),
           'first_iteration': _ref(run_dir, raw['first_iteration']),
           'training_ranges': _ref(run_dir, raw.get('training_ranges'))}
    if out['first_iteration'] is None:
        raise ValueError(f'{run_dir}: first_iteration {raw["first_iteration"]!r} not found')
    return out


def input_key(c: Dict) -> str:
    """What two checkpoints must share to reuse each other's work directory: the adapter (input format) and the
    first-iteration recipe."""
    blob = json.dumps({'adapter': c['adapter'], 'recipe': c['first_iteration']}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


def list_models(root: Path = CHECKPOINTS) -> List[Dict]:
    """The cards of every checkpoint (unreadable ones listed with their error, so the tab can say why)."""
    out = []
    if not Path(root).is_dir():
        return out
    for d in sorted(Path(root).iterdir()):
        if not (d / 'checkpoint.pt').is_file():
            continue
        try:
            c = card(d.name, root)
            c.pop('run_dir', None)
            out.append(c)
        except (ValueError, OSError) as exc:
            out.append({'id': d.name, 'error': str(exc)})
    return out


def recommended(root: Path = CHECKPOINTS) -> str:
    cards = [c for c in list_models(root) if not c.get('error')]
    return next((c['id'] for c in cards if c.get('recommended')), cards[0]['id'] if cards else '')
