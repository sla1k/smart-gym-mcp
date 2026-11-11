# AGENTS.md

## Before you start
- Read `DESIGN.md`. Don't re-derive or re-litigate its decisions or the verified facts in the
  [API-client spec](docs/specs/2026-10-05-api-client-design.md) (§3, §4, §10, §11).
- Put new code in the module the spec assigns it (§5).
- Record work you don't build in `FEATURES.md`.

## Code
- Keep tools in `server.py` thin: no HTTP or payload code in tool bodies.
- Send every write through `service.RoutineService`.
- Change an existing routine only through `diff_routine`.
- Before relying on a new kind of server write, capture the app doing it and save a golden
  fixture in `tests/fixtures/api/` (how: spec §9). Don't guess the wire shape.

## Live account
- Never touch the user's live SmartGym account unless asked. Live checks use only `ZZ-`prefixed
  throwaway routines, with the user confirming on the iPhone (spec §9).

## Tests
- Never hit the network or the account in tests: use `tests/fixtures/api/` and
  `tests/fakes.FakeClient`.
- Test a new or changed tool through `mcp.Client(server.mcp, raise_exceptions=True)` and assert
  on `is_error` and `structured_content`.
- After changing a tool's signature, docstring or return model, review the diff and run
  `UPDATE_TOOL_SURFACE=1 uv run pytest -k tool_surface`.
- Try a change end to end offline with the `run-smart-gym-mcp` skill:
  `uv run -q python .claude/skills/run-smart-gym-mcp/driver.py smoke`.

## Before every push
```sh
uv run ruff check src tests scripts/smoke_stdio.py
uv run ruff format --check src tests scripts/smoke_stdio.py   # drop --check to fix
uv run mypy
uv run pytest
uv build
```

## Git and PRs
- Never push to `main`. One task per branch and PR.
- Name the branch `type/short-description` before the first push; type is `feat`, `fix`,
  `docs`, `ci`, `refactor`, `test` or `chore`. Never rename it once the PR is open.
- Add no AI attribution to commits or PRs: no `Co-Authored-By`, footer or session link.
- Write in the PR description what changed, why, and how you verified it.
- Get a separate code review of each PR before merging it.
- Merge only with a green `ci` check and the branch up to date with `main`, by rebase merge.

## Releases
- Never edit the version by hand; it comes from the git tag.
- To release, push tag `vX.Y.Z` on a `main` commit, then approve the `release` environment.
