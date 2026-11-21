"""Keep the CPU tests to a few threads: this is a shared 48-core host.

torch and BLAS start one thread per core. Next to other work that saturates
the machine, and the host's ``user-resource-guard`` (root daemon,
``/etc/user-resource-guard.conf``: whole machine at 100 % CPU and one user
owning >= 98 % of it) sends SIGTERM to that user's largest process -- the
pytest run, silently, between two tests (2026-10-22, mid
``test_optim_optimizers``). The tests are small; two threads are also faster
than forty-eight. An ``OMP_NUM_THREADS`` you set yourself still wins.
"""
import os

os.environ.setdefault("OMP_NUM_THREADS", "2")
os.environ.setdefault("MKL_NUM_THREADS", os.environ["OMP_NUM_THREADS"])
