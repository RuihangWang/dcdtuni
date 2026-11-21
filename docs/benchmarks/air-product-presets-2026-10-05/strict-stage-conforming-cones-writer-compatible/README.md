# Writer-compatible conforming-cone diagnostic

The actual React export and eight-rank mesh API job rejected the output after 86.764 s (native 66.8893 s). This is not a mesh acceptance or performance PASS.

The stock final `checkMesh -allTopology -allGeometry -parallel` now reports closed topology, matching coupled locations, passing face tetrahedra and passing cell concavity. The previous 38 open tetrahedra and cyclic point-zero transform failure are absent. The independent coordinate-pair audit is recorded separately.

The remaining four failed checks are two skew faces (maximum 4.90172), 3353 small determinants (minimum 4.70435e-11), 984 low interpolation weights (minimum 0.00229916), and 68 low volume ratios (minimum 0.00230446). The actual 5892014-cell mesh remains unauthorized for a solver.

`tested-experimental-source.patch` and `tested-source-identity.json` bind the opt-in source and actual binary; all cone/cleanup experiments remain disabled by default. The exact processor ordering fix also affects the ordinary writer and requires its own default-path regression.

Large original `region0`/`region1` label sets are preserved with deterministic lossless `.gz` compression. `large-raw-set-compression.json` binds both compressed and original bytes; the receipt retains the original SHA256. The other quality sets remain uncompressed.
