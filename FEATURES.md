# SmartGym MCP — Feature Backlog

Future plans and deferred features.

---

## F2 — Progression, PRs & volume analytics  ·  priority: medium

Per-exercise weight-over-time, personal records (max weight, estimated 1RM), volume/tonnage trends.
Read-only.

Candidate tools: `smartgym_get_exercise_progression(exercise, range)`,
`smartgym_get_personal_records(exercise?)`, `smartgym_get_volume_trend(routine?, range)`.

---

## F4 — Set-logging writes  ·  priority: medium

Write new logged sets into `ZVALUES` (reps/weight per set) from the MCP — e.g. log a workout from
an external source. Requires PK alloc + `ZUNIQUEHASHID` + correct `ZEXERCISE → ZUNIQEXERCISE.Z_PK`
wiring and `ZWORKOUT`/`ZHISTORY` session linkage.

---

## F7 — Routine delete  ·  priority: medium

Decision 2026-10-05: archive only (`smartgym_archive_routines`). The server's `routine/delete/`
endpoint is captured (2026-10-05) but deliberately not exposed.

---

## F8 — Custom exercises  ·  priority: medium

The exercise catalog is the app bundle's `Exercises.json`, so only built-in exercises resolve
by name. The account's own `customExercises` (returned by `history/all/`) are not resolvable by
name yet — add them to `ExerciseCatalog` (and to the create / add-exercise payloads) once their
wire shape in `routine/add/` / `routine/update/` is captured.

---

## F3 — Equipment-aware next-weight suggestion  ·  priority: low

`suggest_next_weight(exercise, current_weight)` snapping to the user's actual available
plates/dumbbells (`ZEQUIPMENT.ZSELECTEDWEIGHTS`, `ZEQUIPMENTLIST` increments).

---

## F5 — Sync staleness / iPhone-only data detection  ·  priority: low

Detect and warn when data likely lives only on the iPhone (routines/exercises with zero local
logged sets), and surface DB-file freshness.

---

## F6 — MCPB packaging / distribution  ·  priority: low

Package as an `.mcpb` bundle for one-click install (and/or reconsider a TypeScript port for
first-class MCPB tooling). Not needed for single-user local use.
