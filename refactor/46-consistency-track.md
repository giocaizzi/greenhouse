# Consistency track — one way to do each thing, everywhere (owner directive 2026-10-02)

Runs after the implementation waves, alongside the drift track (`45-drift-track.md`), the lint ratchet
(`40-lint-ratchet.md`) and the dead-code sweep. Seeded by a whole-repo consistency audit; each finding becomes a task.
Behavior-preserving → `refactor(...)`; observable change → `fix(consistency): …` with reviewed golden/test updates.

## Known pattern inconsistencies to converge (audit will extend)
| Pattern | Today | Target |
|---|---|---|
| Transactions | 68 `session.commit()` calls in routes; services sometimes commit | one owner per request (service or a request-scoped unit), same in API and web |
| Entity lookups / 404 | inline `get` + `HTTPException` 20×+ alongside `deps.require_*` | `deps.require_*` everywhere |
| Service results | untyped dicts rebuilt into Pydantic in routes | typed results (dataclass/TypedDict) at the service boundary; Pydantic only at the HTTP edge |
| Repository bypass | 17 `repo.session` uses in services/routes | repository methods |
| Clock / env access | scattered `time.time()`, `os.environ` reads (8 direct env reads) | settings loaded once at the edge; one clock idiom |
| Logging | mixed f-strings / %-style, levels | %-style lazy logging, consistent levels (logger names unchanged) |
| Error handling | broad `except Exception` swallowing (BLE 33) | narrow, logged; behavior changes labeled |
| Constants | literals in routes/web/TUI | `constants.py` (core) / module constants elsewhere |
| Docstrings | 500+ missing/inconsistent | Google style, *why*-oriented, everywhere |
| Naming | `utils`-style dumping, mixed verbs | responsibility names; one vocabulary per domain concept |
| Typing | strict on 61 modules | strict everywhere |
| Stale artifacts | stale comments, TODOs, outdated docs (CLAUDE.md, plugin) | none |
