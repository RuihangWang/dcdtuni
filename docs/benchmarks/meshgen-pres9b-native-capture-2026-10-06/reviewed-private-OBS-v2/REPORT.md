# OBS v2: retain existing paths on exclusive-open failure

PRIVATE only; no normal apply/build/native business. Original OBS-REVIEW-FROZEN is preserved, including its confirmed defect.

## Confirmed old defect and exact repair
Old Writer assigned path before open(O_EXCL). If EEXIST caused open to fail, its destructor unlinked the unowned existing path. v2 adds created=false and sets it only after successful creation. Destructor cleanup requires created&& !retain. Explicit I/O failure unlink occurs only after that successful-open branch. Exception cleanup also owns only a successfully created path. The original file name and exclusive creation policy remain.

Controlled C++ tests executed the actual frozen old/new helper: same-name ordinary file and same-name symlink are both RED on old source (rc1, existing path disappears) and GREEN on v2 (rc0, original inode/path/bytes and symlink target bytes preserved). ON,OFF and owned RLIMIT_FSIZE I/O failure are GREEN on v2. No external path or native job was used. Raw stdout/stderr, exact compiler commands and original prior bytes are retained. New complete Cartesian.cpp observer-after passed private syntax-only.

## Corrected scope
This captures eight scalar CONFIG SUBSET fields, not all CartesianConfig. Literal source context lines17145..18249 reads cfg.mergeVolFrac and cfg.dcMode; both are captured. Inside trySplit, cfg.dcMode affects the shown internal matching branch at18049. H/clipTol and all fixed derived split/plane tolerances are bound; dt.on is captured. Root's normal process/input receipts must separately bind environment/CLI configuration, source/ELF and input/STL. The sourceBaseSHA embedded in the schema is the before-source hash, not the actual compiled ELF identity.

Point-grid observation is built plus prefix size ONLY. Cache buckets, iteration order and regPt decision log are not captured. Generic controlled persistent-cache parity is not proof of actual captured-cache rebuild/replay parity. Such actual parity remains unproven.

## Unchanged observation contract
Default OFF MESHGEN_OBS_PRES9B, absolute existing Root-owned MESHGEN_OBS_PRES9B_DIR, pre/post S9b main entries only; 2 entries and64MiB each per process/rank;16384cells/65536points/262144faces/1048576refs. Ordered CellRec/FaceRec and binary64 point metadata/leafBox/owner/filled are preserved. No predicate, quality tolerance, split call, physical role, charged layout or producer return changed. Complete capture is not native mesh success. Ghost faces may remain unmaterialized; no authority/materialization scan is added. Root preflight owns the directory boundary.

## Review status
The observation patch is ready for independent source review. It is not authorisation to normal apply/build/run. P4 geometry endpoint is a separate private proof; unknown reconstruction metadata stay unknown.
