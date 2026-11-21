"""handle_set_slice ordering: panel parameters first, THEN the origin goes
through the drag arbitration. Re-authoring the disc before arbitration would
let a stale panel origin overwrite a viewport drag."""
import os
import sys
import types

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "kit", "extensions", "dc.cfd_bridge")))
sys.modules.setdefault("omni", types.ModuleType("omni"))
sys.modules.setdefault("omni.usd", types.ModuleType("omni.usd"))

from dc.cfd_bridge import handlers  # noqa: E402


class FakeScene:
    def __init__(self):
        self.calls = []
        self.slice_origin = [0.0, 0.0, 0.0]

    def resolve_slice_origin(self, stage, origin):
        # The arbitration must already see the new plane parameters.
        self.calls.append(('resolve', list(origin), self.slice_normal, self.slice_radius))
        self.slice_origin = list(origin)


def test_set_slice_applies_parameters_before_origin_arbitration():
    s = FakeScene()
    handlers.handle_set_slice(s, None, visible=True, origin=[1.0, 2.0, 3.0],
                              normal=[0.0, 0.0, 1.0], radius=0.75, field='Umag',
                              domain=[0.0, 1.0])
    assert s.slice_visible is True
    assert s.slice_field == 'Umag'
    assert s.slice_domain == [0.0, 1.0]
    assert s.calls == [('resolve', [1.0, 2.0, 3.0], [0.0, 0.0, 1.0], 0.75)]


def test_set_slice_keeps_null_domain():
    s = FakeScene()
    handlers.handle_set_slice(s, None, visible=False, origin=[0, 0, 0],
                              normal=[0, 1, 0], radius=1.0, field='T', domain=None)
    assert s.slice_domain is None and s.slice_visible is False
