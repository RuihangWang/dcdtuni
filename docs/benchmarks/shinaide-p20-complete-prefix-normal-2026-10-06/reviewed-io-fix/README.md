# PRIVATE OBS exclusiveWrite I/O cleanup

A real controlled RLIMIT_FSIZE=1024 reproduces ROOT's observation: requesting 8192 bytes returns false/EFBIG while the original helper leaves a 1024-byte partial file. A subsequent same-name exclusive refusal write then fails. Baseline actual-OF-header harness: 7 PASS / 2 FAIL, exit 1.

The sole PRIVATE helper patch pins its successfully wx-created inode through a duplicated descriptor. On write/flush/close failure it removes only a regular path matching that device and inode, then closes the held descriptor. A failed wx open never enters cleanup. Success leaves the file intact. If the cleanup check observes the path replaced by a symlink or a different regular file, it is preserved. This check and unlink are separate POSIX operations; no general claim against an adversarial rename between them is made. The authorized observation directory must have no competing path mutator.

The fixed actual-OF-header harness passes 9/9: real RLIMIT failure removes its partial, same-name short refusal succeeds, existing regular content and existing/dangling symlinks remain, success remains, and two controlled path-replacement cases retain the replacement. Those replacement cases use a link-time fwrite wrapper forwarding the real write and then replacing the owned path; the primary size-limit case has no injected write failure.

Original 203 frozen payloads and all 33 normal production controls remain byte exact. Only the PRIVATE new header differs, inside exclusiveWrite plus two POSIX includes. No other capture/policy/authority/oracle/quality/MPI body changed. The initial harness include setup compile failure is preserved separately; it is not the behavioral RED.

This is best-effort cleanup under normal POSIX filesystem semantics, not a promise that unlink always succeeds. Unknown identity or failed cleanup must remain a failed observation; written=false is authoritative. No normal deployment, library/APP build, business run, browser/API/Kit/service or Git action occurred. Strict P19 remains Failed2 D831/W82.

Apply only the new after/dcCartCompleteCavityReplayObservation.H after ROOT review; retain the original 203 package as historical evidence.
