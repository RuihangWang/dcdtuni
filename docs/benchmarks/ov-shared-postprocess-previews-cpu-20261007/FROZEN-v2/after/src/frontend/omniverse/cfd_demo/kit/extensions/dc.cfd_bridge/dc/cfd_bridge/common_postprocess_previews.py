"""Display-only shared form handles, authorized by the last signed numeric commit.

No USD/data imports and no numerical slice/trace entry points belong here.
"""
import copy
import json
import math
import re
import secrets

SCHEMA = 'dcdtuni.ov-postprocess-previews/v1'
ACK_SCHEMA = 'dcdtuni.ov-postprocess-previews-ack/v1'
EVENT_SCHEMA = 'dcdtuni.ov-postprocess-preview-event/v1'
BASE_KEYS = ('source', 'packetSha256', 'timeSeconds', 'sceneRevision', 'displayGeneration')
MAX_INTEGER = 9007199254740991


def _keys(value, keys):
    if type(value) is not dict or set(value) != set(keys):
        raise ValueError('Unexpected shared preview fields')


def _text(value, maximum):
    if type(value) is not str or not 1 <= len(value) <= maximum:
        raise ValueError('Invalid shared preview text')


def _hex(value, count):
    if type(value) is not str or re.fullmatch('[0-9a-f]{%d}' % count, value) is None:
        raise ValueError('Invalid shared preview identity')


def _number(value, lower=-1e18, upper=1e18):
    if type(value) not in (int, float) or not math.isfinite(value) or not lower <= value <= upper:
        raise ValueError('Invalid shared preview coordinate')


def _integer(value, lower=0):
    if type(value) is not int or not lower <= value <= MAX_INTEGER:
        raise ValueError('Invalid shared preview generation')


def vector(value):
    if type(value) not in (list, tuple) or len(value) != 3:
        raise ValueError('Invalid shared preview vector')
    for component in value:
        _number(component)
    return tuple(value)


def unit(value):
    value = vector(value)
    length = math.hypot(*value)
    if not length > 1e-12:
        raise ValueError('Degenerate shared preview normal')
    return tuple(v / length for v in value)


def _set_id(value):
    if value != 'draft' or type(value) is not str:
        _integer(value)


def validate_binding(binding):
    _keys(binding, (*BASE_KEYS, 'scopeKey', 'ownerKey'))
    source = binding['source']
    _keys(source, ('projectUid', 'projectContentSha256', 'cfdrunSha256',
                   'meshReceiptSha256', 'meshQuality', 'run'))
    _text(source['projectUid'], 1024)
    for name in ('projectContentSha256', 'cfdrunSha256', 'meshReceiptSha256'):
        _hex(source[name], 64)
    if source['meshQuality'] not in ('accepted', 'diagnostic'):
        raise ValueError('Invalid shared preview mesh quality')
    _keys(source['run'], ('uid', 'receiptSha256'))
    _text(source['run']['uid'], 1024)
    _hex(source['run']['receiptSha256'], 64)
    _hex(binding['packetSha256'], 64)
    _number(binding['timeSeconds'], 0)
    _integer(binding['sceneRevision'])
    _integer(binding['displayGeneration'])
    _text(binding['scopeKey'], 4096)
    _hex(binding['ownerKey'], 32)
    if len(json.dumps(binding, allow_nan=False, ensure_ascii=False).encode('utf-8')) > 16384:
        raise ValueError('Shared preview payload too large')
    return binding


def validate_packet(packet):
    _keys(packet, ('schema', 'binding', 'handles'))
    if packet['schema'] != SCHEMA:
        raise ValueError('Invalid shared preview schema')
    validate_binding(packet['binding'])
    handles = packet['handles']
    if type(handles) is not list or len(handles) > 2:
        raise ValueError('Invalid shared preview handles')
    seen = set()
    for handle in handles:
        if type(handle) is not dict:
            raise ValueError('Invalid shared preview handle')
        kind = handle.get('kind')
        if kind not in ('ring', 'seed') or kind in seen:
            raise ValueError('Invalid shared preview kind')
        seen.add(kind)
        _keys(handle, ('kind', 'setId', 'origin', 'normal', 'radius') if kind == 'ring'
              else ('kind', 'setId', 'centre', 'radius'))
        _set_id(handle['setId'])
        vector(handle['origin'] if kind == 'ring' else handle['centre'])
        if kind == 'ring':
            unit(handle['normal'])
        _number(handle['radius'], 0)
        if not handle['radius'] > 0:
            raise ValueError('Degenerate shared preview radius')
    if len(json.dumps(packet, allow_nan=False, ensure_ascii=False).encode('utf-8')) > 16384:
        raise ValueError('Shared preview payload too large')
    return packet


def identity(value):
    return json.dumps(value, allow_nan=False, sort_keys=True, separators=(',', ':'))


def ray_plane(origin, direction, point, normal):
    origin, direction, point, normal = vector(origin), vector(direction), vector(point), unit(normal)
    denominator = sum(d * n for d, n in zip(direction, normal))
    if abs(denominator) <= 1e-12:
        raise ValueError('Shared preview ray parallel to drag plane')
    distance = sum((p - o) * n for p, o, n in zip(point, origin, normal)) / denominator
    if not math.isfinite(distance) or distance < 0:
        raise ValueError('Shared preview drag plane behind pointer')
    return vector(tuple(o + distance * d for o, d in zip(origin, direction)))


def move_handle(handle, start_hit, hit):
    start = vector(handle['origin'] if handle['kind'] == 'ring' else handle['centre'])
    delta = tuple(b - a for a, b in zip(vector(start_hit), vector(hit)))
    if handle['kind'] == 'ring':
        normal = unit(handle['normal'])
        distance = sum(d * n for d, n in zip(delta, normal))
        delta = tuple(distance * n for n in normal)
    return vector(tuple(s + d for s, d in zip(start, delta)))


class PreviewController:
    """Own a native lease only after the numerical carrier actually committed.

    retire-before-set tombstones survive replays of the same numeric commit;
    adapter owner keys stop an old renderer's retirement clearing its successor.
    """
    def __init__(self, read_context, publish):
        self._read_context = read_context
        self._publish_event = publish
        self._committed = None
        self._active = None
        self._retired = {}
        self._closed = False

    def _drop(self):
        active, self._active = self._active, None
        if active and active['producer'] is not None:
            active['producer'].close()

    def invalidate(self):
        try:
            self._drop()
        finally:
            self._committed = None

    def close(self):
        try:
            self.invalidate()
        finally:
            self._closed = True

    def bind_committed(self, granted, stage_generation):
        if self._closed:
            raise ValueError('Shared preview controller closed')
        self._drop()
        if granted.get('kind') != 'rendererResult':
            self._committed = None
            return
        base = copy.deepcopy({key: granted[key] for key in BASE_KEYS})
        # Verify the complete base using a bounded temporary presentation owner.
        validate_binding(dict(base, scopeKey='committed', ownerKey='0' * 32))
        _integer(stage_generation)
        base_token = identity(base)
        self._retired = {token: value for token, value in self._retired.items() if value == base_token}
        self._committed = dict(base=base, stage_generation=stage_generation)
        self.require_current()

    def require_current(self):
        committed = self._committed
        if self._closed or committed is None:
            raise ValueError('No committed signed numeric result for shared previews')
        try:
            scene, epoch = self._read_context()
        except (ValueError, AttributeError, ReferenceError, TypeError) as exc:
            self.invalidate()
            raise ValueError('Shared preview scene unavailable') from exc
        base = committed['base']
        if (epoch != committed['stage_generation'] or scene.get('projectUid') != base['source']['projectUid']
                or scene.get('projectContentSha256') != base['source']['projectContentSha256']
                or scene.get('revision') != base['sceneRevision']):
            self.invalidate()
            raise ValueError('Shared preview scene changed')
        return copy.deepcopy(base)

    def install(self, raw, factory):
        packet = copy.deepcopy(validate_packet(raw))
        base = self.require_current()
        binding = packet['binding']
        if identity({k: binding[k] for k in BASE_KEYS}) != identity(base):
            raise ValueError('Shared preview differs from committed source/time/generation')
        token = identity(binding)
        if token in self._retired:
            raise ValueError('Shared preview owner retired')
        if len(self._retired) >= 256:
            raise ValueError('Shared preview owner retirement capacity reached')
        self._drop()
        active = dict(packet=packet, lease=secrets.token_hex(16), sequence=0, producer=None)
        self._active = active
        try:
            if packet['handles']:
                active['producer'] = factory(packet['handles'],
                    lambda kind, set_id, position: self._emit(active, kind, set_id, position), self.require_current)
                if not active['producer'].ready():
                    raise ValueError('Native shared preview ownership not ready')
            self.require_current()
            if self._active is not active or token in self._retired:
                raise ValueError('Shared preview retired during installation')
            return dict(schema=ACK_SCHEMA, packet=copy.deepcopy(packet), leaseKey=active['lease'], ready=True)
        except Exception:
            if self._active is active:
                self._drop()
            raise

    def retire(self, raw):
        binding = copy.deepcopy(validate_binding(raw))
        token = identity(binding)
        # A retirement may arrive before installation. Keep exact same-source
        # tombstones even before a numeric ACK, without closing unrelated owners.
        base_token = identity({k: binding[k] for k in BASE_KEYS})
        if self._committed is not None and base_token != identity(self._committed['base']):
            return True
        if token not in self._retired and len(self._retired) >= 256:
            raise ValueError('Shared preview owner retirement capacity reached')
        self._retired[token] = base_token
        if self._active and identity(self._active['packet']['binding']) == token:
            self._drop()
        return True

    def _emit(self, active, kind, set_id, position):
        if self._active is not active or active['producer'] is None:
            return False
        try:
            self.require_current()
            if not active['producer'].ready():
                self._drop()
                return False
            if not any(h['kind'] == kind and h['setId'] == set_id for h in active['packet']['handles']):
                return False
            position = vector(position)
            sequence = active['sequence'] + 1
            _integer(sequence, 1)
            event = dict(schema=EVENT_SCHEMA, binding=copy.deepcopy(active['packet']['binding']),
                leaseKey=active['lease'], sequence=sequence, kind=kind, setId=set_id, position=list(position))
            self._publish_event(event)
            active['sequence'] = sequence
            return True
        except (ValueError, TypeError, ReferenceError, RuntimeError):
            return False
