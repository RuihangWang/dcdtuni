"""Who owns the seed-sphere centre: the UI's numbers or a viewport drag?

The WebRTC command channel is one-way, so the frontend never learns that the user
dragged the seed ball — it keeps sending its own centre. That stale value used to
overwrite the drag, which is why the ball "snapped back to its default" on every Apply.
``handlers._resolve_seed_center`` arbitrates instead; these tests pin that rule down.

``handlers`` imports ``omni.usd`` at module scope, which only exists inside Kit, so it
is stubbed here — none of the arbitration logic touches it.
"""
import os
import sys
import types

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.abspath(os.path.join(
    HERE, "..", "kit", "extensions", "dc.cfd_bridge")))

# Stub the Kit-only module before importing handlers.
sys.modules.setdefault("omni", types.ModuleType("omni"))
sys.modules.setdefault("omni.usd", types.ModuleType("omni.usd"))

from dc.cfd_bridge import handlers  # noqa: E402

UI_DEFAULT = [15.5, 2.5, 9.6]
DRAGGED_TO = [8.0, 2.5, 4.0]


class FakeScene:
    """Minimal stand-in for CfdScene carrying only the arbitration state.
    ``live`` is where the prim actually sits (what a drag would have changed)."""

    def __init__(self, commanded=None, live=None):
        self._seed_ui_center = None
        self._seed_commanded_center = commanded
        self.seed_center = list(commanded) if commanded else [0.0, 0.0, 0.0]
        self._live = live
        self.adopted = False

    def adopt_dragged_seed(self, stage):
        from dc.cfd_bridge import geom
        if self._live is None:
            return None
        if not geom.moved(self._seed_commanded_center, self._live):
            return None
        self.seed_center = list(self._live)
        self.adopted = True
        return self._live


def test_first_command_takes_the_ui_centre():
    """Nothing recorded yet -> the UI seeds the state."""
    s = FakeScene()
    handlers._resolve_seed_center(s, None, UI_DEFAULT)
    assert s.seed_center == UI_DEFAULT
    assert s._seed_ui_center == UI_DEFAULT


def test_viewport_drag_wins_when_ui_did_not_change():
    """THE BUG: Apply re-sent the unchanged UI centre and clobbered the drag.
    Now an unchanged UI value yields to the prim's live position."""
    s = FakeScene(commanded=UI_DEFAULT, live=DRAGGED_TO)
    s._seed_ui_center = list(UI_DEFAULT)      # UI already sent this once
    handlers._resolve_seed_center(s, None, UI_DEFAULT)
    assert s.adopted is True
    assert s.seed_center == DRAGGED_TO, "a dragged sphere must not snap back"


def test_typed_numbers_win_over_a_stale_drag():
    """The user typing a NEW centre is an explicit override; the drag yields."""
    typed = [1.0, 2.0, 3.0]
    s = FakeScene(commanded=UI_DEFAULT, live=DRAGGED_TO)
    s._seed_ui_center = list(UI_DEFAULT)
    handlers._resolve_seed_center(s, None, typed)
    assert s.adopted is False
    assert s.seed_center == typed


def test_no_drag_and_no_ui_change_leaves_centre_alone():
    """Prim still where we put it, UI unchanged -> nothing moves."""
    s = FakeScene(commanded=UI_DEFAULT, live=list(UI_DEFAULT))
    s._seed_ui_center = list(UI_DEFAULT)
    handlers._resolve_seed_center(s, None, UI_DEFAULT)
    assert s.adopted is False
    assert s.seed_center == UI_DEFAULT


def test_repeated_applies_keep_the_dragged_position():
    """Clicking Apply twice must not resurrect the default on the second click."""
    s = FakeScene(commanded=UI_DEFAULT, live=DRAGGED_TO)
    s._seed_ui_center = list(UI_DEFAULT)
    handlers._resolve_seed_center(s, None, UI_DEFAULT)
    # The backend then re-places the prim at the adopted centre.
    s._seed_commanded_center = list(DRAGGED_TO)
    handlers._resolve_seed_center(s, None, UI_DEFAULT)
    assert s.seed_center == DRAGGED_TO
