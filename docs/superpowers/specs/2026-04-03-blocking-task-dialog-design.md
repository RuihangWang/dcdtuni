# Blocking Task Dialog Design Spec

**Date**: 2026-04-03  
**Goal**: Add a floating log dialog that blocks UI during long-running operations (mesh preview, postprocessing init, volume rendering), with cancel support and collapsible UI.

## Component: `showBlockingTaskDialog(options)`

A reusable function that creates a floating dialog + UI mask. Used by 3 operations.

### Dialog Window
- Style: Pattern B (floating, draggable title bar, `position:fixed`, z-index 10001)
- Title bar: operation name + collapse toggle button (▼/▲) + ✕ close (only shown on fail)
- Log area: monospace, auto-scroll, max-height ~200px, scrollable
- Cancel button: full-width at bottom
- On success: auto-close after 1s
- On failure: keep open, show error in red, show ✕ close button

### Collapse Behavior
- Click collapse toggle: log area hides, dialog shrinks to just title bar + cancel button
- Click again: log area expands back
- Window stays draggable in both states

### UI Mask
- Semi-transparent overlay covering everything EXCEPT `#center-view` (viewport)
- Viewport keeps `pointer-events: auto` — user can rotate/pan camera
- Left panel, right panel, toolbar, bottom panel buttons: all blocked by overlay
- Mask removed on cancel or completion

### API
```js
const task = showBlockingTaskDialog({
    title: 'Loading mesh...',
    onCancel: () => { abortController.abort(); }
});
task.log('Connecting to server...');       // append line
task.done();                               // success → auto-close 1s
task.fail('Error: empty mesh data');       // failure → stay open with error
task.isCancelled();                        // check if user cancelled
```

### Integration Points
1. `mpLoadMesh()` (line ~17203) — wrap in dialog, pass AbortSignal to fetches
2. `ppInitPostProcess()` (line ~15119) — wrap in dialog, pass AbortSignal to fetches
3. `ppApplyVolume()` (line ~14355) — wrap in dialog, pass AbortSignal to fetches

### Cancel Mechanism
- Each operation creates `new AbortController()`
- All `fetch()` calls pass `{ signal: controller.signal }`
- Cancel button calls `controller.abort()` + cleans up partial state
- `task.isCancelled()` returns true so operation can bail early between steps
