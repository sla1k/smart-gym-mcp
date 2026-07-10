# CLAUDE.md

Project knowledge lives in shared docs — read them, don't rediscover:
- [`DESIGN.md`](DESIGN.md) — **start here**: status table, verified facts (settled — do not
  re-derive or re-litigate them), architecture invariants, decision log
- [`specs/`](specs/) — source of truth per area; `specs/02-write-and-sync.md` Part E has the
  module layering and the composition table for unimplemented tools
- [`FEATURES.md`](FEATURES.md) — backlog

## Working rules for this repo
- Follow the architecture invariants in DESIGN.md exactly — especially: thin tools, everything
  mutating inside `lifecycle.managed_write`, and `writes.mark_routine_pending` on every
  mutation of an existing routine.
- The DB is the user's **live personal training data**. Never test live applies on real
  routines — only on `ZZ-`prefixed throwaways, with the user in the loop for cross-device
  (iPhone) confirmation. Backups land in `~/.smartgym-mcp/backups/<ts>/`.
- Before trusting a NEW kind of mutation syncs, run the Phase-0-style observation from the
  decision log (DESIGN.md) instead of assuming the dirty flag covers it.
- Tests must never touch the real DB — use the `temp_db_cfg` fixture (temp copy).

## Commands
```sh
uv run pytest
uv run ruff check src tests && uv run ruff format src tests
uv run mypy src
uv run smartgym-mcp            # stdio server
uv run mcp dev src/smartgym_mcp/server.py   # MCP Inspector
```
