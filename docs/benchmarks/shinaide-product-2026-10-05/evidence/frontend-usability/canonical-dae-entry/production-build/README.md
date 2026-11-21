# Frozen production build — canonical Newton entry

The actual `npm run build` ran on the integration source snapshot based on
89f8b48b, CPU 32–35 with CUDA invisible and repository-owned temporary/cache
paths. TypeScript and Vite exited zero in 35.518 s. Every recorded source hash
was unchanged across the build. The entry asset is index-BqnTGa6S.js, SHA256
51b897edd1bca87840b22b80c18077d1f6e8554727de2cf5e47483c9dd36c7f6.

The final complete Vitest suite separately passed 3526 tests with one existing
skip; the final standalone typecheck also passed. See the source-bounded
[liquid receipt](../../../liquid-canonical-default-newton/receipt.json).

This is a source/build receipt. Main deployment, a real browser steady/transient
run and an active OV stream are separate gates. Existing cabinet rendering and
selection source identities are retained in the build receipt.
