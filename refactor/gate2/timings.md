# Measured alias timings (plan T0.11)

Measured on the WP0 head (`refactor/wp0-tooling`, after T0.10). Each alias ran once through the plan §0.3 `t` helper:
`PYTHONHASHSEED=0 flock /tmp/greenhouse-tests.lock uv run pytest -q -n 2 …`. The machine has 4 cores and only this
worktree was active, so there was no lock contention. Wall time includes uv start-up and xdist start-up.
These numbers replace the §0.3 estimates.

| Alias | Tests | Wall (measured) | §0.3 estimate | Ratio (est. ÷ measured) |
|---|---|---|---|---|
| full suite (`FULL`, `-n 2`) | 2797 | **9m56s** | ≈ 11–12 min | 1.1–1.2× |
| conservative map `C(gc.logic.engine)` | 906 | **5m59s** | 9–11 min | 1.5–1.8× |
| `D(gc.repository)` | 777 | **5m50s** | ≈ 9 min | 1.5× |
| `D(gc.schemas)` | 674 | **5m42s** | ≈ 9 min | 1.6× |
| `$TUI` | 127 | **2m33s** | ≈ 3 min | 1.2× |
| `$PIPE` | 220 | **2m06s** | ≈ 3 min | 1.4× |
| `$RENDER` | 307 | **1m15s** | — | — |
| `$ENGINE` | 339 | **1m10s** | ≈ 2 min | 1.7× |
| `$WEB` | 283 | **1m07s** | ≈ 3 min | 2.7× |
| `$SCHED` | 153 | **55s** | ≈ 2 min | 2.2× |
| `$CHECK` | 90 | **50s** | — | — |
| `$API` | 210 | **46s** | ≈ 1 min | 1.3× |
| `$CLI` | 537 | **16s** | — | — |
| `$CORE` | 45 | **9s** | ≈ 1 min | 6.7× |
| `$DEV` | 233 | **9s** | ≈ 1 min | 6.7× |
| `$SETTINGS` | 88 | **7s** | — | — |

Mutation pass (T0.10 validation): `refactor/gate1/mutate.py --module logic/stress.py` with `$ENGINE`. It ran
66 mutants in **25.5 min** wall, within the 45-minute budget. Killed mutants took 5–22 s each; survivors took about
65 s each, because a survivor runs the whole set. Larger targets generate more mutants: `engine.py` 412,
`irrigation.py` 288, `scheduler.py` 160, `issues.py` 144. Those runs will exceed 45 minutes, so for them use
`--function`/`--lines` from the safety docs, or `--sample 150 --seed 0`.

**Verdict.** No alias is slower than its estimate. None is more than 2× slower, which is the threshold for
re-planning. Several aliases are more than 2× faster than estimated: `$WEB`, `$SCHED`, `$CORE`, `$DEV`. Those
estimates were scaled from a loaded Gate 1 machine. So the wave plan is conservative and does not need
re-planning. With two worktrees sharing the lock, expect waits of up to one other worktree's run on top of these
numbers.
