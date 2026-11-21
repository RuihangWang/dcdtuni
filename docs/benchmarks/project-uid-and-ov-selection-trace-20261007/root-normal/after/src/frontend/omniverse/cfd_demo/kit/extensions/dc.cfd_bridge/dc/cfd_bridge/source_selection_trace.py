"""Default-off, bounded local observations; never selection or wire authority."""
import json
import math
import time

SETTING = '/exts/dc.cfd_bridge/sourceSelectionTrace/enabled'
PREFIX = '[dc.cfd_bridge] source-selection-trace '
MAX_RECORDS = 128
MIN_INTERVAL_S = 0.25
MAX_LINE_BYTES = 4096
MAX_ITEMS = 8
MAX_TEXT_BYTES = 192
PHASES = frozenset(('suppressed-or-unbound', 'no-stage', 'path-cap', 'foreign-path',
    'missing-or-hidden-prim', 'binding-refused', 'uid-cap', 'dedupe', 'payload-cap',
    'bus-push-returned', 'state-query'))


def _text(value):
    if not isinstance(value, str):
        return None
    # At most 192 codepoints are encoded, so even an enormous native string is
    # bounded before conversion; UTF-8 is then truncated at a complete codepoint.
    return value[:MAX_TEXT_BYTES].encode('utf-8', errors='replace')[:MAX_TEXT_BYTES].decode('utf-8', errors='ignore')


def public_modifier_flags():
    # Installed carb/input.pyi and native selection manipulator use this public
    # read-only interface. This is callback/query time, never a click receipt.
    import carb.input
    mask = (carb.input.KEYBOARD_MODIFIER_FLAG_SHIFT | carb.input.KEYBOARD_MODIFIER_FLAG_CONTROL
            | carb.input.KEYBOARD_MODIFIER_FLAG_ALT | carb.input.KEYBOARD_MODIFIER_FLAG_SUPER)
    return carb.input.acquire_input_interface().get_global_modifier_flags(mask)


class SourceSelectionTrace:
    def __init__(self, enabled, *, sink=print, clock=time.monotonic, modifiers=public_modifier_flags):
        self.enabled = enabled is True
        self._sink, self._clock, self._modifiers = sink, clock, modifiers
        self._emitted = 0
        self._last = None

    def observe(self, phase, *, paths=None, uids=None, cached_identity=None,
                scene_revision=None, project_hash=None, refused_index=None):
        if not self.enabled or phase not in PHASES or self._emitted >= MAX_RECORDS:
            return
        try:
            now = self._clock()
            if isinstance(now, bool) or not isinstance(now, (int, float)) or not math.isfinite(now):
                return
            if self._last is not None and now - self._last < MIN_INTERVAL_S:
                return
            # Reserve before the optional API call/sink. Exceptions cannot make
            # repeated events or a failing logger evade the frequency/total cap.
            self._last = now
            self._emitted += 1
            paths = paths if isinstance(paths, (tuple, list)) else ()
            uids = uids if isinstance(uids, (tuple, list, dict)) else ()
            # dict iteration is source first-seen order. Never sort/re-evaluate
            # the source selection or traverse additional USD objects here.
            from itertools import islice
            uid_values = list(islice(iter(uids), MAX_ITEMS))
            uid_sample = [_text(uid) for uid in uid_values]
            cached = ()
            if (isinstance(cached_identity, tuple) and len(cached_identity) == 2
                    and isinstance(cached_identity[1], tuple)):
                cached = cached_identity[1]
            flags = None
            try:
                raw_flags = self._modifiers()
                if type(raw_flags) is int and 0 <= raw_flags <= 0xffffffff:
                    flags = raw_flags & 15
            except Exception:
                pass
            record = {
                'schema': 'dcdtuni.source-selection-trace/v1', 'sequence': self._emitted,
                'phase': phase, 'samplePhase': 'query' if phase == 'state-query' else 'selection-event',
                'monotonicSampleS': now, 'gestureModifierProof': False,
                'sceneRevision': scene_revision if type(scene_revision) is int and scene_revision >= 0 else None,
                'projectHash': project_hash if isinstance(project_hash, str) and len(project_hash) == 64
                    and all(c in '0123456789abcdef' for c in project_hash) else None,
                'pathCount': len(paths), 'paths': [_text(path) for path in paths[:MAX_ITEMS]],
                'uidCount': len(uids), 'uids': uid_sample,
                'cachedSceneRevision': cached_identity[0] if isinstance(cached_identity, tuple) and len(cached_identity) == 2
                    and type(cached_identity[0]) is int and cached_identity[0] >= 0 else None,
                'refusedPath': _text(paths[refused_index]) if type(refused_index) is int
                    and 0 <= refused_index < len(paths) else None,
                'cachedUidCount': len(cached), 'cachedUids': [_text(uid) for uid in cached[:MAX_ITEMS]],
                'refusedIndex': refused_index if type(refused_index) is int and 0 <= refused_index < len(paths) else None,
                'modifierFlags': flags, 'modifierSampleAvailable': flags is not None,
                'shiftAtSample': None if flags is None else bool(flags & 1),
                'controlAtSample': None if flags is None else bool(flags & 2),
                'altAtSample': None if flags is None else bool(flags & 4),
                'superAtSample': None if flags is None else bool(flags & 8),
                'truncated': len(paths) > MAX_ITEMS or len(uids) > MAX_ITEMS or len(cached) > MAX_ITEMS
                    or any(isinstance(p, str) and (len(p) > MAX_TEXT_BYTES or len(p[:MAX_TEXT_BYTES].encode('utf-8', errors='replace')) > MAX_TEXT_BYTES)
                        for p in (*paths[:MAX_ITEMS], *uid_values, *cached[:MAX_ITEMS])),
                'transportDeliveryProof': False,
            }
            line = PREFIX + json.dumps(record, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
            if len(line.encode('utf-8')) > MAX_LINE_BYTES:
                # Large/multibyte names are observations only; omit samples rather
                # than emit an oversized line or reinterpret the source decision.
                record['paths'] = record['uids'] = record['cachedUids'] = []
                record['truncated'] = True
                line = PREFIX + json.dumps(record, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
            if len(line.encode('utf-8')) <= MAX_LINE_BYTES:
                self._sink(line)
        except Exception:
            # No exception details/environment leakage and no product mutation.
            return
