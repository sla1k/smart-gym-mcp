# SmartGym MCP — Feature Backlog

What is not built yet. Built features and decisions are in [`DESIGN.md`](DESIGN.md).
A new kind of server write is captured from the app first (DESIGN invariant 6).

---

## F2 — Progression, PRs & volume analytics  ·  priority: medium

Per-exercise weight-over-time, personal records (max weight, estimated 1RM), volume/tonnage
trends. Read-only, from data `history/all` already returns: each routine exercise's logged
sets and the workouts they belong to (`model.Workout.set_ids`).

Candidate tools: `smartgym_get_exercise_progression(exercise, range)`,
`smartgym_get_personal_records(exercise?)`, `smartgym_get_volume_trend(routine?, range)`.

---

## F4 — Set-logging writes  ·  priority: medium

Log a workout (sets with reps/weight) from the MCP — e.g. from an external source. The app's
request for saving a workout has not been captured yet: capture it and keep a golden fixture
before building anything. Today logged history is read-only (DESIGN invariant 4); this would
be the first write that touches it.

---

## F9 — Custom exercise definitions  ·  priority: low

Create / edit / delete the account's own custom exercises (F8 only uses existing ones in
routines; [API-client spec](docs/superpowers/specs/2026-10-05-api-client-design.md) §11).
Captures of the app doing it are already committed, unused by any test until F9 is built:
`tests/fixtures/api/add_custom_exercise.json`
(`exercise/addCustomExercise/`) and `remove_custom_exercise.json`
(`exercise/removeCustomExercise/`). Editing a definition is not captured yet.

---

## F3 — Equipment-aware next-weight suggestion  ·  priority: low

`suggest_next_weight(exercise, current_weight)` snapping to the weights the user actually has:
the selected equipment list's dumbbell / kettlebell weights (`model.EquipmentList`, already
shown by `smartgym_get_equipment`).

---

## F6 — MCPB packaging / distribution  ·  priority: low

Package as an `.mcpb` bundle for one-click install (and/or reconsider a TypeScript port for
first-class MCPB tooling). Not needed for single-user local use; today's install is
`uv tool install` from a release tag.
