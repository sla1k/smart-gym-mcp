# SmartGym MCP — Design Index

Python + FastMCP · stdio · single-user local server that wraps SmartGym's local Core Data SQLite DB.
Notion stays on its own MCP; a thin `smartgym-sync` skill orchestrates both and holds personal config.

The technical design is split into specs by risk profile:

| Spec | Scope | Risk | Status |
|---|---|---|---|
| [`specs/00-foundation.md`](specs/00-foundation.md) | Shared: stack, DB facts, WAL/PK rules, epoch, config, layout | — read first | ✅ implemented |
| [`specs/01-read-data.md`](specs/01-read-data.md) | Read tools + catalog resources | low — no mutation, safe while app open | ✅ implemented (evals pending) |
| [`specs/02-write-and-sync.md`](specs/02-write-and-sync.md) | Write tools + sync model | high — guarded, managed app lifecycle | ✅ implemented + verified E2E (2026-07-10): add/update/reorder/remove/update-routine; archive verified impossible via DB write and removed (see spec 02 Part A) |
| [`specs/03-create-program.md`](specs/03-create-program.md) | Full program creation + verified sync-push (supersedes 02's `create_routine`) | high — Phase 0 spike passed | ✅ implemented + verified E2E (2026-07-10) |

Backlog and deferred work: [`FEATURES.md`](FEATURES.md).

## One-paragraph summary
The current `smartgym-sync` skill carries fragile knowledge (a non-obvious join, CoreData epoch
math, WAL handling, PK allocation) that the model can get wrong. This MCP bakes that into
deterministic tools. **Reads** are WAL-aware and safe to run live. **Writes** are backup-first +
`dry_run`, never run under the live app (the server gracefully quits and relaunches SmartGym
itself), and set `ZHASSYNCED=0` so the app pushes the change to SmartGym's backend on relaunch —
**verified end-to-end** (spec 03: create program → iPhone in ~20 s). Program creation AND the
spec 02 edit tools (add/update/reorder/remove exercise, update routine) are implemented and
live-verified; archiving is in-app only (verified impossible via DB write, spec 02 Part A).

## Verified facts (2026-07-10, SmartGym v7.10.1) — settled, don't re-derive
Each fact's full evidence lives in the linked spec; this is the canonical short list.

- **Sync is NOT iCloud/CloudKit.** SmartGym's own backend: `api.smartgymapp.com/v1.1/`. The app
  pushes every routine with `ZHASSYNCED = 0` within ~20 s of launch, then sets it to `1`. So
  write + `ZHASSYNCED=0` + relaunch **is** the cross-device force-push.
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
  (archived state is server-side, `routine/archive/` only → no archive tool); routine
  soft-delete propagates.

## Architecture invariants
Module layering + per-tool composition table: [spec 02 Part E](specs/02-write-and-sync.md).

1. Tools in `server.py` are THIN — no SQL, no lifecycle code in tool bodies.
2. Every mutation runs inside ONE `lifecycle.managed_write(cfg)` session
   (graceful quit → backup → RW transaction → relaunch; the relaunch fires the sync push).
3. **Every mutation of an existing routine calls `writes.mark_routine_pending`** — without it
   the change never leaves the Mac.
4. Each write tool = plan fn (RO connection, serves `dry_run=true` default) + apply fn (RW,
   revalidates — TOCTOU-safe). All-or-nothing validation, actionable errors.
5. Compose the granular layer (`insert_routine/insert_exercise/insert_set`,
   `ExerciseCatalog.resolve`, `resolve_routine`, `db.next_pk`) — never grow god-methods.
6. Soft-delete only (`ZDATEREMOVED = now`); never SQL `DELETE`. Creates are additive-only.

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
- **Archive (2026-07-10):** implemented, live-verified self-defeating (push resets `ZHIDDEN`),
  removed from the tool surface. Rejected again: token-based `routine/archive/` call (ToS-gray).
  Users archive in-app; a `smartgym_delete_routine` (soft-delete propagates) is backlog F7.
