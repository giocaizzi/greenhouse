# Refactor notes

Running log for the behavior-preserving refactor on `claude/focused-hawking-7to7o3` (baseline `main` @ `a1b2622`).

## Owner decisions

- **No `src/` layout.** The owner wants to keep the `libs/` uv workspace (`libs/greenhouse-{core,server,cli}/greenhouse_*`).
  The PyPA src-layout recommendation (§1.1) is deliberately not applied; hatch package paths and pytest `pythonpath`
  stay as they are. Restructuring happens inside each package.
- **Method-level cleanup is in scope.** The owner wants methods made slick and clear with clean interfaces, not only
  module reorganization. The plan includes a per-method pass (extract function, guard clauses, naming, typed explicit
  parameters, parameter objects, CQS). Frozen public contracts still bound it; internal signatures may change when all
  call sites move in the same commit.
- **Branch:** work lands on `claude/focused-hawking-7to7o3` (the session's designated branch) instead of
  `refactor/clean-structure`.

## Observed bugs (not fixed)

_None recorded yet._

## Baseline warnings (recorded, not fixed)

_To be filled from the Gate 0 baseline run._
