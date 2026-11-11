# AGENTS.md

Local stdio MCP server (Python 3.12+, `uv`, MCP SDK 2.x) that edits SmartGym routines through
SmartGym's own server API.

## Read first
- [`DESIGN.md`](DESIGN.md) — status, architecture invariants, decisions. Settled: don't
  re-derive or re-litigate them.
- [API-client spec](docs/specs/2026-10-05-api-client-design.md) — source of truth for the wire
  protocol and verified server behaviour (§3, §4, §10, §11), modules (§5), tool surface (§6).
- [`FEATURES.md`](FEATURES.md) — backlog.

## Commands
```sh
# CI checks: run all of them before every push
uv run ruff check src tests scripts/smoke_stdio.py
uv run ruff format --check src tests scripts/smoke_stdio.py   # drop --check to fix
uv run mypy                    # src, tests and scripts/smoke_stdio.py
uv run pytest
uv build

# Try a change end to end over stdio, offline (fake account): see the run-smart-gym-mcp skill
uv run -q python .claude/skills/run-smart-gym-mcp/driver.py smoke
npx @modelcontextprotocol/inspector uv run smartgym-mcp
```

## Live account
- The SmartGym account is the user's live personal training data. Never point a tool or the
  driver at it on your own; live checks only on `ZZ-`prefixed throwaway routines, with the user
  confirming on the iPhone. Snapshots land in `~/.smartgym-mcp/backups/<ts>/`.
- Tests never touch the network or the account: use `tests/fixtures/api/` and
  `tests/fakes.FakeClient`.

## Code rules
- Follow DESIGN.md's architecture invariants: thin tools in `server.py`, every write through
  `service.RoutineService`, every edit of an existing routine through `diff_routine`.
- Before trusting a new kind of server write, capture the app doing it and add a golden fixture
  in `tests/fixtures/api/` (how: spec §9). Don't guess the wire shape.
- Test a new tool, or a change to what a tool accepts or answers, the way a host calls it: the
  in-memory `mcp.Client(server.mcp, raise_exceptions=True)`, asserting on the `CallToolResult`
  (`is_error`, `structured_content`). A direct call with a fake `ctx` is only for wiring and
  call-lock tests that need to hold or time a call.

## Gotchas
- `tests/fixtures/tool_surface.json` is what every host's model sees of the tools. A change to a
  tool's signature, docstring or return model fails `test_tool_surface…`: review the diff, then
  regenerate with `UPDATE_TOOL_SURFACE=1 uv run pytest -k tool_surface`.
- `scripts/smoke_stdio.py` runs on the built wheel only in the release build after a merge, so
  lint and type-check it locally. `scripts/spike/` holds throwaway capture scripts and is not
  checked.
- Ruff and mypy target the 3.12 floor; CI runs 3.12 and 3.15 on Linux and macOS.

## Git and PRs
- `main` takes no direct pushes. One task per branch, worktree and PR.
- Name the branch `type/short-description` before the first push: type is one of `feat`, `fix`,
  `docs`, `ci`, `refactor`, `test`, `chore`; lowercase, hyphens, no agent or tool prefix. CI
  fails other names, and the type sets the PR label. Renaming the branch after the PR is open
  closes or orphans the PR.
- Commits and PR descriptions carry no AI attribution: no `Co-Authored-By` trailer, footer or
  session link.
- The PR description says what changed, why, and how it was verified.
- Each PR gets its own code review before merge; one review of a chain of PRs doesn't count.
- Merge only with a green `ci` check, the branch up to date with `main`, by rebase (linear
  history, no merge commits).

## Releases
- Every merge to `main` is built and smoke-tested as dev build `X.Y.Z.devN` but not published;
  install it with `uv tool install git+https://github.com/sla1k/smart-gym-mcp@main` (or `@<sha>`).
- Stable release: push tag `vX.Y.Z` on a `main` commit, then approve the `release` environment.
  The version comes from the tag (hatch-vcs); never edit it by hand.
