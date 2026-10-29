# API client — design

> Status: **implemented**; wire format verified against SmartGym 8.0.3 (Phase 0, 2026-10-05;
> custom exercises 2026-10-18). This doc holds how the MCP talks to the SmartGym server: the
> wire protocol, the verified server behaviour, the modules and the tool surface. The
> project status, the architecture invariants and the decisions live in
> [`DESIGN.md`](../../../DESIGN.md); the backlog in [`FEATURES.md`](../../../FEATURES.md).

## 1. Scope

The MCP is a client of SmartGym's own backend (`https://api.smartgymapp.com/v1.1/`), like any
SmartGym device (DESIGN: decisions). It reads the account from `history/all` and edits
routines with the requests the app itself sends.

## 2. Goals and non-goals

Goals — from chat, each a single tool call (dry run first):
1. Create, update, archive, unarchive routines.
2. Add, remove, reorder exercises in a routine.
3. Change template reps, weights, rest time.
4. Change routine and exercise notes.
5. Whole-routine rewrites ("week 2 of FB-A") in ONE update.
6. Routine sections — warm-up / main / cool-down — read, created, moved between and reordered
   within (§10).
7. The account's custom exercises usable in routines (§11).

Non-goals: routine delete (archive only, DESIGN: decisions); logging workouts (F4); touching
logged sets; personal-trainer/student features; working offline.

## 3. Wire protocol

- Requests are `multipart/form-data` POSTs or GETs with query params; every one carries
  `appVersion`, `authID` (user id), `requestDate` (ISO, UTC), added by `api/client.py`. Auth:
  the `Authorization` header plus the app's own headers (§4 S1). Responses are JSON with
  `"code": "SUCCESS"` or an error code (`ROUTINE_NOT_FOUND`, `TOO_MANY_ROUTINES`, …).
- `routine/add/` — `routines=[<full routine JSON>]`; the server dedupes by the routine's
  `uniqueHashID`; the response maps `original_id` (client hash) → `server_id` for routines,
  exercises, sets. New objects carry only a client `uniqueHashID`; the server assigns the
  identifiers (§4 S6).
- `routine/update/` and `routine/updateExercise/` — the app's edit requests (§4 S2).
- `routine/archive/` `routinesIDs=<id>[,…]`; `routine/unarchive/` `routineID=<id>` → SUCCESS.
- Reads: `history/all/<authID>/` (every routine, workout and equipment list, §4 S4),
  `exercise/allCustomExercises/` (§11), `routine/single/<id>/` (one routine; its `routines`
  list is empty when the id names none).
- Routine JSON carries routine fields (`identifier`, `uniqueHashID`, `name`, `days`, `goal`,
  `note`, `dateArchived`, `dateRemoved`, …) and `exercises[]` (catalog `id`, `identifier`,
  `uniqueHashID`, `idx`, `pause`, `note`, `mode`, `listGroup`, `dateRemoved`, `superset`,
  `sets[]` with `firstValue` / `secondValue` (reps) / `thirdValue` (kg), `type`, `index`,
  `dateLogged`, `dateRemoved`). Every scalar arrives as a string, and template, logged and
  removed sets share one `sets[]`; `model.py` coerces and filters them once.
- The exercise catalog ships in the app bundle (`Exercises.json`, `Equipments.json`,
  `Categories.json`) — readable without running the app.

## 4. Verified server behaviour (Phase 0, 2026-10-05, app 8.0.3)

Throwaway `ZZ-` routines, mitmproxy capture (§9), iPhone checks by the user. Fixtures in
`tests/fixtures/api/`.

**S1 auth.** `Authorization` is static (same across an app restart) and valid ≥ 48 h (captured
2026-10-05, still SUCCESS on 2026-10-07; no expiry observed). The app recomputes `phrase` for
every `requestDate`, but **the server does not check it**: garbage, stale or absent `phrase`
all returned SUCCESS on reads and writes, so it is never sent. Requests **must carry the app's
`user-agent` / `accept` / `accept-language`** — without them the CDN answers a non-JSON HTTP
403 before the API. On expiry the tools raise `AuthError` → re-run
`scripts/capture_credentials.py`.

**S2 edit wire format.** Every in-app Save sends ONE request; routine-level calls carry only
the changed fields plus `routineID`, `days`, `goal` (always re-sent). Exercise ids in these
calls are server routine-exercise identifiers.

| Edit | Endpoint | Fields (besides common + `timezone`) | Fixture |
|---|---|---|---|
| rename | `routine/update/` | `name` | `update_rename` |
| days | `routine/update/` | `days` (comma list of weekday numbers, e.g. `"2,4,6"`) | `update_days` |
| goal | `routine/update/` | `goal` | `update_goal` |
| routine note | `routine/update/` | `note` | `update_note` |
| exercise rest | `routine/update/` | `updateExercises=[{"exerciseID":<id>,"pause":"30"}]` | `update_rest` |
| exercise note | `routine/updateExercise/` | `exercises=[{"exerciseID":<id>,"note":"…"}]` (no routine fields) | `update_exercise_note` |
| reorder | `routine/update/` | `exercisesOrder="<id>:0,<id>:1,…"` | `update_reorder` |
| add exercise | `routine/update/` | `exercises=[<full exercise JSON as in routine/add, with routineID, idx/index, sets[]>]`; response maps `original_id` → `server_id` for the exercise and its sets | `update_add_exercise` |
| remove exercise | `routine/update/` | `removeExercises=<id>` | `update_remove_exercise` |
| add + remove in one save | `routine/update/` | both of the above | — (captured, not kept: no test used it) |
| add set | `routine/updateExercise/` | `exercises=[{"exerciseID":<id>,"addedSets":[{routineID,index,uniqueHashID,firstValue,secondValue,thirdValue,type,uniqueExerciseID}]}]` | `update_add_set` |
| change set | `routine/updateExercise/` | `…"updatedSets":[{identifier,index,uniqueHashID,firstValue,secondValue,thirdValue,type,dateAdded}]` | `update_change_set` |
| remove set | `routine/updateExercise/` | `…"removedSets":[{identifier, …full set…}]` | `update_remove_set` |

**S3 round trip.** Hand-built `routine/update/` (routine note) and `routine/updateExercise/`
(`updatedSets`: reps/weight of one set), both without `phrase` → SUCCESS; iPhone and Mac both
showed the change.

**S4 read coverage — no gaps.** `history/all/<id>/` returns `{code, lastModified, hasMore,
routines[], histories[], equipmentLists[], customExercises[], goal}`: routines with the full
tree, histories (workout duration / calories / heart rate + logged sets), equipment
lists. The app's exercise and workout history screens make no other calls. Incremental reads
page with `lastModified` / `lastModifiedRoutineDate` + `hasMore`.

**S5 Mac coexistence.** An idle Mac app picked API changes up via the incremental
`history/all` and sent nothing back.

**S6 minimal create.** `routine/add/` in the shape `payloads.new_routine_payload` produces (no
`identifier` / `routineID`, `requiresBands` from equipment 38, `isSingleWeight` 0) → SUCCESS
with server ids; iPhone and Mac show the routine correctly. The app's own create also sends
local placeholder ids; the server doesn't need them.

**S7 section moves** — in place, via `listGroup` (§10.3).

**Found later (live use):**
- A history with `"workout": null` exists in a live account; such histories are skipped.
- `routine/add/` answers HTTP 500 to `"days": null`, so unset fields are omitted, as the app
  does. With no days the server stores its default `"0001"` ("Once per week"), so create
  verification skips days when none were given.
- **The app's editor can't save a routine whose goal is empty** (2026-10-18): `routine/add/`
  accepts a routine without `goal` and stores `""`; opening it in the app and tapping Save
  errors that no goal was found. After a `routine/update/` with a goal the same Save works.

## 5. Modules

Imports point one way: `server` on top; `service`, `builders` and `views` over `diff`,
`payloads`, `lookups` and `account`; those over `model`, `matching`, `catalog` and `inputs`;
`api/` and `config` at the bottom. `api/` imports neither the model nor the catalog. The
architecture invariants are in DESIGN.md.

| Module | Holds |
|---|---|
| `server.py` | MCPServer app: thin tools and catalog resources; `AppContext` builds the services on first use and runs calls one at a time |
| `builders.py` | single-field tool inputs → a `DesiredRoutine` (the same diff path as `apply_routine`) |
| `service.py` | `RoutineService`: every write (edit, create, archive) — plan, dry run, snapshot, send, re-read, verify |
| `views.py` | read-tool outputs over the parsed account (pure) |
| `diff.py` | (current `Routine`, `DesiredRoutine`) → `ChangeSet` — pure, no I/O, no wire format |
| `payloads.py` | `RoutineSpec` → `routine/add/` JSON; `ChangeSet` → the edit requests; archive forms |
| `lookups.py` | routine by ref / routine of an exercise id over the parsed account (pure) |
| `account.py` | `AccountCache`: parsed `history/all` + custom exercises for one tool call, dropped after every send (`routine/single` is never cached) |
| `model.py` | `Routine` → `RoutineExercise` → sets, workouts, equipment lists, parsed from server JSON |
| `matching.py` | `ExerciseCatalog.resolve`: deterministic exercise-name resolution |
| `catalog.py` | the app bundle's catalog files and installed version |
| `inputs.py` | tool input specs (`RoutineSpec`, `ExerciseSpec`, `SetSpec`, shared validators) |
| `config.py` | env overrides with defaults; the verified app version |
| `api/client.py` | HTTP: base URL, multipart, common params, auth headers, error mapping, read retries; never logs header values |
| `api/auth.py` | the credentials file (§4 S1) |
| `api/reads.py` | read endpoints → raw JSON |

An edit is one `ChangeSet` per call, sent as at most two requests in this order:
`routine/update/` (routine fields, rest, section moves, added and removed exercises, and
`exercisesOrder` whenever `idx` must be renumbered) and `routine/updateExercise/` (exercise
notes and template sets).

## 6. Tool surface

Exercises are addressed by server `identifier` (`exercise_id`, shown by `get_routine`).
Every write defaults to `dry_run=true`.

Read: `smartgym_health` (credentials, server reachable, installed app version vs the verified
one, `data_as_of` = the server's `lastModified` of `history/all`),
`smartgym_list_routines(include_archived=false)`,
`smartgym_get_routine(routine, history_depth=5)` (warmup / main / cooldown with rest, notes,
template sets, recent sessions), `smartgym_get_workout_history(...)` (date range, routine
filter, paging), `smartgym_get_equipment(owned_only=true)`; resources
`smartgym://catalog/exercises`, `…/equipment`, `…/categories` (the bundle files).

Write:

| Tool | Server call |
|---|---|
| `smartgym_create_program(routines[])` — each routine: `goal` (required, §4), `warmup?`, `exercises` (main), `cooldown?` | `routine/add/` (new hashes; fuzzy matching, all-or-nothing validation) |
| `smartgym_update_routine(routine, name?, days?, goal?, note?)` — `goal` can't be cleared (§4) | `routine/update/` |
| `smartgym_add_exercise(routine, exercise, section="main", position?, rest_seconds?, note?, sets?)` — `position` within the section | `routine/update/` |
| `smartgym_move_exercise(exercise_id, section, position?)` — `position` within the target section, default last | `routine/update/` (§10.3) |
| `smartgym_remove_exercise(exercise_id)` | `routine/update/` |
| `smartgym_reorder_routine(routine, warmup?, main?, cooldown?)` — each a list of exercise ids that must equal that section's current members (reorder only; moving = `move_exercise`) | `routine/update/` |
| `smartgym_update_exercise(exercise_id, rest_seconds?, note?, sets?)` — `sets` = full template list `[{reps, weight_kg}]` | `routine/update/` (rest) and `routine/updateExercise/` (note, template sets) |
| `smartgym_apply_routine(routine, name?, days?, goal?, note?, warmup?, main?, cooldown?)` — three ordered lists (existing by id, new by name) with rest / note / sets | `routine/update/` + `routine/updateExercise/` (§5) |
| `smartgym_archive_routines(routines[])` / `smartgym_unarchive_routine(routine)` | `routine/archive/` / `routine/unarchive/` |

## 7. Safety and errors

- Before every edit of an existing routine, the fetched routine JSON is saved to
  `~/.smartgym-mcp/backups/<ts>/routine-<id>.json`; restore = `apply_routine` from the
  snapshot.
- Re-plan on apply (fresh fetch) — no blind overwrite of edits made elsewhere since the dry run.
- Missing / expired auth → actionable message, nothing sent. Server error code → passed
  through. Network: reads retry (3×, backoff); writes are never auto-resent — on an unknown
  outcome the service re-reads and reports whether the change landed. A failure part-way names
  what was already sent, what failed and what was not sent. Unresolved exercise → rejected with
  candidates.
- After a send, the routine is re-read and compared with the plan's expected view; a mismatch
  is an error naming the differing fields.
- Credentials are never logged and never appear in tool output or exceptions.
- Known limit: concurrent in-app editing of the same routine → last save wins.

## 8. Automated tests

Never touch the network or the account (`tests/fakes.FakeClient`, `httpx.MockTransport`):
`diff.py` cases (reorder, swap, set add/remove/update, notes, no-op, section move / reorder
within a section / add into a section); payload golden tests against the scrubbed captures in
`tests/fixtures/api/`; model parsing from recorded responses; client tests (auth errors,
server codes, no write re-send, no credential leakage); service and server tests on the fake
client.

## 9. Live checks and captures

**Live check** (manual, `ZZ-` routines, iPhone check by the user): a pass over every tool —
create → fields → add/remove/reorder → sections (add into warm-up / cool-down, move, reorder
within) → reps/weights/rest → notes → `apply_routine` rewrite → archive → unarchive. Run after
every SmartGym update; `smartgym_health` warns when the installed app version differs from the
verified one (`config.VERIFIED_APP_VERSION`).

Last full pass: 2026-10-05, SmartGym 8.0.3, `ZZ-E2E` — every write "Applied … verified" on the
server and confirmed on the iPhone (custom exercises: §11).

**Capturing the app** (before trusting a new kind of write; run by the user, credential values
never reach the agent):

```sh
mitmdump --mode local:SmartGym -s scripts/spike/capture.py --set flow_detail=0 --set confdir=<CA dir>
```

`confdir` must hold the CA the Mac trusts, or every SmartGym TLS handshake fails and nothing is
logged. The addon appends redacted records to `scripts/spike/spike-flows.jsonl` (gitignored);
`uv run python scripts/spike/scrub.py <record-index> <fixture-name>` turns one into
`tests/fixtures/api/<fixture-name>.json` with the account id scrubbed. Hand-built probes go
through `scripts/spike/send.py` (`--save=<name>` writes a fixture). Remove the CA trust
afterwards.

## 10. Routine sections (warm-up / main / cool-down)

### 10.1 Verified facts (2026-10-05)

- Each routine exercise carries `listGroup`: **1 = warm-up, 0 = main, 2 = cool-down.**
- `idx` runs **globally** through the sections in display order (e.g. warm-up 0–4, main 5–14,
  cool-down 15–19).
- `routine/add/` and the "add exercise" form of `routine/update/` carry the full exercise JSON
  including `listGroup`. Moving an EXISTING exercise between sections is an in-place
  `listGroup` change (S7, §10.3).

### 10.2 Model and interface (three lists)

- `RoutineExercise.section: Literal["warmup", "main", "cooldown"]`, mapped from `listGroup`;
  any other value → `ApiPayloadError` naming the value (never guessed).
  `Routine.active_exercises()` orders by (section rank warm-up < main < cool-down, `idx`), so a
  routine with inconsistent indexes still reads in section order.
- `DesiredRoutine` takes `warmup`, `main`, `cooldown` (each `list[DesiredExercise] | None`;
  `None` = keep that section's current members and order). A given list defines that section
  completely. An existing exercise listed in a section other than its current one is a
  **move** (it leaves its old section even when that section is kept). An existing exercise
  that is in no given list and whose current section is not kept is **removed**. Listing the
  same exercise twice is an error.

### 10.3 Change calculation and wire

- `ChangeSet` records a section move as a per-exercise `FieldChange("section", old, new)`.
  `final_order` is the global order warm-up → main → cool-down. It is sent as
  `exercisesOrder` (kept exercises, final global index) inside the same `routine/update/`,
  with new exercises carrying their `idx` in their JSON, whenever the kept exercises' relative
  order changes, an exercise is added anywhere but after the last kept one, an exercise is
  added while the kept `idx` are not exactly 0..n-1, or a section move leaves the kept `idx`
  out of order. Pure removals and order-keeping moves on consistent indexes leave gaps, as the
  app itself does (S2/S7).
- Create: `listGroup` from the section, `idx`/`index` global in section order.
- Add into a section: full exercise JSON with `listGroup`.
- Move (S7, verified 2026-10-05, iPhone + Mac): **in place** —
  `updateExercises=[{"listGroup":"<1|0|2>","exerciseID":<id>}]` (string `listGroup`, int id;
  merges with `pause` in the same entry), plus `exercisesOrder` only when the global order
  changes. Fixtures `update_move_to_warmup`, `update_move_to_cooldown`.
- Reorder inside a section: `exercisesOrder` alone (`update_reorder_warmup`).
- Add into a section mid-routine: the new exercise's JSON carries its `listGroup` and global
  `idx`; the app sends `exercisesOrder` for the kept exercises in the **same** request
  (`update_add_to_warmup`, `update_add_main_before_cooldown`). Appending after contiguous
  `idx` 0..n-1 needs no order (`update_add_to_cooldown`).
- `removeExercises` takes a comma list (`"id1,id2"`, `update_remove_two`); kept `idx` are not
  renumbered (gaps stay, as the app leaves them). Removed exercises disappear from
  `routine/single`.
- Clearing an exercise note: `routine/updateExercise/` with `"note": ""` (server stores `""`;
  `update_set_note`, `update_clear_note`).
- Display: the iPhone shows collapsible "Warm Up" / "Cool Down" headers and the Mac separate
  cards **only when the routine has at least one main exercise**; with no main exercise both
  show one flat list (data unchanged). The Mac app has no section editor. Smart Trainer
  routines additionally carry a `smartTrainerWorkout` block — not required for sections.
- Probe method: with no section editor on the Mac, S7 used hand-built requests
  (`scripts/spike/send.py --save=`) on `ZZ-S7`, read back with `routine/single` after each.

## 11. Custom exercises in routines (F8, verified 2026-10-18, app 8.0.3)

The account's own exercises resolve by name next to the bundle catalog and go into create /
add-exercise payloads. Creating or editing the custom exercise definitions themselves is F9
([`FEATURES.md`](../../../FEATURES.md)).

| Fact | Evidence (fixture) |
|---|---|
| `exercise/allCustomExercises/` (GET, no params) lists every custom exercise: `id` (≥ 397744, beyond the bundle's ids), `name`, `type`, `category`, `subCategories`, `equipments`, `twoSides`, `isCustom: "1"`, `dateRemoved`, image fields. `history/all/`'s `customExercises[]` has the same shape but **only the ones already used in a routine** (live 2026-10-18: a new, unused one was missing), so it is not used | `all_custom_exercises`, `history_all_custom` |
| A routine exercise of a custom exercise is an ordinary routine exercise: `id` = the custom id, `isCustom: "1"`; edits address it by its routine-exercise `identifier` like any other | `history_all_custom`, `update_custom_*` |
| Create / add: the app sends `id` = `genericID` = custom id, `isCustom: 1`, and **no** `stretch`, `subCategories`, `secondImage`…`sixthImage`; `firstImage` is a local name ("deadhang1") — the server keeps the custom exercise's own empty one, so the MCP sends that | `add_routine_custom`, `update_add_custom_exercise` |
| The Mac editor sends set / note / rest edits inside `routine/update/`'s `updateExercises` (`updatedSets`, `addedSets`, `removedSets`, `note`, `pause`); the per-exercise entries equal what the MCP sends to `routine/updateExercise/` (S2/S3) | `update_custom_sets_rest`, `update_custom_change_set`, `update_custom_note` |
| Removing a custom exercise from a routine = plain `removeExercises` | — (captured, not kept: no test used it) |

Model: `CatalogExercise.is_custom`; `api.reads.custom_exercises()` fetches them,
`model.parse_custom_exercises()` parses them (removed and malformed ones skipped — a malformed
one is logged), `AccountCache.custom_exercises()` keeps them like the account read, for one tool
call. `RoutineService` reads them only when a change adds an exercise by name and extends the
bundle catalog with them, so a custom exercise made in the app is usable from the next tool
call and edits that add nothing don't depend on that read. A name shared by several exercises
(a custom one named like a built-in one, or the bundle's own duplicate "Single Arm Cable
Triceps Pushdown") is rejected with their ids, never picked silently. Custom exercises carry
no `stretch` on the server, so they are sent with `isStretch: false`, as the app does.

Live pass 2026-10-18 (`ZZ-F8-MCP` / `ZZ-F8B` / `ZZ-F8`): create with Dead Hang + Push Up, add
Dead Hang to an existing routine, change Dead Hang's sets and note — each "Applied … verified"
on the server and confirmed on the iPhone (custom image shown). A custom exercise created in
the app and used in no routine (`ZZ-F8 Fresh`, absent from `history/all/`) resolved through
`allCustomExercises` into `ZZ-F8-FRESH`; iPhone confirmed.
