# SmartGym MCP — Design

Python + MCP SDK 2.x (`MCPServer`) · stdio · single-user local server that edits SmartGym
routines through SmartGym's own server API. Notion stays on its own MCP; a thin
`smartgym-sync` skill orchestrates both and holds personal config.

The MCP is a client of SmartGym's backend, like any SmartGym device. Reads come from one
`history/all` call (routines, workouts, equipment). Every write is a dry run first; applying
fetches the routine fresh, snapshots it, sends the app's own edit requests, re-reads and
verifies. Routines have warm-up / main / cool-down sections. The app is never quit,
relaunched or written to.

## Where things live

Each fact lives in one place:

| Doc | Holds |
|---|---|
| `DESIGN.md` (this file) | status, architecture invariants, decisions |
| [API-client spec](docs/specs/2026-10-05-api-client-design.md) | wire protocol and verified server behaviour (§3, §4, §10, §11), modules (§5), tool surface (§6), safety and errors (§7), tests, live checks and captures (§8, §9) |
| [`FEATURES.md`](FEATURES.md) | backlog: what is not built yet |
| [`AGENTS.md`](AGENTS.md) | working rules, branches, releases, commands |

## Status

Verified against SmartGym **8.0.3** (`config.VERIFIED_APP_VERSION`).

| Area | Status |
|---|---|
| Reads: health, routines, routine detail with recent sessions, workout history, equipment, catalog resources | ✅ implemented |
| Writes: create program, update routine, add / move / remove / reorder / update exercise, apply routine, archive / unarchive | ✅ implemented; live pass 2026-10-05 (spec §9) |
| Sections (warm-up / main / cool-down) | ✅ implemented (spec §10) |
| Custom exercises in routines (F8) | ✅ implemented; live pass 2026-10-18 (spec §11) |
| Custom exercise definitions, analytics, set logging, … | backlog ([`FEATURES.md`](FEATURES.md)) |

## Verified facts — settled, don't re-derive or re-litigate

They live in the API-client spec, each with its evidence and fixture:
§3 wire protocol · §4 Phase 0 results S1–S7 (auth, edit wire format, round trip, read
coverage, Mac coexistence, minimal create) and later findings (null workouts, unset days,
empty goal) · §10.1 / §10.3 sections and in-place moves · §11 custom exercises.

## Architecture invariants

Modules and their layering: spec §5.

1. Tools in `server.py` are thin — no HTTP or payload code in tool bodies.
2. Every write goes through `service.RoutineService`: fetch fresh → plan (`diff.py`) → dry run
   returns the plan → snapshot of the routine before an edit → send each request once, never
   re-sent → re-fetch → verify against the plan. Dry-run default: spec §6; snapshot path and
   error handling: spec §7.
3. Every edit of an existing routine goes through `diff_routine`, one `ChangeSet` per call;
   single-field tools are `builders.py` wrappers.
4. Only template sets are changed; logged history is never touched.
5. Credentials only via `api/auth.py` (the file) and `api/app_session.py` (the app's session,
   read only by `smartgym-mcp auth`); never logged or returned.
6. Before trusting a new kind of server write, capture the app doing it and add a golden
   fixture (`tests/fixtures/api/`; how: spec §9).
7. Tool calls run one at a time (`runtime.AppContext.run`: async tools take one `anyio.Lock`, then run
   their blocking work on a worker thread): the account cache (`account.AccountCache`,
   lifetime: spec §5), the API client's lifetime and each write's re-read → rewrite assume
   calls don't overlap. A call that can't start in time, is cancelled while waiting, or
   arrives once shutdown began sends nothing; a call cancelled after its thread started runs
   to the end, holding the lock until it has. Shutdown closes the client only after the call
   in flight. Input checks that need no network run before the wait.

## Decisions

- **The server API is the only backend (2026-10-05):** the MCP talks to
  `api.smartgymapp.com/v1.1/` like any SmartGym device and reads no local app data besides the
  bundle's catalog files and version. Why: SmartGym 8.0.3's launch resync sends local routines
  only via `routine/add/`, which the server dedupes by `uniqueHashID` — a known routine gets
  SUCCESS with its existing ids and its content is dropped — so a local DB write + relaunch
  can't update a routine (the DB path was retired 2026-10-06). Rejected: Shortcuts / App
  Intents (start-only, no create actions; no routine-update intent in 8.0.3), UI automation of
  Import-from-Text (non-deterministic LLM parsing), a crafted `.gym` share file (only ever the
  DB path's fallback). The ToS risk (unofficial API, reuse of the account session) is
  explicitly accepted by the user.
- **Credentials = a 0600 file the user creates with `smartgym-mcp auth`**
  (`~/.smartgym-mcp/credentials.json`; 2026-11-09, user choice). The command reads the app's
  session from its URL cache once, checks it and saves it; the server only reads the file, so
  the MCP client never needs access to the app's data. mitmproxy is only for capturing the
  app's traffic (spec §9), not for credentials. What the file holds and sends:
  spec §4 S1. An `AuthError` drops the client, so a re-captured
  session takes effect on the next call without restarting the server.
- **Sections are three lists** (warm-up / main / cool-down; user choice "A", spec §10.2).
- **Archive, never delete (2026-10-05, formerly backlog F7):** archive / unarchive send
  `routine/archive/` / `routine/unarchive/`. The app's `routine/delete/` was seen in the
  2026-10-05 capture but is deliberately not exposed (no fixture kept).
- **Goal is required on create and can't be cleared on edit (2026-10-18):** the app's editor
  can't save a routine without one (spec §4). `RoutineSpec.goal` is a non-blank string;
  `smartgym_update_routine` / `smartgym_apply_routine` reject `goal=""` (shared `inputs.Goal`
  validator). Changing a goal still works (user choice).
- **Exercise matching is deterministic:** numeric id, exact name, then token-set fuzzy match
  (threshold 0.85, alias map, no LLM). Below threshold rejects the whole change with
  candidates; a name shared by several exercises: spec §11.
- **Program semantics:** create is additive-only (a name must not match an active routine);
  omitted sets → one default 1×10 bodyweight set, flagged in the plan.
- **Custom exercises (F8, 2026-10-18):** the account's custom exercises resolve by name with
  the bundle catalog and are sent in the app's captured shape (spec §11). Managing the
  definitions (create / edit / delete) is split off as F9 (user choice).
- **Releases are tags + GitHub Releases, no signed binary (2026-10-19):** the PyInstaller
  binary was signed only to keep the macOS TCC grant for the app's container DB, which nothing
  reads any more; a `uv tool install` from a tag needs no signing. How to release and install:
  AGENTS.md "Releases".
- **Only stable tags become Releases:** releases are immutable (they can't be replaced, only
  added), so a dev release per merge would flood the page; dev builds of `main` stay
  unpublished.
- **No rc tags:** dev builds of `main` are the testing period before a stable tag.
- **`mcp` is pinned to the tested 2.x API** in `pyproject.toml`, since installs don't read
  `uv.lock`.
