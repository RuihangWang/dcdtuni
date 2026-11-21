"""The post-processing prefetch cap rises with the host's available memory and never falls below the fixed one."""
import io

import server


def _meminfo(monkeypatch, kib):
    real = open
    monkeypatch.setattr("builtins.open", lambda path, *a, **k: io.StringIO(f"MemTotal: 1 kB\nMemAvailable: {kib} kB\n")
                        if path == "/proc/meminfo" else real(path, *a, **k))


def test_cap_follows_available_memory(monkeypatch):
    _meminfo(monkeypatch, 1024 * 1024)                       # 1 GiB available: the fixed cap
    assert server._pp_prefetch_max_cells() == server.PP_PREFETCH_MAX_CELLS
    _meminfo(monkeypatch, 227 * 1024 * 1024)                 # 227 GiB: a 10 % share at 650 B a cell
    cap = server._pp_prefetch_max_cells()
    assert cap == int(227 * 2**30 * server.PP_PREFETCH_MEMORY_SHARE / server.PP_PREFETCH_BYTES_PER_CELL)
    assert cap > 10_375_055                                  # Alibaba-hongchou is prefetched


def test_unreadable_meminfo_keeps_the_fixed_cap(monkeypatch):
    real = open

    def broken(path, *a, **k):
        if path == "/proc/meminfo":
            raise OSError("no procfs")
        return real(path, *a, **k)
    monkeypatch.setattr("builtins.open", broken)
    assert server._pp_prefetch_max_cells() == server.PP_PREFETCH_MAX_CELLS
