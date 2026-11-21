"""Bounded binary transfer of an owned cfdrun input to a supervised child."""
import os
import re
import select
import threading
from .cfdrun_snapshot import CfdrunSnapshot, MAX_CFDRUN_SNAPSHOT_BYTES

_CHUNK = 256 * 1024

def read_cfdrun_stdin(stream, *, case_dir, logical_path, expected_sha256,
                      expected_stl_batch_sha256=None, mesh_budget=None):
    """Read only the explicit stdin mode; missing/mismatched identity refuses."""
    if (not isinstance(expected_sha256, str)
            or re.fullmatch(r'[0-9a-f]{64}', expected_sha256) is None):
        raise ValueError('stdin cfdrun requires an expected lowercase SHA-256')
    if expected_stl_batch_sha256 is not None and (
            not isinstance(expected_stl_batch_sha256, str)
            or re.fullmatch(r'[0-9a-f]{64}', expected_stl_batch_sha256) is None):
        raise ValueError('stdin STL batch requires an expected lowercase SHA-256')
    payload = bytearray()
    fd = None
    if mesh_budget is not None:
        try:
            fd = stream.fileno()
        except (AttributeError, OSError):
            pass
    while True:
        size = min(_CHUNK, MAX_CFDRUN_SNAPSHOT_BYTES + 1 - len(payload))
        if mesh_budget is not None:
            mesh_budget.check("cfdrun stdin")
        if fd is not None:
            ready, _, _ = select.select([fd], [], [], mesh_budget.remaining())
            if not ready:
                mesh_budget.check("cfdrun stdin")
            chunk = os.read(fd, size)
        else:
            chunk = stream.read(size)
        if mesh_budget is not None:
            mesh_budget.check("cfdrun stdin")
        if not isinstance(chunk, bytes):
            raise TypeError('cfdrun stdin must be a binary stream')
        if not chunk:
            break
        payload.extend(chunk)
        if len(payload) > MAX_CFDRUN_SNAPSHOT_BYTES:
            raise ValueError('cfdrun stdin byte capacity exceeded')
    return CfdrunSnapshot(payload, case_dir, logical_path, expected_sha256=expected_sha256,
                          stl_batch_sha256=expected_stl_batch_sha256)

class CfdrunInputFeeder:
    """One writer; the job owner drains stdout and joins before terminal state.

    Closing/terminating the supervised reader unblocks a full pipe. Writer
    errors are kept for the owner, rather than silently authorizing a job.
    """
    def __init__(self, stream, snapshot):
        if not isinstance(snapshot, CfdrunSnapshot):
            raise TypeError('cfdrun feeder requires a CfdrunSnapshot')
        self._stream = stream
        self._payload = snapshot.read_bytes()
        self.error = None
        self._thread = threading.Thread(target=self._run, name='cfdrun-input', daemon=True)

    def _run(self):
        try:
            view = memoryview(self._payload)
            offset = 0
            while offset < len(view):
                written = self._stream.write(view[offset:offset + _CHUNK])
                if type(written) is not int or written <= 0 or written > min(_CHUNK, len(view)-offset):
                    raise OSError('cfdrun stdin write made invalid progress')
                offset += written
            self._stream.flush()
        except Exception as exc:
            self.error = exc
        finally:
            try:
                self._stream.close()
            except Exception as exc:
                if self.error is None:
                    self.error = exc
            self._payload = None

    def start(self):
        self._thread.start()

    def join(self, timeout=None):
        self._thread.join(timeout)

    def is_alive(self):
        return self._thread.is_alive()
