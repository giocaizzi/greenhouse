# Phase 1 safety net — CLI (`greenhouse`), gap items G5 + G6

New files only:

- `tests/cli/test_contract_help.py` (90 tests)
- `tests/cli/test_contract_json_output.py` (276 tests)
- goldens: `tests/golden/cli/help/*.txt` (74 pages), `tests/golden/cli/params.json`, `tests/golden/cli/requests_and_output.json`

No production, fixture or existing test file was touched. Nothing is committed (`tests/**/test_contract_*.py` and `tests/golden/` are in `.git/info/exclude` for now).

## What is pinned

| Test | Contract / invariant |
|---|---|
| `test_command_tree_has_every_help_page` | 74 pages = 13 groups + 61 leaves, walked from `typer.main.get_command(app)` |
| `test_params_golden` → `params.json` | Every group and command: help, short_help, hidden, deprecated, `no_args_is_help`, `invoke_without_command`, subcommand **registration order**. For each param in declaration order: name, kind, opts, secondary opts, default, required, type name and class, range min/max, envvar, is_flag, multiple, nargs, hidden, prompt, hide_input, is_eager, expose_value, help |
| `test_help_page_golden[<path>]` ×74 → `help/greenhouse.<dotted.path>.txt` | Exact `--help` stdout (prog name `greenhouse`), exit 0, empty stderr, exact tail `╯\n\n` |
| `test_group_without_args_prints_help_and_exits_2` ×13 | Current behavior: a bare group prints its help page minus the final blank line and exits **2** |
| `test_help_is_width_stable_against_hostile_terminal_env` | No ANSI codes, lines ≤ 100 wide, re-render stays identical |
| `test_every_cli_command_has_a_case` | Every leaf in the live tree has at least one G6 case. A new command with no case fails |
| `test_every_client_method_has_a_case` | The public `IrrigationClient` methods and the cases match exactly, both ways |
| `test_requests_and_output_golden` → `requests_and_output.json` (only test that writes it) | **cli** (182 cases): each `httpx.Client` built (base_url, timeout=30.0, headers); each request (method, full URL, path, raw_path, query pairs in send order, raw body bytes, parsed JSON, Authorization, Content-Type); stdout, stderr, exit code; any non-`SystemExit` exception type; files written; token file after the run (content and mode). **client** (106 cases): every method, with default and explicit args, None-dropping vs None-keeping bodies, and `_request` handling of 4xx/5xx/connect/CSV/list bodies. **client_constructor** (7): token `""`/None/env/file, header merge (token beats a passed Authorization), default base URL |
| `test_cli_case[<id>]`, `test_client_case[<id>]` | The same cases re-run one by one and compared with their golden entry, so a failure names the command |
| `test_exit_code_contract`, `test_real_connection_refused` | Readable asserts: exit 0/1/2 table; `Error: Cannot connect to server: …` with a real refused TCP connect |

What the CLI cases cover:

- **Server URL:** default `http://localhost:8000`, `$IRRIGATION_SERVER_URL`, `--server`, `--server` winning over env, `--server ""` falling back to env, base URL with a path prefix.
- **Token:** none, a token file (stripped), an empty file, env winning over file, `$HOME/.config` used when `XDG_CONFIG_HOME` is unset.
- **`login`:** the stored and env tokens are both suppressed. Also covered: prompts, `--print-token`, empty token, 401.
- **`logout`:** with and without a token; server 500 or connect error still clears the local token.
- **Every `--yes` / `-y` command:** run with `--yes`, with `-y`, with the prompt declined (`Aborted.`, exit 1, no request) and with it confirmed.
- **`check` and `monitor`:** every exit branch.
- **`stats --export`:** CSV body, and a JSON body.
- **Usage errors (exit 2, exact rich error panel):** unknown command, unknown subcommand, missing argument, bad int, missing required option, out-of-range values. `tui --refresh -1` is included.

## Not pinned (and why)

- **`greenhouse tui`** apart from `--help`, its params and the `--refresh -1` usage error. The TUI package owns it.
- **`--install-completion` / `--show-completion` side effects.** They write to shell rc files and depend on the detected shell. Only their help text and params are pinned.
- **Per-line trailing spaces in help goldens are stripped.** Rich pads every line to the console width. The repo's pre-commit `trailing-whitespace` / `end-of-file-fixer` hooks would rewrite the goldens on commit, so the padding is dropped (it follows from the pinned width anyway). Interior spacing, line breaks and box drawing are byte-exact, and the exact output tail is asserted separately. Other agents writing `.txt`/`.html` goldens face the same hook.
- **Normalization in `requests_and_output.json`:** the per-case pytest tmp dir becomes `<TMP>`. It is random and appears in `login`'s `Token stored at …`. The `<VERSION>` normalizer is applied to help goldens, but no version string occurs today.
- **Tracebacks.** For uncaught exceptions only the exception type is recorded; the traceback text depends on line numbers.

## Determinism evidence

Each line below covers both files, all green, and the golden sha256 was identical before and after every run:

- 2 sequential runs: 366 passed.
- `-p xdist -n 2`: 366 passed.
- `TZ=America/New_York`: 366 passed.
- Hostile environment: 366 passed. Variables: `TERMINAL_WIDTH=60 COLUMNS=40 LINES=10 FORCE_COLOR=1 GITHUB_ACTIONS=1 PY_COLORS=1 TTY_COMPATIBLE=1 _TYPER_COMPLETE_TEST_DISABLE_SHELL_DETECTION=1 GREENHOUSE_API_TOKEN=leak IRRIGATION_SERVER_URL=http://leak.example TERM=xterm-256color`.
- Inside a real pty at 47 columns (`script -qec "stty cols 47 …"`): 366 passed.
- Existing `tests/cli/test_cli.py` + `test_completeness.py`, run together with the new files in both orders: 446 passed.
- `ruff check` and `ruff format --check`: clean.

How width stability works:

- `clean_env` sets COLUMNS=100, TERM=dumb and NO_COLOR.
- The `stable_cli_env` fixture also deletes the rich/typer/click render variables.
- It monkeypatches the import-time constants `typer.rich_utils.MAX_WIDTH=None` and `FORCE_TERMINAL=False`, which typer reads once from `TERMINAL_WIDTH` / `FORCE_COLOR` / `GITHUB_ACTIONS`.
- It resets `rich._console`, the global console, which caches its width when created.
- `params.json` is built inside the fixture because typer's completion options change with `_TYPER_COMPLETE_TEST_DISABLE_SHELL_DETECTION` at `get_command()` time. Found by the hostile-env run.

## Bugs or quirks observed, not fixed

Each is pinned in the golden. The case id is in parentheses.

1. A blank `GREENHOUSE_API_TOKEN` (whitespace) disables the token-file fallback, so no Authorization is sent (`token.blank_env_current_behavior_ignores_file`; `client.py:25-26`).
2. Only `httpx.ConnectError` is mapped to `ServerError`. `ReadTimeout` and other transport errors escape as a traceback with exit 1 (`error.read_timeout_current_behavior_uncaught`; `client.py:88`).
3. A 2xx response with an empty or non-JSON body raises `JSONDecodeError` uncaught (`error.empty_200_body_current_behavior_uncaught`; `client.py:101`).
4. FastAPI 422 `detail` lists are printed as a Python repr: `Error: [{'loc': …}]` (`error.422_detail_list_python_repr`; `_helpers.py:23`).
5. ID `0` is treated as "not given":
   - `check 0` gives "provide a cluster ID or --all" (`check.cluster_zero_current_behavior`).
   - `plant list --cluster 0` and `sensor list --cluster 0` list every cluster (`plant.list.cluster_zero_current_behavior_lists_all`, `sensor.list.cluster_zero_current_behavior_lists_all`).
6. `stats --export` with a non-CSV response writes an empty file and still prints `Exported to …` (`stats.export_json_response_writes_empty`).
7. `delete_scheduler_job` puts the job id into the path unescaped, so a `/` changes the route (`client/delete_scheduler_job.path_not_escaped`).
8. `irrigator add --device-ip ""` drops the field (truthiness check), while `irrigator update --local-key ""` sends it (`is not None`) (`irrigator.add.empty_ip_current_behavior_dropped`, `irrigator.update.empty_key_current_behavior_sent`).
9. Every `call()` builds a new `httpx.Client` and never closes it. `plant list` / `sensor list` with no cluster build N+1 clients (visible in the `clients` arrays). Not a contract, but recorded.
10. Bare groups exit 2 rather than 0 (`test_group_without_args_prints_help_and_exits_2`).

## Production lines and branches guarded (mutation targets)

- **`greenhouse_cli/main.py`:**
  - `typer.Typer(help=…, no_args_is_help=True, rich_markup_mode="rich")`
  - the `--server` option help text, `ctx.obj = server`
  - registration order at lines 43-58
- **`greenhouse_cli/client.py`:**
  - `_default_token_path` (XDG vs `~/.config`)
  - `load_stored_token` lines 24-34 (env strip, blank env, missing/empty file)
  - `store_token` chmod `0o600`, `clear_stored_token` return value
  - `__init__` lines 79-83 (header merge, `token is not None`, `timeout=30.0`)
  - `_request` lines 86-101 (ConnectError message, `>= 400`, `.get("detail", resp.text)` fallback, `text/csv` → `{"csv": …}`)
  - every method's verb, path, `json=` vs `params=`, None-filtering (`{k: v … if v is not None}` vs raw `kwargs` in `add_plant`/`add_sensor`), param order in `list_activity`/`list_alerts`, `check`/`sync_alerts` branch on `cluster_id is None`, `stats_export` `.get("csv", "")`, `update_vacation`/`update_window` per-field `is not None`
- **`commands/_helpers.py`:** `get_client` (`ctx.obj or env or default`), `call` (`Error: {detail}` to stderr, exit 1), `output` (`print_json(json.dumps(data, default=str))`).
- **`commands/operations.py`:**
  - `irrigate` exit 1 on `action == "error"` (line 32)
  - `check` lines 42-53 (no-target error, `--all` precedence, `isinstance(dict)`, has_alerts → 2 before error → 1)
  - `monitor` needs_water → 2 (line 60)
  - `stats` export branch (lines 94-99)
  - `stop-all` confirm text
- **`commands/auth.py`:** `_login_client(token="")`, the login branches (print_token, empty token, store and message), logout swallowing `ServerError` plus both messages.
- **`commands/plants.py:51-58`, `commands/sensors.py:33-40`:** the cluster-or-all iteration, skipping empty lists.
- **`commands/irrigators.py:29-42`, `:111-117`:** config-blob construction (truthiness vs `is not None`).
- **Every `typer.confirm(…, abort=True)` prompt text:** in clusters, plants, sensors, irrigators, vacation, windows, operations.
- **Every option name, default, range and help string** (help and params goldens), including `alerts list --limit` (1..500), `decisions list --limit` (1..200), `windows --start-hour`/`--end-hour` (0..23) and `--weekday-mask` (1..127), and `tui --refresh min=0`.
