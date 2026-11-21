# Launcher cleanup ownership — 2026-10-07

A delayed old launcher cleanup could kill a replacement backend that had reused its port. This was observed while restarting only the isolated 8396/8397/8302 display service; the held main service was unaffected.

`start_server.sh` now releases TCP listeners only after checking the current user, launcher ancestry and process start identity. It sends TERM before an identity-checked KILL. OV supervisor cleanup remains on its existing private ownership protocol. An empty cleanup preserves the original exit status.

Actual controlled TCP tests: the current launcher child is stopped; an independently launched replacement on the same port remains connectable. Replaying the previous blind port cleanup killed that replacement (expected failure, exit -9). Final four controls pass, including original exit 0 and 7 with empty cleanup. The relevant existing launcher/preflight/ownership regression set passed 90 tests; final return-status refinement was followed by these four focused controls and bash syntax/diff checks.

The first broad run had 87 passes and 3 fixture failures because fake Kit startup lacked its Python/CONDA_PREFIX binding after owned configuration preparation moved earlier. Its fixture now explicitly identifies the active interpreter; no product oracle was relaxed. Those initial logs remain under `.codex-tmp/launcher-cleanup-ownership-root-20261007/`.

This proves the ownership guard and controlled listener behavior. The running isolated launcher predates this cleanup edit; it will adopt the guard at its next ordinary script startup. Actual restarts also wait for all previous owned roles to exit and listener release before launching the replacement.
