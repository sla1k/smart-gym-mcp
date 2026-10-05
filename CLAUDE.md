# CLAUDE.md

Project knowledge lives in shared docs — read them, don't rediscover:
- [`DESIGN.md`](DESIGN.md) — **start here**: status table, verified facts (settled — do not
  re-derive or re-litigate them), architecture invariants, decision log
- [`docs/superpowers/specs/2026-10-05-api-client-design.md`](docs/superpowers/specs/2026-10-05-api-client-design.md)
  — source of truth for the API client (module layering §5, tool surface §6, sections §10);
  `specs/` holds the superseded DB-era specs
- [`FEATURES.md`](FEATURES.md) — backlog

## Working rules for this repo
- Follow the architecture invariants in DESIGN.md exactly — especially: thin tools, every write
  through `service.RoutineService`, every edit of an existing routine through `diff_routine`.
- The SmartGym account is the user's **live personal training data**. Live checks only on
  `ZZ-`prefixed throwaway routines, with the user confirming on the iPhone. Snapshots land in
  `~/.smartgym-mcp/backups/<ts>/`.
- Before trusting a NEW kind of server write, capture the app doing it first (golden fixture in
  `tests/fixtures/api/`) instead of assuming the wire shape.
- Tests never touch the network or the account — use the fixtures and `tests/fakes.FakeClient`.

## Commands
```sh
uv run pytest
uv run ruff check src tests && uv run ruff format src tests
uv run mypy src
uv run smartgym-mcp            # stdio server
scripts/build.sh               # PyInstaller build + codesign + install
uv run mcp dev src/smartgym_mcp/server.py   # MCP Inspector
```
