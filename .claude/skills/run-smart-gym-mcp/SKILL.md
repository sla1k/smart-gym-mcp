---
name: run-smart-gym-mcp
description: Run, start, smoke-test, inspect and drive the smartgym-mcp stdio MCP server offline — list its tools, call any tool (dry-run or applied writes) against a fake SmartGym account, read the catalog resources, and see the exact POST a write would send. Use when asked to run the server, try a tool, check a change works end to end over stdio, or run the tests.
---

`smartgym-mcp` is a local stdio MCP server (Python, `uv`). An agent drives it with
`.claude/skills/run-smart-gym-mcp/driver.py`: an MCP client that starts the server as a
subprocess over stdio, exactly like Claude Desktop does, against an **offline fake account**
(the synthetic fixture `tests/fixtures/api/history_all_synthetic.json` plus a generated app
bundle). Nothing reaches the network or Vlad's live SmartGym account.

All paths are relative to the repo root.

## Prerequisites

`uv` and Python 3.12 (`.python-version`). Nothing from `apt-get`; the server is pure Python
and runs on Linux even though the app it talks to is macOS/iOS only.

```bash
uv sync
```

## Run (agent path): the driver

```bash
uv run -q python .claude/skills/run-smart-gym-mcp/driver.py smoke
```

`smoke` prints: the 15 tools with read / write / write idempotent, the 3 resources, then
`smartgym_health` (`ok: true`, 1 routine), `smartgym_list_routines`, `smartgym_get_routine`,
a catalog read, a dry-run rename and the same rename with `dry_run: false`.

One tool call, or one resource:

```bash
uv run -q python .claude/skills/run-smart-gym-mcp/driver.py call smartgym_get_routine '{"routine": "zz-fb"}'
uv run -q python .claude/skills/run-smart-gym-mcp/driver.py read smartgym://catalog/equipment
uv run -q python .claude/skills/run-smart-gym-mcp/driver.py tools
```

Several steps against ONE server (one per line: `TOOL {json}`, `read URI`, `# comment`):

```bash
uv run -q python .claude/skills/run-smart-gym-mcp/driver.py session <<'EOF'
smartgym_add_exercise {"routine": "ZZ-FB", "exercise": "plank", "sets": [{"reps": 1, "weight_kg": 0}]}
smartgym_add_exercise {"routine": "ZZ-FB", "exercise": "plank", "dry_run": false}
smartgym_get_workout_history {"date_from": "2026-01-01"}
smartgym_get_routine {"routine": "nope"}
smartgym_create_program {"routines": [{"name": "ZZ-New", "goal": "Strength", "exercises": [{"exercise": "Barbell Squat"}]}]}
EOF
```

Output: `>>> step`, then the tool's structured result as JSON, prefixed `ERROR` when the call
returned `isError`. After a step that POSTs, a `POST <path> (recorded, not sent) {form}`
line shows the form the write built.

What the fake account holds: one active routine `ZZ-FB — Test` (id 3000001; warmup
Shoulder Circling, main Cable Chest Press, cooldown Cross Arm Stretch), an archived
`ZZ-Old`, a removed `ZZ-Gone`, two logged sessions in Sept 2026, and the custom exercise
`Dead Hang`. Bundle exercises: the routine's four plus `Plank`, `Barbell Squat`,
`Dumbbell Row`.

Sandbox: `/tmp/smartgym-driver` (override with `SMARTGYM_DRIVER_DIR`), wiped at the start
of every run: `server.log` (the server's stderr), `posts.jsonl` (every recorded POST),
`backups/` (the snapshots writes save), `SmartGym.app/` (the generated bundle).

### --real: the unpatched entry point

```bash
uv run -q python .claude/skills/run-smart-gym-mcp/driver.py --real call smartgym_health
```

Runs the installed `smartgym-mcp` script with no credentials file: health answers
`ok: false` with the "credentials not found … scripts/capture_credentials.py" problem, every
account tool fails with that message, the catalog resources work off the generated bundle.
That is what a client sees before setup, and it checks the real `main()` wiring.

### MCP Inspector CLI: schema portability and exit codes

The official Inspector (Node 22, `npx`) can drive the same offline server, because
`driver.py serve` builds the sandbox and sets the server's env itself:

```bash
I="npx -y @modelcontextprotocol/inspector@2.14.0 --cli uv run -q python .claude/skills/run-smart-gym-mcp/driver.py serve"
$I --method tools/list --strict > /dev/null; echo "exit $?"   # 0 = no schema-portability errors
$I --method tools/call --tool-name smartgym_update_routine --tool-arg routine=ZZ-FB --tool-arg name=X
$I --method tools/call --tool-name smartgym_get_routine --tool-arg routine=nope; echo "exit $?"   # 5 = isError
$I --method resources/read --uri smartgym://catalog/equipment
```

Run `tools/list --strict` after any change to a tool's signature, `Field` constraints or
return model: it reports schema constructs some clients can't handle and exits 6 on an
error-severity one. Exit codes: 0 ok, 5 the tool returned `isError`, 6 a strict finding.

## Run (human path)

`uv run smartgym-mcp` starts the stdio server and waits on stdin; it is meant to be
spawned by an MCP client (Claude Desktop config in `README.md`). Real use needs
`~/.smartgym-mcp/credentials.json` (from `scripts/capture_credentials.py`, a mitmproxy
capture of the iPhone app) and `/Applications/SmartGym.app` — neither exists in a
container. Do not point the driver or any tool at the live account; AGENTS.md allows live
checks only on `ZZ-` routines with Vlad confirming on the iPhone.

## Test

```bash
uv run pytest -q
uv run ruff check src tests scripts/smoke_stdio.py
uv run ruff format --check src tests scripts/smoke_stdio.py
uv run mypy
```

333 tests pass in about 4 s. Most PRs touch services/diff/payloads: the tests for those
call the code directly with `tests/fakes.FakeClient` (`tests/test_service.py`,
`tests/test_payloads.py`); `tests/test_server.py` drives `server.mcp` in-process with
`mcp.Client(server.mcp)`. Use the driver for what the tests can't show: the server as a
real subprocess, and a tool's full JSON answer.

## Gotchas

- **`dry_run: false` ends in an error with the fake — by design.** The POST is recorded and
  the snapshot saved, then the verifying re-read gets the unchanged fixture and the tool
  answers `SmartGym accepted the edit, but the routine now differs from the plan: …`.
  That error means the write ran all the way to verification. The fake never applies a
  write, so state does not carry between `session` steps either.
- **The recorded form lacks `authID`, `appVersion`, `requestDate`.** `ApiClient` adds those;
  the driver replaces it below that layer. Long fields are cut at 300 characters in the
  printout; `posts.jsonl` has them whole. Compare with the captured app forms in
  `tests/fixtures/api/update_*.json`.
- **How the fake is injected:** the driver's `serve` sets `server.AppContext` to a
  `partial(runtime.AppContext, factory=…)` before `server.main()`. Patching
  `runtime.build_services` does nothing — it is bound as the dataclass field's default when
  `runtime` is imported. `lifespan()` looks `AppContext` up in `server`'s globals at start.
- **Piping raw JSON-RPC into `smartgym-mcp` drops tool answers.** With
  `printf '…initialize…' '…tools/call…' | uv run smartgym-mcp`, only `initialize` is
  answered: stdin hits EOF and the server exits while the tool call is still on its worker
  thread. Keep stdin open (`{ printf …; sleep 2; } | …`) or use the driver.
- **Tool errors are results, resource errors are protocol errors.** A failed tool returns
  `isError: true` with text `Error executing tool <name>: <message>`; a missing catalog
  file comes back as JSON-RPC error `-32603` from `resources/read`.
- **Catalog resources default to `/Applications/SmartGym.app`.** On Linux they fail with
  `Catalog Equipments.json not found …` unless `SMARTGYM_APP_BUNDLE` points at a bundle;
  the driver generates a tiny one with `Info.plist` version 8.0.3 so health shows no
  version warning.
- `smartgym_list_routines` shows 1 routine though the fixture has 3: `ZZ-Old` is archived,
  `ZZ-Gone` removed. `smartgym_get_routine` shows 3 exercises of the 4 (one is removed).
- **Inspector `--tool-arg` JSON-parses values:** `routine=3000001` is sent as the number
  3000001 and fails input validation (`Input should be a valid string`). Quote it as JSON:
  `--tool-arg 'routine="3000001"'`.
- **The Inspector doesn't forward your shell's env to the server**, only a default set; a
  `SMARTGYM_*` export is ignored (health then names `~/.smartgym-mcp/credentials.json`).
  Pass `-e KEY=VALUE`, or use `driver.py serve`, which needs none.
- The fake account's time zone is fixed to `Europe/Madrid`, as in the tests.

## Troubleshooting

- `TypeError: Client.__init__() got an unexpected keyword argument 'errlog'` — in mcp 2,
  `Client` takes no `errlog`; wrap the params: `Client(stdio_client(params, errlog=f))`.
- `warning: The UV_NATIVE_TLS environment variable is deprecated` on every `uv run` — the
  container's environment, harmless; `-q` hides it.
- `smartgym-mcp not on PATH` (driver exit) — `--real` needs the project's
  venv on PATH; start the driver through `uv run`, not a bare `python`.
- A step hangs or the server dies: read `/tmp/smartgym-driver/server.log`.
