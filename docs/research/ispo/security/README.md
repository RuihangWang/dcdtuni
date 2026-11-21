# W-SEC integration record — 2026-10-08 SGT

Status: **245/245 server tests passed, zero skips**. This comprises the existing 186 regression tests and 59 new security cases. Root independently repeated the complete suite. Source/tests are frozen for review; product services remain stopped.

Receipt: `receipt-2026-10-08_033935_SGT.json`. The receipt pins every source/test hash, source locations, final log and JUnit hashes, test boundaries and earlier failed attempts. Initial identities remain in `baseline-source.json`.

## Changes and verified scope

- Protected HTTP requests and authentication-check/login fail closed if authentication is unconfigured. Default loopback requests require an ordinary session. Explicit `--trust-loopback` grants HTTP administrator access only to actual loopback peers, warns at startup, and ignores forwarded headers for authorization.
- Default bind is `127.0.0.1`. Actual `__main__` default wiring is executed with real synthetic credential initialization and listener/thread sentinels. Actual missing-credential CLI invocations exit before creating a store or binding. No authenticated product service is launched for this gate.
- Administrator bootstrap contains no embedded password. It requires the complete environment pair or an owned regular exact-0600 two-line file. Partial environment pairs, permissive/incorrect modes, foreign owners, symlinks, FIFOs and malformed files are rejected.
- Terminal is disabled by default. When explicitly enabled, the actual handler always validates a nonexpired administrator token; HTTP loopback trust does not apply. Real ephemeral WebSocket handshakes verify missing/invalid/viewer rejection and administrator acceptance up to a PTY sentinel. No PTY or shell is created.
- Internal browser receives a random session issued at authenticated process bootstrap. It is an ordinary persisted session scoped by the HTTP listener cookie name. A hash fingerprint supplies traceability. Cookie installation failure stops the worker with a sanitized error; bootstrap credentials are removed from the child environment. No real browser is launched.
- Malformed/future/naive or expired session timestamps fail closed. Auth logger identity follows the isolated store, so independent stores retain their own trace.
- Existing worker-cookie regression fixture supplies its pre-issued session; HTTP-port cookie assertions are unchanged.

The Python/server scope closes S1–S5 above. **S6 (server-free air pipeline and complete M1 slice) is not closed by this receipt.** Wrapper/Vite/OV behavior is outside these source hashes; root owns any wrapper default-bind adjustment. These tests do not establish CFD, training or paper benefits.

## Credentials and launch interface

Set both `DCDTUNI_ADMIN_USER` and `DCDTUNI_ADMIN_PASSWORD` through the operator's existing secret mechanism, or pass `--admin-credentials <private-file>`. Private file contains exactly two nonempty lines: username, then password, owned by current user with mode 0600. No example includes a credential value. Existing stores require these explicit credentials to authenticate an administrator before startup.

Default backend invocation accepts `--app`, `--port`, `--ws-port`, `--host`, `--case-dir`, `--auth-dir`, `--admin-credentials`, `--trust-loopback`, and `--enable-terminal`. `--host 0.0.0.0` is explicit exposure and still requires credentials. `--enable-terminal` is an independent opt-in. Legacy `--password` argument is removed. Random worker session stays internal to process and is installed as an HTTP cookie, rather than exposing it in browser-child environment or stdout.

Task does **not** invoke this launch interface: the user's platform-service gate remains with root.

## Verification and reproduction

Final files: `_tmp/security-final3/pytest.log` and `_tmp/security-final3/junit.xml`. Python is the `dcdtuni` conda interpreter. Node22 must be in `PATH` for eight existing server tests. All subprocess/pytest stores and caches stay in repository `_tmp/`; each rerun uses a fresh directory.

Exact final pytest invocation runs after `agent._runtime.configure_runtime_caches()`, with `PYTHONPATH=src/server:src`, `CUDA_VISIBLE_DEVICES=`, `OMP_NUM_THREADS=1`, `MKL_NUM_THREADS=1`, `PYTHONDONTWRITEBYTECODE=1` and `PATH=/home/litianyu/.local/bin:/usr/local/bin:/usr/bin:/bin`:

~~~python
from agent._runtime import configure_runtime_caches
configure_runtime_caches()
import pytest
raise SystemExit(pytest.main([
    'src/server/tests', '--import-mode=importlib',
    '--basetemp=<fresh-repository-_tmp-run>/pytest',
    '--junitxml=<fresh-repository-_tmp-run>/junit.xml',
    '--assert=plain', '--tb=short', '-q',
]))
~~~

`--import-mode=importlib` avoids confusing the `server` package with `server.py`. `--assert=plain` avoids printing generated secret inputs in assertion diagnostics. Earlier instrumentation failures and implementation corrections stay in the receipt.

## Boundaries

Existing `src/server/auth/` credential/user/session files were never read, listed or changed. Source reads masked old string literals. Security tests use generated synthetic credentials and isolated stores under `_tmp/`. Temporary HTTP and WebSocket listeners use port zero on loopback, terminate inside the test, and are distinct from product services. No GPU, real browser, terminal shell, 40901 changes, stage, commit or push occurred.
