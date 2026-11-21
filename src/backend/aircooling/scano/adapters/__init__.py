"""Adapters: the one place an input format (and the forward code reading it) is known.

The pipeline around a model is format-free: the case's first iteration (run with the card's recipe), the joined
OpenFOAM graph (foam_mesh) and its boundary values (foam_fields), work directories, result directories in the
solver's own layout (foam_writer), post-processing injection. What a checkpoint reads and how it runs is the
adapter's, chosen by the card's "adapter" id (models.card):

  adapter = for_card(card)
  rec  = adapter.ingest(base, graph, raw_bc, bindings, log)   model input of a new first iteration (files in base/,
                                                              JSON-able notes kept in base/t1.json)
  room = adapter.open_room(base, graph, rec)                  the work directory held in memory by the worker
  x, changed = room.state(cfdrun)                             the input of a request (changed: patch names)
  res  = room.predict(model, x)                               {'pred' [cells + boundary faces, 5] u v w p_rgh T,
                                                               'timing' {'forward_s', ...}, 'n_cells', 'n_nodes'}
  model = adapter.load_model(run_dir)                         a checkpoint on the worker's device (attributes
                                                              device, load_seconds; matmul optional)
  adapter.warm_up(model, scratch)                             one forward without any case (first CUDA call)

The prediction contract (every cell, then every boundary face in the graph's order, physical u v w p_rgh T) is
what foam_writer and the post-processing injection read, so a new format only has to produce it.
"""
from __future__ import annotations

from typing import Dict

REGISTRY: Dict[str, type] = {}


def register(cls):
    REGISTRY[cls.id] = cls
    return cls


def for_card(card: Dict):
    """The adapter of a resolved model card (models.card)."""
    from . import scano_dcts_v1  # noqa: F401  (registers itself)
    cls = REGISTRY.get(card.get('adapter'))
    if cls is None:
        raise ValueError(f"model {card.get('id')!r}: adapter {card.get('adapter')!r} is not one of {sorted(REGISTRY)}")
    return cls(card)


class Adapter:
    """Base: holds the card; subclasses implement ingest, open_room, load_model, warm_up."""
    id = ''

    def __init__(self, card: Dict):
        self.card = card
        self.recipe = card['first_iteration']
