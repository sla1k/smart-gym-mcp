# SmartGym MCP — Design Index

Python + FastMCP · stdio · single-user local server that edits SmartGym routines through SmartGym's own server API.
Notion stays on its own MCP; a thin `smartgym-sync` skill orchestrates both and holds personal config.

The technical design is split into specs by risk profile:

| Spec | Scope | Risk | Status |
|---|---|---|---|
| [`specs/00-foundation.md`](specs/00-foundation.md) | Shared: stack, DB facts, WAL/PK rules, epoch, config, layout | — | historical (DB era); the local DB is no longer used |
| [`specs/01-read-data.md`](specs/01-read-data.md) | Read tools + catalog resources | — | superseded by the API client (reads come from `history/all`) |
| [`specs/02-write-and-sync.md`](specs/02-write-and-sync.md) | Write tools + sync model (DB era) | — | superseded by the API client (DB write path removed) |
| [`specs/03-create-program.md`](specs/03-create-program.md) | Full program creation via the DB (DB era) | — | superseded by the API client (DB write path removed) |
| [`docs/superpowers/specs/2026-10-05-api-client-design.md`](docs/superpowers/specs/2026-10-05-api-client-design.md) | API client: reads + writes over SmartGym's server, sections | high — Phase 0 + S7 verified | ✅ implemented (Plan 2) |

Backlog and deferred work: [`FEATURES.md`](FEATURES.md).

## One-paragraph summary
The MCP is a client of SmartGym's backend, like any SmartGym device. Reads come from one
`history/all` call (routines, workouts, equipment). Every write is a dry run first; applying
fetches the routine fresh, snapshots it, sends the app's own edit requests (`routine/update/`,
`routine/updateExercise/`, `routine/add/`, archive/unarchive), re-reads and verifies. Routines
have warm-up / main / cool-down sections (`listGroup` 1/0/2). The app is never quit, relaunched,
or written to; the local DB is no longer used.

## Verified facts (2026-07-10, SmartGym v7.10.1; amended 2026-10-05 for v8.0.3) — settled, don't re-derive
Each fact's full evidence lives in the linked spec; this is the canonical short list. These are
DB-era facts kept for history; the API facts (Phase 0, S7) live in the API-client spec §3/§10.

- **SmartGym 8.0.3 (verified 2026-10-05, MITM capture + iPhone checks, spec 02 Part A §8.0):**
  the launch resync sends every pending routine to **`routine/add/` only**. The server dedupes
  that endpoint by the routine's `ZUNIQUEHASHID`: a known hash → `SUCCESS` with the existing ids,
  **content discarded**. `routine/update/` is reachable only from the in-app editor's Save (an
  editor-tracked diff, never stored state). A **fresh routine hash** makes the server store the
  full current tree as a NEW routine (removed exercises travel with `dateRemoved`); the app
  remaps the local row. A launch also **re-imports recently modified server routines over local
  routine fields** (observed: unpublished note reverted, flag flipped to synced) → edits must
  not meet a running app before publishing. Archive = `routine/archive/` with
  `routinesIDs=<server id>`, sent by the app for whatever local row is archived — so a local
  tombstone carrying the old server id retires the stale copy everywhere (verified).

- **Sync is NOT iCloud/CloudKit.** SmartGym's own backend: `api.smartgymapp.com/v1.1/`. The app
  pushes every routine with `ZHASSYNCED = 0` within ~20 s of launch, then sets it to `1`. Under
  v7.10.1 write + `ZHASSYNCED=0` + relaunch was the cross-device force-push; under v8 that holds
  for NEW routines only (see above).
  ([spec 03 §A + Phase 0 results](specs/03-create-program.md))
- `ZIDENTIFIER` = **server-assigned** on push (insert a placeholder via `db.generate_identifier`).
  `ZUNIQUEHASHID` = client-generated identity (`YYMMDD`+8 digits), survives the push.
- Domain hierarchy: **Program → Routine (`ZROUTINE`) → Exercise (`ZUNIQEXERCISE`) → Set
  (`ZVALUES`)**. Template sets have `ZDATELOGGED IS NULL`; reps=`ZSECONDVALUE`,
  kg=`ZTHIRDVALUE`, `ZFIRSTVALUE=1.0`, `ZTYPE=0`. ([spec 03 §B](specs/03-create-program.md))
- `ZDATEADDED` is midnight-local; `ZPRECISEDATEADDED` is the real timestamp. CoreData epoch =
  unix − 978307200. Critical read join: `ZVALUES.ZEXERCISE → ZUNIQEXERCISE.Z_PK`.
  ([spec 00](specs/00-foundation.md))
- WAL can be a week ahead of the main file: reads use `mode=ro`, **never `immutable=1`**.
- Process probe must stay `pgrep -ix SmartGym` (exact). Substring matching hits our own
  `smartgym-mcp` process and wedges every write path (regression-tested).
- **The push response is authoritative for a synced routine's content** (spec 02 Part A,
  reconciliation observations): exercise ADDs need a `ZEXERCISESTATEQUEUE` row (`ZSTATE=1`,
  `writes.enqueue_exercise_added`) or the app deletes them ~5 s after relaunch; field edits,
  reorders, and removals need only the dirty flag; **`ZHIDDEN=1` is reverted by the push**
  (archived state is server-side, `routine/archive/` only → no archive tool *on the DB path*;
  superseded 2026-10-05: the API path's `smartgym_archive_routines` sends `routine/archive/`
  itself); routine soft-delete propagates.

## Architecture invariants
Module layering: [API-client spec §5](docs/superpowers/specs/2026-10-05-api-client-design.md).

1. Tools in `server.py` are thin — no HTTP or payload code in tool bodies.
2. Every write = fetch fresh → plan (`diff.py`) → dry run returns the plan → snapshot → send →
   re-fetch → verify (`service.py`).
3. Every edit of an existing routine goes through `diff_routine`; single-field tools are
   `builders.py` wrappers.
4. Only template sets are changed; logged history is never touched.
5. Credentials only via `api/auth.py`; never logged or returned.
6. Before trusting a new kind of server write, capture the app doing it and add a golden
   fixture (`tests/fixtures/api/`).

## Decision log (2026-07-10)
- **Write mechanism:** direct DB write + managed relaunch (primary); crafted `.gym` share file
  is the documented fallback only. Rejected: backend-API replication (token extraction,
  ToS-gray), UI automation of Import-from-Text (non-deterministic LLM parsing), Shortcuts/App
  Intents (start-only, no create actions). ([spec 03 §A/§C](specs/03-create-program.md))
- **App lifecycle:** full-auto quit/relaunch by the server (never `kill -9`).
- **Exercise matching:** deterministic fuzzy (threshold 0.85, alias map, no LLM); below
  threshold rejects the whole program with candidates.
- **Program semantics:** additive-only; omitted sets → one default 1×10 set, flagged in the plan.
- **Process rule:** verify sync behavior of any NEW mutation kind with a Phase-0-style
  observation (throwaway `ZZ-` routine + iPhone check) before trusting it. The rule has now
  paid off twice: it caught the add-exercise reconciliation deletion (fixed via
  `ZEXERCISESTATEQUEUE`) and the archive revert (tool removed) — spec 02 Part A.
- **Archive (2026-07-10) — superseded 2026-10-05 by the API path's archive tool
  (`smartgym_archive_routines` / `smartgym_unarchive_routine` → `routine/archive/` /
  `routine/unarchive/`):** implemented, live-verified self-defeating (push resets `ZHIDDEN`),
  removed from the tool surface. Rejected again: token-based `routine/archive/` call (ToS-gray).
  Users archive in-app; a `smartgym_delete_routine` (soft-delete propagates) is backlog F7.

## Decision log (2026-10-05, SmartGym 8.0.3 sync break)
- **Edits publish explicitly** (`smartgym_publish_routines`), not on every write: each publish
  leaves one stale server copy per routine, so batching edits keeps it to one tombstone.
- **Stale copy retirement = local tombstone** (`OLD — <name>`, old hash + old server id,
  synced, no exercises) archived by the user in the Mac app → the app's own `routine/archive/`.
  Direct `routine/archive/` replay with the app's session credentials was the user's first
  choice but is blocked (credential access denied by the permission system) — tombstone is the
  fallback the user chose.
- Rejected: in-app editor Save as push trigger (sends only an editor-tracked diff, and takes the
  `routine/add/` path when `hasSynced=0`); App Intents (no routine-update intent in v8.0.3).

## Decision log (2026-10-06, API client)
- **DB write path + publish/tombstone retired:** `db.py`, `lifecycle.py`, `writes.py`,
  `queries.py` and `smartgym_publish_routines` are gone (git history keeps them); every tool
  works over SmartGym's server API.
- **Credentials = user-captured 0600 file** (`~/.smartgym-mcp/credentials.json`, written by
  `scripts/capture_credentials.py`): `phrase` is unchecked by the server and never sent; the
  app's `user-agent` / `accept` / `accept-language` headers are required.
- **Sections modelled as three lists** (warm-up / main / cool-down; user choice A).
- **Routine delete stays out** — archive only.
- **Section display needs a main exercise:** the iPhone shows Warm Up / Cool Down headers (the
  Mac shows separate cards) only when the routine has at least one main exercise; the Mac app
  has no section editor (S7).
- **Return routines restructured (2026-10-06):** FB-A/B/C — Return W1/W2 split into warm-up /
  main / cool-down in place via `apply_routine` (section moves only; the core block Crunch /
  Side Plank / Bird Dog kept in warm-up, user choice); snapshots in
  `~/.smartgym-mcp/backups/`. The older duplicate "Return W2" copies (3681263, 3681262,
  3681264) were archived, keeping 3681294 / 3681296 / 3681295.
