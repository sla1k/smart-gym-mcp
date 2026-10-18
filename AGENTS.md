# AGENTS.md

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
- `main` takes no direct pushes: every change lands through a PR that is up to date with `main`
  and has a green `ci` check, merged by rebase (linear history, no merge commits).

## Branches and PRs
- One task per branch, worktree and PR.
- Before the first push, name the branch `type/short-description`: type is one of `feat`, `fix`,
  `docs`, `ci`, `refactor`, `test`, `chore`; lowercase, words joined by hyphens; no agent or tool
  prefix. CI fails other names.
- Name the branch before opening the PR; renaming it afterwards, locally or on GitHub, closes or
  orphans the PR.
- Before pushing, run `uv run ruff check src tests`, `uv run ruff format --check src tests`,
  `uv run mypy src` and `uv run pytest`.
- The PR description says what changed, why, and how it was verified.
- Update the branch from `main` before merging; rebase merge only.

## Releases
- Every merge to `main` is built and smoke-tested as dev build `X.Y.Z.devN`, but not published
  (`.github/workflows/release.yml`); install it from `@main` or `@<sha>`.
- A stable release = push tag `vX.Y.Z` on a `main` commit, then approve the `release`
  environment. The version comes from the tag (hatch-vcs); never edit it by hand.
- Release notes are grouped by the PR label the branch type sets (`.github/labeler.yml`).

## Commands
```sh
uv run pytest
uv run ruff check src tests && uv run ruff format src tests
uv run mypy src
uv run smartgym-mcp            # stdio server
uv tool install git+https://github.com/sla1k/smart-gym-mcp@<tag>   # install a release
npx @modelcontextprotocol/inspector uv run smartgym-mcp   # MCP Inspector
```
