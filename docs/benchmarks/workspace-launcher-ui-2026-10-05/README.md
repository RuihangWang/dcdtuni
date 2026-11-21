# Launcher and workspace UI validation, 2026-10-05

Environment: 40901 integration worktree, branch goal/integration-20261005.
Historical evidence and completion are separate: real Kit and browser acceptance
are recorded by the integration owner; the checks below are directed tests.

## Launcher: 217a5113

- Shell syntax and whitespace checks passed.
- Seven launcher lifecycle tests passed using a fake Kit with no GPU context.
- Coverage: --gpu aliases, implicit React, reused-backend rejection, GPU0 UUID
  default and current-worktree extensions, repository-local portable/log/cache
  paths, supervisor teardown before Kit listening, and build-failure cleanup.
- The fake Kit child exited after its own supervisor control port was closed
  with fuser; no process-name searches or broad termination were used.
- All temporary launcher test directories were verified inactive and removed.
  The active real Kit runtime was not touched.

## Workspace UI: 83033f55

- 27 directed tests passed: toolbar view updates, shortcut editor, natural-size
  toolbar fit, theme selection persistence, responsive chart drawing buffer,
  MMS small-response tick precision, analytic dashes, and missing-sample gaps.
- TypeScript no-emit validation and whitespace checks passed.
- Common mode and renderer controls now precede expandable modelling, view, and
  preference groups. Narrow toolbar layouts scroll rather than shrinking text.
- Inspector labels and value controls use aligned columns. Chart legends wrap
  outside the plot; changing panel width redraws the canvas at device resolution.
- Engineering and slate themes retain previous saved values and Chinese/English
  translation support.

Actual browser screenshots, renderer transitions and solver correctness are
integration acceptance items, not claims established by these component tests.
