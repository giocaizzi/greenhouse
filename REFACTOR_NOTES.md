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

1. **`WWW-Authenticate` header dropped on JSON 401s.** `greenhouse_server/web/exception_handlers.py:41-44` — the global
   `HTTPException` handler returns `JSONResponse({"detail": exc.detail}, status_code=exc.status_code)` without
   `headers=exc.headers`, so the `WWW-Authenticate: Bearer` header raised for 401s on `/api/v1` and `/mcp` never reaches
   the client. Found by the contract extractor (probe script in the scratchpad). To be pinned by a characterization test
   in Phase 1 (asserting the header is **absent**). Not fixed.

## Baseline warnings (recorded, not fixed)

_To be filled from the Gate 0 baseline run._
