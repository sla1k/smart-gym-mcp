# SmartGym MCP — Feature Backlog

Future plans and deferred features. Triaged 2026-10-19 for the API-client architecture
(server API only, no local DB): items are ordered by priority, then readiness.

---

## F2 — Progression, PRs & volume analytics  ·  priority: high  ·  ready

Per-exercise weight-over-time, personal records (max weight, estimated 1RM), volume/tonnage trends.
Read-only.

Candidate tools: `smartgym_get_exercise_progression(exercise, range)`,
`smartgym_get_personal_records(exercise?)`, `smartgym_get_volume_trend(routine?, range)`.

Unblocked by the API client: one `history/all` call already gives the full server history
(61+ workouts vs ~30 in the old local DB). Every input is parsed today: `RoutineExercise.logged_sets`
(reps, kg, `logged_at`), `RoutineExercise.catalog_id` to group the same exercise across routines,
and `Workout.set_ids` to tie sets to sessions. Pure functions in `reads.py` over `AccountData`;
no new server call, no live risk, tests on the existing fixtures.

---

## F8 — Custom exercises  ·  priority: medium  ·  needs a capture

The exercise catalog is the app bundle's `Exercises.json`, so only built-in exercises resolve
by name. The account's own `customExercises` (returned by `history/all/`) are not parsed by
`model.parse_history_all` yet and not resolvable by name — add them to `ExerciseCatalog` (and to
the create / add-exercise payloads) once their wire shape in `routine/add/` / `routine/update/` is
captured (golden fixture first, per invariant 6).

---

## F3 — Equipment-aware next-weight suggestion  ·  priority: low

`suggest_next_weight(exercise, current_weight)` snapping to weights the user actually has.
The API's `equipmentLists` carry `dumbbellWeights` and `kettlebellWeights` (comma lists, already
parsed into `EquipmentList`) plus owned equipment ids — but no barbell plates or machine
increments, so those need a fallback (fixed increment, or a user-configured one). Builds on F2
for the "current weight" from history.

---

## F4 — Set-logging writes  ·  priority: low  ·  needs a decision + capture

Log a workout (sets with reps/weight) from the MCP — e.g. from an external source.
Not a free extension of the current design: logging is a non-goal of the API-client spec (§2),
and invariant 4 says logged history is never touched. Before building, decide to lift that
invariant for an additive-only "log workout" path, then capture the app finishing a workout
(endpoint and payload unknown — likely a `history/…` write creating a history with
`workout` + `routine.exercises[].sets`) on a `ZZ-` routine.

---

## F6 — MCPB packaging / distribution  ·  priority: low

Package as an `.mcpb` bundle for one-click install. Install today is
`uv tool install git+…@<tag>` (decision 2026-10-19), which is enough for single-user use.
One-click value is limited while credentials still come from the manual
`scripts/capture_credentials.py` step.

---

## Tech debt

- `ChangeSet.final_order` uses string keys (`"id:N"` / `"new:k"`) instead of a tagged model
  (deferred from Plan 1's final review).

---

## Closed

- **F5 — Sync staleness / iPhone-only data detection** — obsolete (2026-10-19). It guarded
  against the local DB lagging the iPhone; reads now come from the server, which is the source
  of truth. App-version drift is covered by `smartgym_health`.
- **F7 — Routine delete** — won't do (decision 2026-10-06, DESIGN.md): archive only
  (`smartgym_archive_routines`). The server's `routine/delete/` endpoint is captured
  (2026-10-05) but deliberately not exposed.
