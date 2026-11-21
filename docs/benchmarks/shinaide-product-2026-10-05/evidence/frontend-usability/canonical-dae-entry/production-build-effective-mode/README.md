# Pure default-mode display follow-up

A real browser showed that the missing-experiment input still exposed duration
and an enabled load-step checkbox while its top bar selected steady. The
compiler rejects configured events for steady; it never silently discards them.
The display condition now reads the marked default mode directly from the
top bar, without graph compilation or project writes. Legacy v2 behavior is
unchanged. Three new UI regressions first failed twice on the preceding source;
all 71 related tests and the independent typecheck pass after the one-line fix.

The actual TypeScript/Vite production build exited zero in 47.719 s, using
CPU 32–35, no visible CUDA and repository-owned scratch/cache paths. Its
source hashes were unchanged. The entry is index-B5f2ecwI.js.
This receipt does not replace the ongoing main browser run on 5f9bd080/Bqn.
The compiler, backend, migration and canonical inputs did not change.
See the [semantic failure and repair](../../../liquid-default-effective-mode-ui/README.md).
