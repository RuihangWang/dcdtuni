# Prepared liquid run stale banner

The actual result playhead rematerializes scheduled project fields and emits the general rendering revision. Log and save notifications also increase that revision. These are not committed input edits; comparing the prepared run to the rendering revision incorrectly showed stale inputs after an otherwise unchanged run.

Add a separate committed document-input edit counter, retain all rendering revision/listener behavior, and capture/compare that counter in LiquidExperimentPanel. Accepted load/edit/undo/redo/legacy in-place commits increase it; playback/log/save/refused edits do not. The emission audit records every caller and rejected/accepted branch. All real accepted document edits conservatively remain stale, including metadata/display edits. This is not a content-hash equivalence claim.

Eight new actual projectStore plus rendered Panel regressions cover final playhead24.5, log/save, real heat/keyframe edits, undo/redo, identical-UID document load, legacy in-place physics commit, refused off-start geometry, and Prepare capturing the input clock after materialization. The corrected meaningful baseline failed4/passed4. Final new8 plus existing18 transient store and1 Panel tests passed27/27. The first invalid authored fixture log is preserved and explicitly excluded from regression evidence.

No build or browser rerun occurred for this stale patch. The earlier Inspector build is separately frozen and unchanged. ROOT owns review, Git, build/deployment and actual browser verification.
