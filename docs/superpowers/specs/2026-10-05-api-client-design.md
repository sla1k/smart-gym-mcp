# API client (server-first rebuild) — design

> Status: **design approved 2026-10-05, Phase 0 spike pending.** Supersedes the DB-write path of
> specs 02/03 once Phase 0 passes. Until then the shipped DB tools + `smartgym_publish_routines`
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

**Outcome (2026-10-05):**

| # | Result | Notes |
|---|---|---|
| S1 | **pass** (lifetime open) | `Authorization` static across restarts; server ignores `phrase` (garbage / stale / absent all SUCCESS, reads and writes); app `user-agent`/`accept`/`accept-language` required (CDN 403 without). Re-test after ≥ 24 h. |
| S2 | **pass** | 12 edits captured; two endpoints (`routine/update/`, `routine/updateExercise/`); shapes + fixtures in §3. |
| S3 | **pass** | Hand-built note + set update, no `phrase`; iPhone and Mac match. |
| S4 | **pass, no gaps** | Full `history/all/<id>/` returns routines, histories (workout duration/calories/HR + logged sets), equipment lists, custom exercises; exercise/workout history screens make no other calls. Paging via `hasMore` + `lastModified`. |
| S5 | **pass** | Idle Mac app refreshed via incremental `history/all`, pushed nothing. |
| S6 | **pass** | `payloads.new_routine_payload` shape (no `identifier`/`routineID`) → SUCCESS; iPhone and Mac show `ZZ-S6` correctly. |

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
3. Every edit of an existing routine goes through `diff.py` → one `routine/update/` per call.
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
| `smartgym_create_program(routines[])` | `routine/add/` (new hashes; fuzzy matching, all-or-nothing validation as today) |
| `smartgym_update_routine(routine, name?, days?, goal?, note?)` | `routine/update/` |
| `smartgym_add_exercise(routine, exercise, position?, rest_seconds?, note?, sets?)` | `routine/update/` |
| `smartgym_remove_exercise(exercise_id)` | `routine/update/` |
| `smartgym_reorder_routine(routine, exercise_ids[])` (must equal the active set) | `routine/update/` |
| `smartgym_update_exercise(exercise_id, rest_seconds?, note?, sets?)` — `sets` = full template list `[{reps, weight_kg}]` | `routine/update/` |
| `smartgym_apply_routine(routine, desired)` — desired fields + ordered exercises (existing by id, new by name) with rest / note / sets | one `routine/update/` |
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
set add/remove/update, notes, no-op); payload golden tests against scrubbed Phase 0 captures;
model parsing from recorded responses; client tests on `httpx.MockTransport` (auth errors,
server codes, no write re-send, no credential leakage).

Live (manual, `ZZ-` routines, iPhone check): a scripted pass over every tool — create →
fields → add/remove/reorder → reps/weights/rest → notes → `apply_routine` rewrite → archive →
unarchive. Run after implementation and after every SmartGym update; `smartgym_health` warns
when the installed app version differs from the last verified one.

## 9. Migration

1. Phase 0 spike → update §3/§4 with verified facts; pick credential sourcing.
2. Build the API client + read tools; compare their output with the DB read tools on real data.
3. Build the write tools; live E2E on `ZZ-` routines.
4. Retire the DB write path, lifecycle, publish/tombstone; update DESIGN.md, README, FEATURES.
