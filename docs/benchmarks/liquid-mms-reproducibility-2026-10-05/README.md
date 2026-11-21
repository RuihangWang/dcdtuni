# MMS fixture reproducibility repair (2026-10-05)

The canonical v3 manifest is now tracked at `docs/benchmarks/fixtures/mms-product-v3.liquidrun`.
The generator defaults there and its `--check` mode reproduces both project and manifest byte-for-byte.
The compiled metadata now includes MMS_TARGETS. Three clean-worktree contract tests pass;
`receipt.json` re-runs the independent small-loop closed form and time-refinement studies.

The old sampled-curvature quantity is a diagnostic, not a rigorous error upper bound, and has
been removed from acceptance gates. Historical receipt field names stay readable.
See the updated derivation under `docs/developer/liquid_dae_mms_product_derivation.md`.
