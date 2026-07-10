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

Archiving via direct DB write is verified impossible — archived state is server-side and owned
by the app; archive in-app and it syncs down. Candidates:
- `smartgym_delete_routine` — routine soft-delete (`ZDATEREMOVED` + dirty flag) verified to
  push without being reverted; semantically distinct from archive (no "Archived Routines" entry).
  Needs an iPhone-arrival confirmation before shipping.
- Revisit archive only if a sanctioned mechanism appears (e.g. an App Intent in a future app
  version).

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
