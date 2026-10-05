# API client (server-first rebuild) — design

> Status: **design approved 2026-10-05; Phase 0 passed (S1 token lifetime open); §10 routine
> sections added 2026-10-05; S7 passed (in-place section move).** Supersedes the DB-write path of
> specs 02/03. Until then the shipped DB tools + `smartgym_publish_routines`
> (re-key + tombstone, spec 02 Part A §8.0) remain the working fallback.

## 1. Why

SmartGym 8.0.3 broke the DB-write sync model (spec 02 Part A §8.0): the app re-sends existing
routines only via `routine/add/`, which the server ignores for known routines; real updates
exist only as the in-app editor's `routine/update/` diff. The re-key + tombstone workaround
works but duplicates routines, needs a manual archive per publish, and depends on app
lifecycle tricks (quit, keep closed, relaunch).

The user chose to drop the app from the loop: the MCP becomes a client of SmartGym's own
backend (`https://api.smartgymapp.com/v1.1/`), like any other SmartGym device. The ToS risk
(unofficial API, reuse of the account session) is explicitly accepted by the user.

## 2. Goals / non-goals

Goals — from chat, each a single tool call (dry run first):
1. Create, update, archive, unarchive routines.
2. Add, remove, reorder exercises in a routine.
3. Change template reps, weights, rest time.
4. Change routine and exercise notes.
5. Whole-routine rewrites ("week 2 of FB-A") in ONE update.
6. Routine sections — warm-up / main / cool-down — read, created, moved between and reordered
   within (§10).

Usage mix (user): whole-routine rewrites and single tweaks equally often.

Non-goals: routine delete (user decision: archive only); logging workouts (F4); touching logged
sets; personal-trainer/student features; working offline.

## 3. Verified facts this design builds on (2026-10-05, app 8.0.3, MITM capture)

- Requests are `multipart/form-data` POSTs or GETs with query params; bodies carry
  `appVersion`, `authID` (user id), `requestDate` (ISO, UTC); auth via `Authorization` and
  `phrase` headers. Responses are JSON with `"code": "SUCCESS"` or an error code
  (`ROUTINE_NOT_FOUND`, `TOO_MANY_ROUTINES`, …).
- `routine/add/` — `routines=[<full routine JSON>]`; the server dedupes by routine
  `uniqueHashID`; the response maps `original_id` (client hash) → `server_id` for routines,
  exercises, sets. Removed exercises travel with `dateRemoved` and no sets.
- `routine/update/` — editor diff: `routineID`, `exercisesOrder`, `removeExercises`,
  `updateExercises` (per exercise `addedSets` / `updatedSets` / `removedSets`), plus routine
  fields; exact shape = Phase 0 S2.
- `routine/archive/` `routinesIDs=<id>[,…]`; `routine/unarchive/` `routineID=<id>` → SUCCESS.
- Reads seen: `routine/all/<authID>/?lastModified=…` (incremental, `hasMore`),
  `routine/single/<id>/`, `history/all/<authID>/` (histories, goals, equipment lists),
  `user/info/<authID>`, `options.json`.
- Routine JSON (`routine/single`) carries routine fields (`identifier`, `uniqueHashID`, `name`,
  `days`, `goal`, `note`, `dateArchived`, `dateRemoved`, …) and `exercises[]` (catalog `id`,
  `identifier`, `uniqueHashID`, `idx`, `pause`, `note`, `mode`, `listGroup`, `sets[]` with
  `firstValue` / `secondValue` (reps) / `thirdValue` (kg), `type`, `index`, `dateLogged`).
- The exercise catalog ships in the app bundle (`Exercises.json` etc.) — readable without
  running the app.

### Phase 0 findings (2026-10-05, app 8.0.3) — fixtures in `tests/fixtures/api/`

**S1 auth — passes (token lifetime still open).** `Authorization` is static (same across an
app restart). The app recomputes `phrase` for every new `requestDate`, but **the server does
not check it**: a garbage `phrase`, a stale one with a new date, and no `phrase` at all all
returned SUCCESS on `user/info/<id>`; `routine/archive/` with a garbage `phrase` returned
SUCCESS and the iPhone showed the routine archived, then active again after
`routine/unarchive/`. Requests **must carry the app's `user-agent` / `accept` /
`accept-language`** — without them the CDN answers a non-JSON HTTP 403 before the API.
Open: how long `Authorization` stays valid (re-test after ≥ 24 h).

**S2 edit wire format.** Every in-app Save sends ONE request; routine-level calls carry only
the changed fields plus `routineID`, `days`, `goal` (always re-sent). Two endpoints:

| Edit | Endpoint | Fields (besides common + `timezone`) | Fixture |
|---|---|---|---|
| rename | `routine/update/` | `name` | `update_rename` |
| days | `routine/update/` | `days` (`"2,4,6"` — comma list of weekdays; check the read format in S4) | `update_days` |
| goal | `routine/update/` | `goal` | `update_goal` |
| routine note | `routine/update/` | `note` | `update_note` |
| exercise rest | `routine/update/` | `updateExercises=[{"exerciseID":<id>,"pause":"30"}]` | `update_rest` |
| exercise note | `routine/updateExercise/` | `exercises=[{"exerciseID":<id>,"note":"…"}]` (no routine fields) | `update_exercise_note` |
| reorder | `routine/update/` | `exercisesOrder="<id>:0,<id>:1,…"` | `update_reorder` |
| add exercise | `routine/update/` | `exercises=[<full exercise JSON as in routine/add, with routineID, idx/index, sets[]>]`; response maps `original_id` → `server_id` for the exercise and its sets | `update_add_exercise` |
| remove exercise | `routine/update/` | `removeExercises=<id>` | `update_remove_exercise` |
| add + remove in one save | `routine/update/` | both of the above | `update_add_and_remove_exercise` |
| add set | `routine/updateExercise/` | `exercises=[{"exerciseID":<id>,"addedSets":[{routineID,index,uniqueHashID,firstValue,secondValue,thirdValue,type,uniqueExerciseID}]}]` | `update_add_set` |
| change set | `routine/updateExercise/` | `…"updatedSets":[{identifier,index,uniqueHashID,firstValue,secondValue,thirdValue,type,dateAdded}]` | `update_change_set` |
| remove set | `routine/updateExercise/` | `…"removedSets":[{identifier, …full set…}]` | `update_remove_set` |

**S3 round trip — passes.** Hand-built `routine/update/` (routine note) and
`routine/updateExercise/` (`updatedSets`: reps/weight of one set), both sent with NO
`phrase` header → SUCCESS; iPhone and Mac both showed the new note and set.

**S5 Mac coexistence — passes.** The idle Mac app picked the S3 changes up via
`history/all/<id>/?lastModified=…&lastModifiedRoutineDate=…` (incremental) and sent nothing
back (no `routine/add` / `routine/update`).

**S4 read path (partial).** `history/all/<id>/` with `lastModified` /
`lastModifiedRoutineDate` returns `{code, lastModified, hasMore, routines[], histories[],
equipmentLists[], goal}`; routines carry the full tree (`identifier`, `uniqueHashID`, `name`,
`days` as `"2,4,6"`, `note`, `dateArchived`, `dateRemoved`, `exercises[]` with `id`, `idx`,
`pause`, `note`, `dateRemoved`, `superset`, `sets[]` with `dateLogged`, `dateRemoved`,
`secondValue`, `thirdValue`, `serverID`). Fixture `history_all_incremental`.

Notes: exercise ids in these calls are server exercise identifiers. The app's own
`routine/add/` also sends local placeholder `identifier`s and `routineID` on exercises;
S6 checks whether the server needs them.

## 4. Phase 0 spike (gate — nothing below is built before it passes)

Throwaway; `ZZ-` routines only; mitmproxy local capture as on 2026-10-05; iPhone checks by the
user. Findings are recorded back into §3 as verified facts.

| # | Question | Method | Pass |
|---|---|---|---|
| S1 | Auth: is `phrase` static or per-request? Token lifetime / refresh? | The **user** captures headers across requests and an app restart (credential handling stays with the user; Claude sees only static / changing / expired verdicts) | Replayed headers accepted later, ideally after an app restart |
| S2 | `routine/update/` payload shape | In-app edits on a `ZZ-` routine while capturing: rename, days, goal, note, rest, exercise note, reorder, add/remove exercise, add/update/remove set | Every field in §2 has a documented shape |
| S3 | Update round-trip | One hand-built `routine/update/` for a `ZZ-` routine; check `routine/single/` + iPhone | Server + iPhone match intent |
| S4 | Read coverage | Capture `routine/all`, `routine/single`, `history/all`, and the calls behind opening an exercise's history | Everything the current read tools expose is available, esp. per-set logged history; gaps listed |
| S5 | Coexistence with the Mac app | After an API change, a running Mac app with no local edits refreshes | Mac shows the change; nothing pushed back |
| S6 | Minimal create payload | Hand-built `routine/add/` for `ZZ-S6` in the shape `payloads.new_routine_payload` produces (no `identifier`/`routineID`, `requiresBands` from equipment 38, `isSingleWeight` 0) | `SUCCESS` with server ids; iPhone shows the routine and sets correctly |
| S7 | Section moves (§10) | In-app on a `ZZ-` routine while capturing: main → warm-up, warm-up → cool-down, reorder inside warm-up, add a new exercise straight into cool-down | Wire shape of a section change documented + fixtures; else fallback §10.4 |

**Outcome (2026-10-05):**

| # | Result | Notes |
|---|---|---|
| S1 | **pass** (lifetime open) | `Authorization` static across restarts; server ignores `phrase` (garbage / stale / absent all SUCCESS, reads and writes); app `user-agent`/`accept`/`accept-language` required (CDN 403 without). Re-test after ≥ 24 h. |
| S2 | **pass** | 12 edits captured; two endpoints (`routine/update/`, `routine/updateExercise/`); shapes + fixtures in §3. |
| S3 | **pass** | Hand-built note + set update, no `phrase`; iPhone and Mac match. |
| S4 | **pass, no gaps** | Full `history/all/<id>/` returns routines, histories (workout duration/calories/HR + logged sets), equipment lists, custom exercises; exercise/workout history screens make no other calls. Paging via `hasMore` + `lastModified`. |
| S5 | **pass** | Idle Mac app refreshed via incremental `history/all`, pushed nothing. |
| S6 | **pass** | `payloads.new_routine_payload` shape (no `identifier`/`routineID`) → SUCCESS; iPhone and Mac show `ZZ-S6` correctly. |
| S7 | **pass** (in-place move) | Mac app cannot edit sections → hand-built probes on `ZZ-S7`: move to warm-up / cool-down via `listGroup` in `updateExercises`, reorder, add mid-routine and at end, set/clear note, remove two; each read back via `routine/single`; iPhone shows Warm Up / Cool Down headers (needs ≥ 1 main exercise). Details §10.3. |

**Decision: build** (API-only, no local-DB read fallback). Open before Plan 2 is final:
`Authorization` lifetime, and credential sourcing (user-provided 0600 file vs reading the
app's storage with an explicit user grant).

Exit: S1 + S3 pass → build. S4 gaps → those reads stay on the local DB (read-only, existing
`db.py` / `queries.py`). S1 fails → stop and revisit; the re-key fallback stays.
Credential sourcing (user-provided 0600 file vs reading app storage with an explicit user
grant) is decided from S1's result.

## 5. Architecture

```
server.py        thin MCP tools (no HTTP or payload code in tool bodies)
api/client.py    HTTP: base URL, multipart, common params, auth headers, error mapping,
                 read retries; never logs header values
api/auth.py      credential provider (shape decided after S1)
model.py         Routine → Exercise → Set (pydantic), parsed from server JSON
payloads.py      Routine → routine/add/ JSON; ChangeSet → routine/update/ form
diff.py          (current Routine, desired Routine) → ChangeSet — pure, no I/O
matching.py      ExerciseCatalog.resolve (kept)
catalog.py       bundle catalog reader (kept)
snapshots.py     saves the fetched routine JSON before every write
db.py/queries.py read-only, ONLY for S4 gaps (otherwise removed)
```
Removed: `lifecycle.py`, `writes.py`, DB write helpers, re-key/tombstone, the publish tool.

Invariants:
1. Tools are thin: parse input → plan (pure) → apply via the client.
2. Every write = **fetch fresh → plan → (dry run returns the plan) → snapshot → send →
   re-fetch → verify** against the plan; a mismatch is an error naming the differing fields.
3. Every edit of an existing routine goes through `diff.py` → one diff per call, sent as at most
   two requests in this order: `routine/update/` (routine fields, rest, section moves, added and
   removed exercises, and `exercisesOrder` for the kept exercises whenever `idx` must be
   renumbered) and `routine/updateExercise/` (exercise notes and template sets).
4. Only template sets (`dateLogged` null) are ever changed.

## 6. Tool surface

Exercises are addressed by server `identifier` (shown by `get_routine`). Every write defaults
to `dry_run=true`.

Read: `smartgym_health` (server reachable, auth valid, installed app version vs last verified),
`smartgym_list_routines(include_archived=false)`,
`smartgym_get_routine(routine, history_depth=5)` (exercises, rest, notes, template sets,
recent sessions), `smartgym_get_workout_history(...)` (filters as today),
`smartgym_get_equipment(owned_only=true)`, catalog resources (as today).

Write:

| Tool | Server call |
|---|---|
| `smartgym_create_program(routines[])` — each routine: `warmup?`, `exercises` (main), `cooldown?` | `routine/add/` (new hashes; fuzzy matching, all-or-nothing validation as today) |
| `smartgym_update_routine(routine, name?, days?, goal?, note?)` | `routine/update/` |
| `smartgym_add_exercise(routine, exercise, section="main", position?, rest_seconds?, note?, sets?)` — `position` within the section | `routine/update/` |
| `smartgym_move_exercise(exercise_id, section, position?)` — `position` within the target section, default last | `routine/update/` (S7) |
| `smartgym_remove_exercise(exercise_id)` | `routine/update/` |
| `smartgym_reorder_routine(routine, warmup?, main?, cooldown?)` — each a list of exercise ids that must equal that section's current members (reorder only; moving = `move_exercise`) | `routine/update/` |
| `smartgym_update_exercise(exercise_id, rest_seconds?, note?, sets?)` — `sets` = full template list `[{reps, weight_kg}]` | `routine/update/` |
| `smartgym_apply_routine(routine, desired)` — desired fields + three ordered lists `warmup` / `main` / `cooldown` (existing by id, new by name) with rest / note / sets | one `routine/update/` |
| `smartgym_archive_routines(routines[])` / `smartgym_unarchive_routine(routine)` | `routine/archive/` / `routine/unarchive/` |

Single-field tools are thin wrappers: they build a `desired` routine from the fetched one and go
through the same `diff.py` path as `apply_routine`.

## 7. Safety and errors

- Snapshot of the fetched routine JSON to `~/.smartgym-mcp/backups/<ts>/routine-<id>.json`
  before every write; restore = `apply_routine` from the snapshot.
- Re-plan on apply (fresh fetch) — no blind overwrite of edits made elsewhere since the dry run.
- Missing / expired auth → actionable message, nothing sent. Server error code → passed
  through. Network: reads retry (3×, backoff); writes are never auto-resent — on uncertainty,
  re-fetch and report whether the change landed. Unresolved exercise → rejected with candidates.
- Credentials are never logged and never appear in tool output or exceptions.
- Known limit: concurrent in-app editing of the same routine → last save wins.

## 8. Testing

Automated (never touches the network or the account): `diff.py` cases (reorder, swap,
set add/remove/update, notes, no-op, section move / reorder within a section / add into a
section); payload golden tests against scrubbed Phase 0 captures;
model parsing from recorded responses; client tests on `httpx.MockTransport` (auth errors,
server codes, no write re-send, no credential leakage).

Live (manual, `ZZ-` routines, iPhone check): a scripted pass over every tool — create →
fields → add/remove/reorder → sections (add into warm-up / cool-down, move, reorder within)
→ reps/weights/rest → notes → `apply_routine` rewrite → archive → unarchive. Run after implementation and after every SmartGym update; `smartgym_health` warns
when the installed app version differs from the last verified one.

## 9. Migration

1. Phase 0 spike → update §3/§4 with verified facts; pick credential sourcing.
2. Build the API client + read tools; compare their output with the DB read tools on real data.
3. Build the write tools; live E2E on `ZZ-` routines.
4. Retire the DB write path, lifecycle, publish/tombstone; update DESIGN.md, README, FEATURES.

## 10. Routine sections (warm-up / main / cool-down)

### 10.1 Verified facts (2026-10-05, local DB read-only + captures)

- Each routine exercise carries `listGroup` (DB `ZUNIQEXERCISE.ZLISTGROUP`, API `listGroup`):
  **1 = warm-up, 0 = main, 2 = cool-down.** The user's hand-made FB-A/B/C routines use all
  three (e.g. FB-A: 5 warm-up, 10 main, 5 cool-down).
- `idx` / `ZINDEX` runs **globally** through the sections in display order: warm-up 0–4, main
  5–14, cool-down 15–19.
- Every routine created by the MCP so far (FB-A/B/C — Return W1/W2, 21–23 exercises each) has
  all exercises in `listGroup` 0: the old create path copied the most common `ZLISTGROUP`
  instead of treating it as a section, and Plan 1's `payloads.py` hardcodes `listGroup: 0`.
- `routine/add/` and the "add exercise" form of `routine/update/` carry the full exercise JSON
  including `listGroup` (S2/S6 fixtures). Moving an EXISTING exercise between sections is an
  in-place `listGroup` change (S7, §10.3).

### 10.2 Model and interface (user choice: three lists, "A")

- `RoutineExercise.section: Literal["warmup", "main", "cooldown"]`, mapped from `listGroup`;
  any other value → `ApiPayloadError` naming the value (never guessed).
  `Routine.active_exercises()` orders by (section rank warm-up < main < cool-down, `idx`), so a
  routine with inconsistent indexes still reads in section order.
- `DesiredRoutine` takes `warmup`, `main`, `cooldown` (each `list[DesiredExercise] | None`;
  `None` = keep that section's current members and order), replacing Plan 1's single
  `exercises` list. A given list defines that section completely. An existing exercise listed
  in a section other than its current one is a **move** (it leaves its old section even when
  that section is kept). An existing exercise that is in no given list and whose current
  section is not kept is **removed**. Listing the same exercise twice is an error.
- `create_program` routines take `warmup` / `exercises` (main) / `cooldown`; warm-up and
  cool-down are optional, so existing call shapes keep working.
- `get_routine` returns `warmup` / `main` / `cooldown` lists (each exercise with id, name,
  rest, note, template sets, recent sessions).

### 10.3 Change calculation and wire

- `ChangeSet` records a section move as a per-exercise `FieldChange("section", old, new)`.
  `final_order` is the global order warm-up → main → cool-down. It is sent as
  `exercisesOrder` (kept exercises, final global index) inside the same `routine/update/`,
  with new exercises carrying their `idx` in their JSON, whenever the kept exercises' relative
  order changes, an exercise is added anywhere but after the last kept one, an exercise is
  added while the kept `idx` are not exactly 0..n-1, or a section move leaves the kept `idx`
  out of order. Pure removals and order-keeping moves on consistent indexes leave gaps, as the
  app itself does (S2/S7).
- Create: `listGroup` from the section, `idx`/`index` global in section order (known shape).
- Add into a section: full exercise JSON with `listGroup` (known shape).
- Move (S7, verified 2026-10-05, iPhone + Mac): **in place** —
  `updateExercises=[{"listGroup":"<1|0|2>","exerciseID":<id>}]` (string `listGroup`, int id;
  merges with `pause` in the same entry), plus `exercisesOrder` only when the global order
  changes. Fixtures `update_move_to_warmup`, `update_move_to_cooldown`. §10.4 is not needed.
- Reorder inside a section: `exercisesOrder` alone (`update_reorder_warmup`).
- Add into a section mid-routine: the new exercise's JSON carries its `listGroup` and global
  `idx`; the app sends `exercisesOrder` for the kept exercises in the **same** request
  (captured app add, `update_add_to_warmup`, `update_add_main_before_cooldown`). A separate
  follow-up order request with the new server id is also accepted. Appending after
  contiguous `idx` 0..n-1 needs no order (`update_add_to_cooldown`).
- `removeExercises` takes a comma list (`"id1,id2"`, `update_remove_two`); kept `idx` are not
  renumbered (gaps stay, as the app leaves them). Removed exercises disappear from
  `routine/single`.
- Clearing an exercise note: `routine/updateExercise/` with `"note": ""` (server stores `""`;
  `update_set_note`, `update_clear_note`).
- Display: the iPhone shows collapsible "Warm Up" / "Cool Down" headers and the Mac separate
  cards **only when the routine has at least one main exercise**; with no main exercise both
  show one flat list (data unchanged). Smart Trainer routines additionally carry a
  `smartTrainerWorkout` block — not required for sections.
- Probe method: the Mac app has no section editor, so S7 used hand-built requests
  (`scripts/spike/send.py --save=`) on `ZZ-S7`, read back with `routine/single` after each.

### 10.4 Fallback if S7 shows no in-place move

A move becomes remove + re-add in the target section (same catalog exercise, rest, note,
template sets). Cost: the re-added routine exercise gets a new identifier, so that routine's
per-exercise "recent sessions" restart for it; workout history itself is untouched. The dry
run states this per moved exercise.

### 10.5 Fixing the existing "Return" routines

No special tool: each of the 6 routines is rewritten in place with `apply_routine`. Claude
proposes the three lists using the hand-made FB-A/B/C routines as the reference (same warm-up
drills and stretches) plus the catalog `stretch` flag; the dry run shows the split; the user
approves each routine; the user checks it on the iPhone. One routine at a time, after the
live `ZZ-` pass of §8 has covered every section operation.

