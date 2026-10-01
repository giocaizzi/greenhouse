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

2. **SAFETY — `dry_run_global` is never enforced.** The preference (labelled "Global dry-run (never actuate)" in the TUI,
   `tui/resources.py:113`) is stored (`models.py:422`), editable via API/web/CLI/TUI and shown in the web context
   (`web/context.py:49`), but no actuation path reads it (`grep -rn dry_run_global libs/` — only storage/display hits).
   Verified by the orchestrator. Not fixed (behavior change); recommend a dedicated fix PR.
3. **SAFETY — IK10PW keep-alive fallback can leave the pump on until the device's own auto-off.**
   `devices/irrigators/ik10pw.py:160-177`: `self.on()` runs first, then `signal.signal(SIGTERM, …)` is called *outside*
   the `try`. `signal.signal` raises `ValueError` when called off the main thread (APScheduler worker / FastAPI
   threadpool), so the `finally` that sends `off()` never runs and the error propagates with the pump ON. Bounded by the
   firmware auto-off timer the keep-alive is meant to refresh. Verified by reading; to be pinned by a characterization
   test. Not fixed; recommend a dedicated fix PR.

Further suspected bugs (B-3…B-25: offline flag after every sync, caps never checked in the automatic pipeline,
re-raised alerts not re-notified, `water_warning` meaning differs between engine and health monitor, 500 on duplicate
device id in web create routes, vacation end < start accepted by the API, `local_key` returned in plain text, …) are
listed with file:line evidence in `refactor/00-smells.md` ("Observed bugs"). They are recorded, not fixed; each one that a
refactored module touches gets a characterization test pinning current behavior.

## Baseline warnings (recorded, not fixed)

_To be filled from the Gate 0 baseline run._
