# Reloading an existing completed liquid run

Static source finding: full page reload initializes liquidRuntimeClient.session to null. Only the ordinary Prepare/New action installs a runtime session. The current Panel has no attach/restore-existing-run control, and refreshRuntimeFrames returns immediately without an existing session. No persistent runtime-run identity recovery path was found in the current React source.

The completed b69f result can still be read without solving: open liquid post-processing for its case, refresh the solve-record scan if needed, then select run-b69f923f8c76499eb4ec60c64ae46cb4 in #lsm-pp-run-select. This fetches the stored result into liquidSession and does not attach liquidRuntimeClient. Therefore the absence of the runtime stale banner after this operation alone is not a validation of its comparison logic.

For actual UI validation after reload, normal Prepare can create a new initialized ready session. Playback/view/log/save can then be exercised against that captured input clock, and a committed input edit must show the warning. This is a separate initialized session, not a restoration of the completed run. ROOT owns any such actual UI operation. This audit made no browser/API/business call.
