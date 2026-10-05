# API Client — Plan 1: Phase 0 spike + offline foundation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the Phase 0 experiments that gate the server-first rebuild, and build every part of the new API client that is already fully known (server data model, bundle catalog, change calculation, create/archive payloads, HTTP client core), tested offline.

**Architecture:** New pure modules (`model.py`, `diff.py`, `payloads.py`) and an `api/client.py` HTTP core with an injectable transport and credential provider, added next to the existing DB-based code without touching it. The live tools stay on the DB path until Plan 2 (written from Phase 0 results) wires these modules into `server.py` and retires the DB write path.

**Tech Stack:** Python ≥ 3.11, pydantic 2, httpx 0.28 (`MockTransport` for tests), pytest, ruff, mypy (strict), uv. Phase 0 uses mitmproxy local capture (already installed via `brew install --cask mitmproxy`).

**Spec:** `docs/superpowers/specs/2026-10-05-api-client-design.md`

**Why two plans:** the spec gates the build on Phase 0, and three things are unknown until it runs: auth mechanics (S1), the exact `routine/update/` wire format (S2/S3), and read coverage (S4). Plan 2 (auth provider, update payload, read/write tools, snapshots, retirement of the DB write path) is written from the recorded Phase 0 facts. Task 1 (spike) and Tasks 2–6 (offline foundation) are independent and may run in either order.

## Global Constraints

- Python `>=3.11`; mypy `strict = true` on `src`; ruff `line-length = 95`, rules `I, UP, B, SIM`.
- Commands: `uv run pytest`, `uv run ruff check src tests && uv run ruff format src tests`, `uv run mypy src`.
- Automated tests never touch the network, the SmartGym account, or the real DB.
- Live experiments only on routines named with the `ZZ-` prefix; the user confirms iPhone results.
- Credential values (`Authorization`, `phrase`) are never printed, logged, committed, or put in exception messages or tool output. Anything that reads them is run by the user, not the agent.
- Server base URL: `https://api.smartgymapp.com/v1.1/`. The server sends scalars as strings; parsing coerces in `model.py` only.
- Only template sets (`dateLogged` null) are ever changed. No routine delete (archive only).
- Commit messages: imperative, no AI/assistant attribution lines.

## Review Focus

1. Server JSON with numbers as strings, `null` vs `""` notes, and logged/removed sets mixed into `sets[]` → parsed into clean typed template sets only (Task 2 tests).
2. The same catalog exercise appearing twice in one routine (warm-up + working "Cable Chest Press") → diff matches by server `identifier`, never by catalog id (Task 4 test).
3. `sets: []` (empty list) for an exercise → rejected, not "delete all sets" (Task 4 test).
4. Non-ASCII names ("FB-A — Return W1") through multipart → sent as UTF-8 (Task 6 test).
5. HTTP 200 with `{"code": "ROUTINE_NOT_FOUND"}` → `ApiError`, never treated as success (Task 6 test).

---

### Task 1: Phase 0 spike (S1–S6, user in the loop)

Throwaway investigation. Output: verified facts appended to the spec §3/§4, scrubbed fixtures for Plan 2, and an exit decision. No product code.

**Files:**
- Create: `scripts/spike/capture.py` (mitmproxy addon; run by the user)
- Create: `scripts/spike/send.py` (sends one hand-built request; run by the user)
- Create: `scripts/spike/scrub.py` (turns captures into fixtures)
- Create: `tests/fixtures/api/` (scrubbed captures)
- Modify: `docs/superpowers/specs/2026-10-05-api-client-design.md` §3, §4

**Interfaces:**
- Produces for Plan 2: `tests/fixtures/api/update_<edit>.json` (one per S2 edit: `{"path": ..., "form": {...}, "response": {...}}`), `tests/fixtures/api/routine_single.json`, `tests/fixtures/api/history_all.json`, spec facts for auth / update format / read coverage.

> Steps 1–3 code below is the original draft; the committed `scripts/spike/*` were revised after the final review (secret redaction in the log, decoded form + query values, credentials saved only as a complete pair with `authID`, scrub fails loudly instead of writing unscrubbed/empty fixtures, `send.py` takes `authID` from the credentials file). The scripts are authoritative.

- [ ] **Step 1: Write the capture addon**

`scripts/spike/capture.py`:

```python
"""mitmproxy addon for the Phase 0 spike. RUN BY THE USER, not the agent.

Logs SmartGym API bodies to spike-flows.jsonl. Credential headers are never
written there — only a verdict per request: whether `phrase` / `Authorization`
equal the previous request's value. With SPIKE_SAVE_CREDENTIALS=1 the latest
headers are written to ~/.smartgym-mcp/credentials.json (mode 0600) for
send.py; that file never leaves the user's machine.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

from mitmproxy import http

OUT = Path(__file__).with_name("spike-flows.jsonl")
CRED = Path.home() / ".smartgym-mcp" / "credentials.json"
SECRET_HEADERS = ("authorization", "phrase")
_last: dict[str, str] = {}


def _verdicts(flow: http.HTTPFlow) -> dict[str, str]:
    out = {}
    for h in SECRET_HEADERS:
        value = flow.request.headers.get(h)
        if value is None:
            out[h] = "absent"
            continue
        digest = hashlib.sha256(value.encode()).hexdigest()
        out[h] = "first" if h not in _last else ("same" if _last[h] == digest else "CHANGED")
        _last[h] = digest
    return out


def response(flow: http.HTTPFlow) -> None:
    if "smartgymapp.com" not in flow.request.pretty_host:
        return
    rec = {
        "t": time.strftime("%Y-%m-%d %H:%M:%S"),
        "method": flow.request.method,
        "path": flow.request.path.split("?")[0],
        "query_keys": sorted(flow.request.query.keys()),
        "header_names": sorted(flow.request.headers.keys()),
        "secret_verdicts": _verdicts(flow),
        "req": flow.request.get_content().decode("utf-8", errors="replace"),
        "status": flow.response.status_code if flow.response else None,
        "resp": flow.response.get_content().decode("utf-8", errors="replace")
        if flow.response
        else "",
    }
    with OUT.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    if os.environ.get("SPIKE_SAVE_CREDENTIALS") == "1":
        CRED.parent.mkdir(parents=True, exist_ok=True)
        payload = {h: flow.request.headers[h] for h in SECRET_HEADERS if h in flow.request.headers}
        CRED.write_text(json.dumps(payload))
        CRED.chmod(0o600)
```

- [ ] **Step 2: Write the send script**

`scripts/spike/send.py`:

```python
"""Send ONE hand-built request with the user's captured credentials. RUN BY THE USER.

usage: uv run python scripts/spike/send.py <path> <form.json>
form.json: {"field": "value", ...} — values sent as multipart form fields.
Prints only the HTTP status and the JSON response body.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import httpx

BASE = "https://api.smartgymapp.com/v1.1/"
CRED = Path.home() / ".smartgym-mcp" / "credentials.json"


def main() -> None:
    path, form_file = sys.argv[1], Path(sys.argv[2])
    creds = json.loads(CRED.read_text())
    form = json.loads(form_file.read_text(encoding="utf-8"))
    form.setdefault("requestDate", dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%S"))
    files = {k: (None, str(v).encode("utf-8")) for k, v in form.items()}
    r = httpx.post(BASE + path, files=files, headers=creds, timeout=30)
    print(r.status_code)
    print(r.text)


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Write the scrubber**

`scripts/spike/scrub.py`:

```python
"""Turn spike-flows.jsonl records into committed test fixtures.

usage: uv run python scripts/spike/scrub.py <record-index> <fixture-name>
Replaces the account id with "1" everywhere and drops credential verdicts.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

SRC = Path(__file__).with_name("spike-flows.jsonl")
DEST = Path(__file__).parents[2] / "tests" / "fixtures" / "api"


def _form(body: str) -> dict[str, str]:
    fields = re.findall(r'name\s*=\s*"(\w+)"\s*\r?\n\r?\n(.*?)\r?\n-{5,}', body, re.S)
    return {k: v for k, v in fields}


def main() -> None:
    index, name = int(sys.argv[1]), sys.argv[2]
    rec = json.loads(SRC.read_text(encoding="utf-8").splitlines()[index])
    form = _form(rec["req"])
    account = form.get("authID", "")
    blob = json.dumps(
        {"path": rec["path"], "form": form, "response": json.loads(rec["resp"] or "{}")},
        ensure_ascii=False,
        indent=2,
    )
    if account:
        blob = blob.replace(account, "1")
    DEST.mkdir(parents=True, exist_ok=True)
    (DEST / f"{name}.json").write_text(blob + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Start capturing (user)**

The user runs (and approves the redirector extension / trusted CA as on 2026-10-05):

```bash
SPIKE_SAVE_CREDENTIALS=1 mitmdump --mode local:SmartGym -s scripts/spike/capture.py --set flow_detail=0 --set confdir=<dir holding the CA trusted on 2026-10-05>
```

Without `confdir` mitmdump mints a new, untrusted CA and every SmartGym TLS handshake fails (nothing is logged).

Then opens SmartGym on the Mac.

- [ ] **Step 5: S1 — auth verdicts**

User: browse a few routines, quit and relaunch SmartGym, browse again. Agent then reads ONLY the verdict column:

```bash
python3 -c "import json; [print(r['t'], r['path'], r['secret_verdicts']) for r in map(json.loads, open('scripts/spike/spike-flows.jsonl'))]"
```

Expected outcomes and meaning:
- `phrase` always `same` across requests and the restart → static; the provider can be a user-maintained file.
- `phrase` `CHANGED` per request → signed per request; record it and STOP the build decision for S1 until the signing input is understood (exit rule: revisit).
- Next day (or after ≥ 24 h), the user runs `send.py` with a read (`routine/all/1/`-style GET is not supported by send.py; use Step 7's archive/unarchive pair on a `ZZ-` routine) → `SUCCESS` means the token survives a day.

Record the verdicts in spec §4 S1 and §3.

- [ ] **Step 6: S2 — update payload capture**

User creates `ZZ-SPIKE` in the Mac app with 3 exercises (2 sets each), then performs these edits one at a time, tapping Save after each, telling the agent after each one:
1. rename routine, 2. change days, 3. change goal, 4. change routine note, 5. change one exercise's rest, 6. change one exercise's note, 7. reorder two exercises, 8. add an exercise, 9. remove an exercise, 10. add a set, 11. change a set's reps and weight, 12. remove a set.

After each edit the agent locates the newest `routine/update/` record and scrubs it:

```bash
n=$(($(wc -l < scripts/spike/spike-flows.jsonl) - 1)); uv run python scripts/spike/scrub.py $n update_<edit-name>
```

Expected: 12 fixtures `tests/fixtures/api/update_*.json`. Document the field shapes (`routineID`, `exercisesOrder`, `removeExercises`, `updateExercises[].addedSets/updatedSets/removedSets`, routine field keys, how a new exercise is introduced) in spec §3.

- [ ] **Step 7: S3 + archive check — hand-built round trip**

Agent writes `scripts/spike/s3_form.json` = the `update_note` fixture's `form` with the note changed to `"S3 hand-built"`; user runs:

```bash
uv run python scripts/spike/send.py routine/update/ scripts/spike/s3_form.json
```

Expected: `200` + `{"code":"SUCCESS"...}`; user confirms the note on the iPhone. Then the archive pair on `ZZ-SPIKE` (agent writes `{"routinesIDs": "<id>"}` / `{"routineID": "<id>"}`; `send.py` fills `authID` from the local credentials file):

```bash
uv run python scripts/spike/send.py routine/archive/ scripts/spike/archive_form.json
uv run python scripts/spike/send.py routine/unarchive/ scripts/spike/unarchive_form.json
```

Expected: both `SUCCESS`; iPhone shows archived, then active again.

- [ ] **Step 8: S6 — minimal create payload**

Agent hand-builds `scripts/spike/s6_form.json` for a new routine `ZZ-S6` with Push Up (catalog 194, 2 sets) and Plank (catalog 20, 1 set) using EXACTLY the shape `payloads.new_routine_payload` produces (Task 5; if Task 5 is not done yet, copy the shape from its Step 3 code), i.e. no `identifier` / `routineID` keys and `isSingleWeight: 0`. User runs:

```bash
uv run python scripts/spike/send.py routine/add/ scripts/spike/s6_form.json
```

Expected: `SUCCESS` with `server_id`s; iPhone shows `ZZ-S6` with correct sets. If rejected, record the server message; Plan 2 adjusts `payloads.py`.

- [ ] **Step 9: S4 — read coverage**

User opens a real routine, opens one exercise's history/progress screen, and the workout history tab. Agent lists the endpoints hit and scrubs one each:

```bash
python3 -c "import json; [print(i, r['method'], r['path'], sorted(r['query'])) for i, r in enumerate(map(json.loads, open('scripts/spike/spike-flows.jsonl')))]"
```

Then `scrub.py <i> routine_single`, `scrub.py <i> history_all`, `scrub.py <i> exercise_history`. Record in spec §4 S4 which current read-tool fields (`smartgym_get_routine` sessions/top_set/volume, `smartgym_get_workout_history` duration/calories/HR, equipment weights) each endpoint provides, and list gaps.

- [ ] **Step 10: S5 — Mac app coexistence**

With the Mac app open and idle, user runs Step 7's note update again with note `"S5"`, then refreshes the Mac routine list (pull to refresh / reopen the routine). Expected: Mac shows `S5`; capture shows no `routine/add/` or `routine/update/` sent by the Mac afterwards. Record in spec §4 S5.

- [ ] **Step 11: Exit decision + commit**

Write the outcome table into spec §4 (S1–S6 pass/fail + notes) and the decision (build / build with DB-read fallback for listed gaps / stop). Clean up: user deletes `ZZ-SPIKE`, `ZZ-S6` in-app, stops mitmdump, removes CA trust. `scripts/spike/spike-flows.jsonl` is NOT committed.

```bash
printf 'scripts/spike/spike-flows.jsonl\nscripts/spike/*_form.json\n' >> .gitignore
git add .gitignore scripts/spike/capture.py scripts/spike/send.py scripts/spike/scrub.py tests/fixtures/api docs/superpowers/specs/2026-10-05-api-client-design.md
git commit -m "Record Phase 0 API spike results and fixtures"
```

---

### Task 2: Server routine model (`model.py`)

**Files:**
- Create: `src/smartgym_mcp/model.py`
- Create: `tests/fixtures/api/routine_single_synthetic.json`
- Test: `tests/test_api_model.py`

**Interfaces:**
- Produces:
  - `class ApiPayloadError(ValueError)`
  - `class TemplateSet(BaseModel)`: `identifier: int`, `unique_hashid: int`, `index: int`, `reps: float`, `weight_kg: float`
  - `class RoutineExercise(BaseModel)`: `identifier: int`, `unique_hashid: int`, `catalog_id: int`, `name: str`, `index: int`, `rest_seconds: int`, `note: str | None`, `removed: bool`, `template_sets: list[TemplateSet]`
  - `class Routine(BaseModel)`: `identifier: int`, `unique_hashid: int`, `name: str`, `days: str | None`, `goal: str | None`, `note: str | None`, `archived: bool`, `removed: bool`, `exercises: list[RoutineExercise]`; method `active_exercises() -> list[RoutineExercise]` (not removed, sorted by `index`)
  - `parse_routine(raw: Mapping[str, Any]) -> Routine`
  - `parse_routines_response(raw: Mapping[str, Any]) -> list[Routine]` (raises `ApiPayloadError` unless `code == "SUCCESS"`)

- [ ] **Step 1: Write the synthetic fixture**

`tests/fixtures/api/routine_single_synthetic.json` (shape copied from a real `routine/single/` response, values invented):

```json
{
  "code": "SUCCESS",
  "lastModified": "2026-10-01 16:25:09",
  "hasMore": false,
  "routines": [
    {
      "identifier": "3000001",
      "uniqueHashID": "26091100000001",
      "days": "0001",
      "reference": "0",
      "number": "18",
      "goal": "",
      "dateCreated": "2026-09-11 08:34:19",
      "name": "ZZ-FB — Test",
      "note": "Routine note",
      "dateRemoved": null,
      "dateArchived": null,
      "userID": "1",
      "migratedSets": "1",
      "source": "cloud",
      "exercises": [
        {
          "id": "363", "name": "Resistance Band Pull Apart", "type": "0", "isCustom": "0",
          "identifier": "40000001", "uniqueHashID": "26091100000011", "idx": "1",
          "pause": "10", "note": null, "dateRemoved": null, "mode": "0", "routineID": "3000001",
          "listGroup": "0",
          "sets": [
            {"identifier": "50000002", "uniqueHashID": "26091100000102", "firstValue": "1",
             "secondValue": "15", "thirdValue": "0", "type": "0", "index": "1",
             "dateRemoved": null, "dateLogged": null},
            {"identifier": "50000001", "uniqueHashID": "26091100000101", "firstValue": "1",
             "secondValue": "19", "thirdValue": "0", "type": "0", "index": "0",
             "dateRemoved": null, "dateLogged": null},
            {"identifier": "50000003", "uniqueHashID": "26091100000103", "firstValue": "1",
             "secondValue": "20", "thirdValue": "0", "type": "0", "index": "0",
             "dateRemoved": null, "dateLogged": "2026-09-14 10:00:00"},
            {"identifier": "50000004", "uniqueHashID": "26091100000104", "firstValue": "1",
             "secondValue": "8", "thirdValue": "0", "type": "0", "index": "2",
             "dateRemoved": "2026-09-12 00:00:00", "dateLogged": null}
          ]
        },
        {
          "id": "207", "name": "Cable Chest Press", "type": "0", "isCustom": "0",
          "identifier": "40000002", "uniqueHashID": "26091100000012", "idx": "0",
          "pause": "75", "note": "", "dateRemoved": null, "mode": "0", "routineID": "3000001",
          "listGroup": "0",
          "sets": [
            {"identifier": "50000011", "uniqueHashID": "26091100000111", "firstValue": "1",
             "secondValue": "10", "thirdValue": "42.5", "type": "0", "index": "0",
             "dateRemoved": null, "dateLogged": null}
          ]
        },
        {
          "id": "140", "name": "Bridge", "type": "1", "isCustom": "0",
          "identifier": "40000003", "uniqueHashID": "26091100000013", "idx": "2",
          "pause": "10", "note": "2 sec hold", "dateRemoved": "2026-09-20 00:00:00",
          "mode": "0", "routineID": "3000001", "listGroup": "0", "sets": []
        }
      ]
    }
  ]
}
```

- [ ] **Step 2: Write the failing tests**

`tests/test_api_model.py`:

```python
"""model.py: SmartGym API JSON → typed routine model (strings coerced once)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from smartgym_mcp.model import ApiPayloadError, parse_routine, parse_routines_response

FIXTURE = Path(__file__).parent / "fixtures" / "api" / "routine_single_synthetic.json"


def _raw() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_parses_routine_fields_and_coerces_strings() -> None:
    (routine,) = parse_routines_response(_raw())
    assert routine.identifier == 3000001
    assert routine.unique_hashid == 26091100000001
    assert routine.name == "ZZ-FB — Test"
    assert routine.days == "0001"
    assert routine.goal is None  # "" normalizes to None
    assert routine.note == "Routine note"
    assert not routine.archived and not routine.removed


def test_active_exercises_sorted_by_index_and_skip_removed() -> None:
    (routine,) = parse_routines_response(_raw())
    active = routine.active_exercises()
    assert [e.name for e in active] == ["Cable Chest Press", "Resistance Band Pull Apart"]
    assert [e.catalog_id for e in active] == [207, 363]
    assert len(routine.exercises) == 3
    removed = next(e for e in routine.exercises if e.name == "Bridge")
    assert removed.removed


def test_template_sets_exclude_logged_and_removed_and_sort_by_index() -> None:
    (routine,) = parse_routines_response(_raw())
    band = next(e for e in routine.exercises if e.catalog_id == 363)
    assert [(s.identifier, s.reps, s.weight_kg) for s in band.template_sets] == [
        (50000001, 19.0, 0.0),
        (50000002, 15.0, 0.0),
    ]
    chest = next(e for e in routine.exercises if e.catalog_id == 207)
    assert chest.template_sets[0].weight_kg == 42.5
    assert chest.rest_seconds == 75
    assert chest.note is None  # "" normalizes to None


def test_non_success_code_raises() -> None:
    with pytest.raises(ApiPayloadError, match="ROUTINE_NOT_FOUND"):
        parse_routines_response({"code": "ROUTINE_NOT_FOUND"})


def test_missing_required_field_raises_payload_error() -> None:
    raw = _raw()["routines"][0]
    del raw["identifier"]
    with pytest.raises(ApiPayloadError, match="identifier"):
        parse_routine(raw)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_api_model.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'smartgym_mcp.model'`

- [ ] **Step 4: Implement `model.py`**

`src/smartgym_mcp/model.py`:

```python
"""Server-side routine model (spec 04 §3), parsed from SmartGym API JSON.

The API sends every scalar as a string ("pause": "10") and mixes logged and
removed sets into `sets[]`. Parsing coerces and filters once, here, so nothing
downstream ever sees raw API strings.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel


class ApiPayloadError(ValueError):
    """The server answered with an unexpected shape or a non-SUCCESS code."""


class TemplateSet(BaseModel):
    identifier: int
    unique_hashid: int
    index: int
    reps: float
    weight_kg: float


class RoutineExercise(BaseModel):
    identifier: int
    unique_hashid: int
    catalog_id: int
    name: str
    index: int
    rest_seconds: int
    note: str | None
    removed: bool
    template_sets: list[TemplateSet]


class Routine(BaseModel):
    identifier: int
    unique_hashid: int
    name: str
    days: str | None
    goal: str | None
    note: str | None
    archived: bool
    removed: bool
    exercises: list[RoutineExercise]

    def active_exercises(self) -> list[RoutineExercise]:
        return sorted((e for e in self.exercises if not e.removed), key=lambda e: e.index)


def _req(raw: Mapping[str, Any], key: str) -> Any:
    if key not in raw or raw[key] is None:
        raise ApiPayloadError(f"API object is missing required field {key!r}.")
    return raw[key]


def _int(raw: Mapping[str, Any], key: str) -> int:
    return int(str(_req(raw, key)))


def _float(raw: Mapping[str, Any], key: str) -> float:
    return float(str(_req(raw, key)))


def _text(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value)
    return s if s.strip() else None


def _parse_set(raw: Mapping[str, Any]) -> TemplateSet:
    return TemplateSet(
        identifier=_int(raw, "identifier"),
        unique_hashid=_int(raw, "uniqueHashID"),
        index=_int(raw, "index"),
        reps=_float(raw, "secondValue"),
        weight_kg=_float(raw, "thirdValue"),
    )


def _parse_exercise(raw: Mapping[str, Any]) -> RoutineExercise:
    sets = [
        _parse_set(s)
        for s in raw.get("sets") or []
        if s.get("dateLogged") is None and s.get("dateRemoved") is None
    ]
    return RoutineExercise(
        identifier=_int(raw, "identifier"),
        unique_hashid=_int(raw, "uniqueHashID"),
        catalog_id=_int(raw, "id"),
        name=str(_req(raw, "name")),
        index=_int(raw, "idx"),
        rest_seconds=int(str(raw.get("pause") or 0)),
        note=_text(raw.get("note")),
        removed=raw.get("dateRemoved") is not None,
        template_sets=sorted(sets, key=lambda s: s.index),
    )


def parse_routine(raw: Mapping[str, Any]) -> Routine:
    return Routine(
        identifier=_int(raw, "identifier"),
        unique_hashid=_int(raw, "uniqueHashID"),
        name=str(_req(raw, "name")),
        days=_text(raw.get("days")),
        goal=_text(raw.get("goal")),
        note=_text(raw.get("note")),
        archived=raw.get("dateArchived") is not None,
        removed=raw.get("dateRemoved") is not None,
        exercises=[_parse_exercise(e) for e in raw.get("exercises") or []],
    )


def parse_routines_response(raw: Mapping[str, Any]) -> list[Routine]:
    code = raw.get("code")
    if code != "SUCCESS":
        raise ApiPayloadError(f"SmartGym API answered {code!r} instead of SUCCESS.")
    return [parse_routine(r) for r in raw.get("routines") or []]


__all__ = [
    "ApiPayloadError",
    "Routine",
    "RoutineExercise",
    "TemplateSet",
    "parse_routine",
    "parse_routines_response",
]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_api_model.py -v && uv run mypy src && uv run ruff check src tests`
Expected: 5 passed; mypy "Success"; ruff "All checks passed!"

- [ ] **Step 6: Commit**

```bash
git add src/smartgym_mcp/model.py tests/test_api_model.py tests/fixtures/api/routine_single_synthetic.json
git commit -m "Add SmartGym API routine model with string coercion"
```

---

### Task 3: Exercise catalog from the app bundle

**Files:**
- Modify: `src/smartgym_mcp/catalog.py` (add `CatalogExercise`, `load_bundle_exercises`)
- Modify: `src/smartgym_mcp/matching.py` (add `ExerciseCatalog.from_bundle`)
- Test: `tests/test_bundle_catalog.py`

**Interfaces:**
- Consumes: `catalog.read_catalog(cfg: Config, name: str) -> str` (existing), `Config` (existing; `app_bundle: Path`).
- Produces:
  - `@dataclass(frozen=True) class CatalogExercise`: `id: int`, `name: str`, `type: int`, `category: int`, `sub_categories: str`, `two_sides: int`, `stretch: int`, `equipment_ids: tuple[str, ...]`, `images: tuple[str, str, str, str, str, str]`
  - `load_bundle_exercises(cfg: Config) -> list[CatalogExercise]`
  - `ExerciseCatalog.from_bundle(exercises: Sequence[CatalogExercise]) -> ExerciseCatalog` — resolutions carry the **catalog id** in `ExerciseResolution.z_pk` (field rename is Plan 2, when the DB path is retired).

- [ ] **Step 1: Write the failing tests**

`tests/test_bundle_catalog.py`:

```python
"""Bundle catalog: Exercises.json → CatalogExercise + name resolution by catalog id."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from smartgym_mcp.catalog import load_bundle_exercises
from smartgym_mcp.config import Config, load_config
from smartgym_mcp.matching import ExerciseCatalog, UnresolvedExercise

EXERCISES = {
    "exercises": [
        {"id": "194", "name": "Push Up", "type": "1", "category": "11", "subCategories": "9,12",
         "twoSides": "0", "stretch": "0", "equipments": "", "firstImage": "0194-1",
         "secondImage": "0194-2", "thirdImage": "", "fourthImage": "", "fifthImage": "",
         "sixthImage": ""},
        {"id": "363", "name": "Resistance Band Pull Apart", "type": "0", "category": "5",
         "subCategories": "101", "twoSides": "0", "stretch": "0", "equipments": "38",
         "firstImage": "0363-1", "secondImage": "0363-2", "thirdImage": "",
         "fourthImage": "", "fifthImage": "", "sixthImage": ""},
    ]
}


@pytest.fixture
def bundle_cfg(tmp_path: Path) -> Config:
    resources = tmp_path / "SmartGym.app" / "Contents" / "Resources"
    resources.mkdir(parents=True)
    (resources / "Exercises.json").write_text(json.dumps(EXERCISES), encoding="utf-8")
    return replace(load_config(), app_bundle=tmp_path / "SmartGym.app")


def test_load_bundle_exercises_coerces_fields(bundle_cfg: Config) -> None:
    push_up, band = load_bundle_exercises(bundle_cfg)
    assert (push_up.id, push_up.name, push_up.type, push_up.category) == (194, "Push Up", 1, 11)
    assert push_up.sub_categories == "9,12"
    assert push_up.equipment_ids == ()
    assert band.equipment_ids == ("38",)
    assert push_up.images == ("0194-1", "0194-2", "", "", "", "")


def test_from_bundle_resolves_to_catalog_id(bundle_cfg: Config) -> None:
    catalog = ExerciseCatalog.from_bundle(load_bundle_exercises(bundle_cfg))
    assert catalog.resolve("push up").z_pk == 194
    assert catalog.resolve("363").resolved_name == "Resistance Band Pull Apart"
    with pytest.raises(UnresolvedExercise):
        catalog.resolve("Zercher squat")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_bundle_catalog.py -v`
Expected: FAIL — `ImportError: cannot import name 'load_bundle_exercises'`

- [ ] **Step 3: Implement**

Append to `src/smartgym_mcp/catalog.py` (add `import json` and `from dataclasses import dataclass` to the imports):

```python
@dataclass(frozen=True)
class CatalogExercise:
    """One bundle catalog exercise; `id` is the server's exercise `id`/`genericID`."""

    id: int
    name: str
    type: int
    category: int
    sub_categories: str
    two_sides: int
    stretch: int
    equipment_ids: tuple[str, ...]
    images: tuple[str, str, str, str, str, str]


def load_bundle_exercises(cfg: Config) -> list[CatalogExercise]:
    raw = json.loads(read_catalog(cfg, "exercises"))["exercises"]
    return [
        CatalogExercise(
            id=int(e["id"]),
            name=str(e["name"]),
            type=int(e["type"]),
            category=int(e["category"]),
            sub_categories=str(e.get("subCategories") or ""),
            two_sides=int(e.get("twoSides") or 0),
            stretch=int(e.get("stretch") or 0),
            equipment_ids=tuple(x for x in str(e.get("equipments") or "").split(",") if x),
            images=(
                str(e.get("firstImage") or ""),
                str(e.get("secondImage") or ""),
                str(e.get("thirdImage") or ""),
                str(e.get("fourthImage") or ""),
                str(e.get("fifthImage") or ""),
                str(e.get("sixthImage") or ""),
            ),
        )
        for e in raw
    ]
```

In `src/smartgym_mcp/matching.py` add `from collections.abc import Sequence` and `from .catalog import CatalogExercise` to the imports, and this classmethod under `load`:

```python
    @classmethod
    def from_bundle(cls, exercises: Sequence[CatalogExercise]) -> ExerciseCatalog:
        """Catalog from the app bundle; resolutions carry the catalog id in `z_pk`."""
        return cls([(e.id, e.name) for e in exercises])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_bundle_catalog.py tests/test_create_program.py -v && uv run mypy src && uv run ruff check src tests`
Expected: all pass (existing matching tests unchanged); mypy/ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/smartgym_mcp/catalog.py src/smartgym_mcp/matching.py tests/test_bundle_catalog.py
git commit -m "Load the exercise catalog from the SmartGym app bundle"
```

---

### Task 4: Change calculation (`diff.py`)

**Files:**
- Create: `src/smartgym_mcp/diff.py`
- Test: `tests/test_diff.py`

**Interfaces:**
- Consumes: `model.Routine`, `model.RoutineExercise`, `model.TemplateSet` (Task 2); `matching.ExerciseCatalog`, `matching.UnresolvedExercise` (existing); `models.FieldChange(field, old, new)`, `models.SetSpec(reps, weight_kg)` (existing).
- Produces:
  - `class DiffError(ValueError)`
  - `class DesiredExercise(BaseModel)`: `exercise_id: int | None = None`, `exercise: str | None = None` (exactly one), `rest_seconds: int | None = Field(None, ge=0)`, `note: str | None = None`, `sets: list[SetSpec] | None = None`
  - `class DesiredRoutine(BaseModel)`: `name / days / goal / note: str | None = None`, `exercises: list[DesiredExercise] | None = None` — `None` = keep; `""` clears days/goal/note; `exercises` given = the full ordered active list (omitted existing exercises are removed)
  - `class AddedSet(BaseModel)`: `index: int`, `reps: float`, `weight_kg: float`
  - `class UpdatedSet(BaseModel)`: `identifier: int`, `index: int`, `reps: float`, `weight_kg: float`
  - `class ExerciseUpdate(BaseModel)`: `identifier: int`, `name: str`, `changes: list[FieldChange]`, `added_sets: list[AddedSet]`, `updated_sets: list[UpdatedSet]`, `removed_set_ids: list[int]`
  - `class AddedExercise(BaseModel)`: `catalog_id: int`, `name: str`, `position: int`, `rest_seconds: int`, `note: str | None`, `sets: list[SetSpec]`
  - `class RemovedExercise(BaseModel)`: `identifier: int`, `name: str`
  - `class ChangeSet(BaseModel)`: `routine_identifier: int`, `routine_name: str`, `routine_changes: list[FieldChange]`, `added: list[AddedExercise]`, `removed: list[RemovedExercise]`, `updated: list[ExerciseUpdate]`, `final_order: list[str] | None` (keys `"id:<identifier>"` / `"new:<index into added>"`; `None` = order unchanged), `warnings: list[str]`; property `is_empty -> bool`
  - `DEFAULT_SET: SetSpec` (1 × 10 reps, bodyweight)
  - `diff_routine(current: Routine, desired: DesiredRoutine, catalog: ExerciseCatalog) -> ChangeSet` — all-or-nothing: collects every problem, raises one `DiffError`

- [ ] **Step 1: Write the failing tests**

`tests/test_diff.py`:

```python
"""diff.py: (current routine, desired routine) → ChangeSet. Pure, no I/O."""

from __future__ import annotations

import pytest

from smartgym_mcp.diff import DesiredExercise, DesiredRoutine, DiffError, diff_routine
from smartgym_mcp.matching import ExerciseCatalog
from smartgym_mcp.model import Routine, RoutineExercise, TemplateSet
from smartgym_mcp.models import SetSpec

CATALOG = ExerciseCatalog([(194, "Push Up"), (20, "Plank"), (207, "Cable Chest Press")])


def _sets(*pairs: tuple[float, float], base: int) -> list[TemplateSet]:
    return [
        TemplateSet(identifier=base + i, unique_hashid=base + i, index=i, reps=r, weight_kg=w)
        for i, (r, w) in enumerate(pairs)
    ]


def _routine() -> Routine:
    def ex(ident: int, cat: int, name: str, idx: int, sets: list[TemplateSet]) -> RoutineExercise:
        return RoutineExercise(
            identifier=ident, unique_hashid=ident, catalog_id=cat, name=name, index=idx,
            rest_seconds=60, note=None, removed=False, template_sets=sets,
        )

    return Routine(
        identifier=1, unique_hashid=1, name="ZZ-R", days="0001", goal=None, note="old",
        archived=False, removed=False,
        exercises=[
            ex(11, 207, "Cable Chest Press", 0, _sets((12, 20), base=100)),   # warm-up
            ex(12, 207, "Cable Chest Press", 1, _sets((10, 40), (10, 40), (8, 42.5), base=200)),
            ex(13, 194, "Push Up", 2, _sets((15, 0), base=300)),
        ],
    )


def _keep_all(**overrides: DesiredExercise) -> list[DesiredExercise]:
    return [overrides.get(str(i), DesiredExercise(exercise_id=i)) for i in (11, 12, 13)]


def test_nothing_requested_is_empty() -> None:
    assert diff_routine(_routine(), DesiredRoutine(), CATALOG).is_empty


def test_routine_fields_change_and_empty_string_clears() -> None:
    cs = diff_routine(_routine(), DesiredRoutine(name="ZZ-R2", note="", days="0001"), CATALOG)
    assert [(c.field, c.old, c.new) for c in cs.routine_changes] == [
        ("name", "ZZ-R", "ZZ-R2"),
        ("note", "old", None),
    ]


def test_empty_name_rejected() -> None:
    with pytest.raises(DiffError, match="name must be non-empty"):
        diff_routine(_routine(), DesiredRoutine(name="  "), CATALOG)


def test_reorder_only() -> None:
    order = [DesiredExercise(exercise_id=i) for i in (13, 11, 12)]
    cs = diff_routine(_routine(), DesiredRoutine(exercises=order), CATALOG)
    assert cs.final_order == ["id:13", "id:11", "id:12"]
    assert not (cs.added or cs.removed or cs.updated)


def test_omitted_exercise_is_removed_without_reorder() -> None:
    keep = [DesiredExercise(exercise_id=11), DesiredExercise(exercise_id=13)]
    cs = diff_routine(_routine(), DesiredRoutine(exercises=keep), CATALOG)
    assert [(r.identifier, r.name) for r in cs.removed] == [(12, "Cable Chest Press")]
    assert cs.final_order is None


def test_add_new_exercise_by_name_at_position() -> None:
    wanted = _keep_all()
    wanted.insert(1, DesiredExercise(exercise="plank", rest_seconds=45, sets=[SetSpec(reps=30)]))
    cs = diff_routine(_routine(), DesiredRoutine(exercises=wanted), CATALOG)
    (added,) = cs.added
    assert (added.catalog_id, added.name, added.position, added.rest_seconds) == (20, "Plank", 1, 45)
    assert cs.final_order == ["id:11", "new:0", "id:12", "id:13"]


def test_new_exercise_without_sets_gets_default_set_and_warning() -> None:
    wanted = [*_keep_all(), DesiredExercise(exercise="Plank")]
    cs = diff_routine(_routine(), DesiredRoutine(exercises=wanted), CATALOG)
    assert [(s.reps, s.weight_kg) for s in cs.added[0].sets] == [(10, 0.0)]
    assert any("default" in w for w in cs.warnings)


def test_duplicate_catalog_exercise_matched_by_identifier() -> None:
    working = DesiredExercise(exercise_id=12, rest_seconds=120)
    cs = diff_routine(_routine(), DesiredRoutine(exercises=_keep_all(**{"12": working})), CATALOG)
    (upd,) = cs.updated
    assert upd.identifier == 12
    assert [(c.field, c.old, c.new) for c in upd.changes] == [("rest_seconds", "60", "120")]


def test_sets_update_add_and_remove_by_position() -> None:
    sets = [SetSpec(reps=10, weight_kg=40), SetSpec(reps=10, weight_kg=45)]
    cs = diff_routine(
        _routine(),
        DesiredRoutine(exercises=_keep_all(**{"12": DesiredExercise(exercise_id=12, sets=sets)})),
        CATALOG,
    )
    (upd,) = cs.updated
    assert [(u.identifier, u.index, u.reps, u.weight_kg) for u in upd.updated_sets] == [
        (201, 1, 10, 45)
    ]
    assert upd.removed_set_ids == [202]
    assert upd.added_sets == []

    more = [SetSpec(reps=15), SetSpec(reps=12)]
    cs = diff_routine(
        _routine(),
        DesiredRoutine(exercises=_keep_all(**{"13": DesiredExercise(exercise_id=13, sets=more)})),
        CATALOG,
    )
    (upd,) = cs.updated
    assert [(a.index, a.reps) for a in upd.added_sets] == [(1, 12)]
    assert upd.updated_sets == [] and upd.removed_set_ids == []


def test_exercise_note_change_and_clear() -> None:
    cs = diff_routine(
        _routine(),
        DesiredRoutine(exercises=_keep_all(**{"13": DesiredExercise(exercise_id=13, note="Slow")})),
        CATALOG,
    )
    assert [(c.field, c.old, c.new) for c in cs.updated[0].changes] == [("note", None, "Slow")]


def test_empty_sets_list_rejected() -> None:
    with pytest.raises(DiffError, match="sets must not be empty"):
        diff_routine(
            _routine(),
            DesiredRoutine(exercises=_keep_all(**{"13": DesiredExercise(exercise_id=13, sets=[])})),
            CATALOG,
        )


def test_all_problems_reported_together() -> None:
    wanted = [
        DesiredExercise(exercise_id=999),
        DesiredExercise(exercise="Zercher squat"),
        DesiredExercise(exercise_id=11),
        DesiredExercise(exercise_id=11),
    ]
    with pytest.raises(DiffError) as exc:
        diff_routine(_routine(), DesiredRoutine(exercises=wanted), CATALOG)
    msg = str(exc.value)
    assert "999" in msg and "Zercher" in msg and "more than once" in msg


def test_desired_exercise_needs_exactly_one_reference() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        DesiredExercise()
    with pytest.raises(ValueError, match="exactly one"):
        DesiredExercise(exercise_id=1, exercise="Plank")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_diff.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'smartgym_mcp.diff'`

- [ ] **Step 3: Implement `diff.py`**

`src/smartgym_mcp/diff.py`:

```python
"""Desired-vs-current routine diff (spec 04 §5). Pure — no I/O, no wire format.

Single-field tools and apply_routine both describe a *desired* routine; this
module turns it into one ChangeSet that payloads.py later encodes for
routine/update/. Exercises are matched by server identifier only, so a
routine holding the same catalog exercise twice stays unambiguous. Sets are
matched by position: overlap → update, extra desired → add, extra current →
remove. Validation is all-or-nothing.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from .matching import ExerciseCatalog, UnresolvedExercise
from .model import Routine, RoutineExercise
from .models import FieldChange, SetSpec

DEFAULT_SET = SetSpec(reps=10, weight_kg=0.0)


class DiffError(ValueError):
    """The desired routine was rejected — nothing should be sent."""


class DesiredExercise(BaseModel):
    exercise_id: int | None = None
    exercise: str | None = None
    rest_seconds: int | None = Field(default=None, ge=0)
    note: str | None = None
    sets: list[SetSpec] | None = None

    @model_validator(mode="after")
    def _one_reference(self) -> DesiredExercise:
        if (self.exercise_id is None) == (self.exercise is None):
            raise ValueError(
                "Each exercise needs exactly one of exercise_id (existing) or "
                "exercise (catalog name or id, for a new one)."
            )
        return self


class DesiredRoutine(BaseModel):
    name: str | None = None
    days: str | None = None
    goal: str | None = None
    note: str | None = None
    exercises: list[DesiredExercise] | None = None


class AddedSet(BaseModel):
    index: int
    reps: float
    weight_kg: float


class UpdatedSet(BaseModel):
    identifier: int
    index: int
    reps: float
    weight_kg: float


class ExerciseUpdate(BaseModel):
    identifier: int
    name: str
    changes: list[FieldChange]
    added_sets: list[AddedSet]
    updated_sets: list[UpdatedSet]
    removed_set_ids: list[int]


class AddedExercise(BaseModel):
    catalog_id: int
    name: str
    position: int
    rest_seconds: int
    note: str | None
    sets: list[SetSpec]


class RemovedExercise(BaseModel):
    identifier: int
    name: str


class ChangeSet(BaseModel):
    routine_identifier: int
    routine_name: str
    routine_changes: list[FieldChange]
    added: list[AddedExercise]
    removed: list[RemovedExercise]
    updated: list[ExerciseUpdate]
    final_order: list[str] | None
    warnings: list[str]

    @property
    def is_empty(self) -> bool:
        return not (
            self.routine_changes
            or self.added
            or self.removed
            or self.updated
            or self.final_order is not None
        )


def _clean(value: str) -> str | None:
    return value.strip() or None


def _routine_changes(
    current: Routine, desired: DesiredRoutine, problems: list[str]
) -> list[FieldChange]:
    changes: list[FieldChange] = []
    for field in ("name", "days", "goal", "note"):
        requested = getattr(desired, field)
        if requested is None:
            continue
        new = _clean(requested)
        if field == "name" and new is None:
            problems.append("Routine name must be non-empty.")
            continue
        old = getattr(current, field)
        if new != old:
            changes.append(FieldChange(field=field, old=old, new=new))
    return changes


def _exercise_update(ex: RoutineExercise, want: DesiredExercise) -> ExerciseUpdate | None:
    changes: list[FieldChange] = []
    if want.rest_seconds is not None and want.rest_seconds != ex.rest_seconds:
        changes.append(
            FieldChange(field="rest_seconds", old=str(ex.rest_seconds), new=str(want.rest_seconds))
        )
    if want.note is not None and _clean(want.note) != ex.note:
        changes.append(FieldChange(field="note", old=ex.note, new=_clean(want.note)))
    added: list[AddedSet] = []
    updated: list[UpdatedSet] = []
    removed: list[int] = []
    if want.sets is not None:
        current = ex.template_sets
        for i, s in enumerate(want.sets):
            if i >= len(current):
                added.append(AddedSet(index=i, reps=s.reps, weight_kg=s.weight_kg))
            elif (current[i].reps, current[i].weight_kg) != (s.reps, s.weight_kg):
                updated.append(
                    UpdatedSet(
                        identifier=current[i].identifier, index=i, reps=s.reps,
                        weight_kg=s.weight_kg,
                    )
                )
        removed = [s.identifier for s in current[len(want.sets) :]]
    if not (changes or added or updated or removed):
        return None
    return ExerciseUpdate(
        identifier=ex.identifier, name=ex.name, changes=changes, added_sets=added,
        updated_sets=updated, removed_set_ids=removed,
    )


def diff_routine(
    current: Routine, desired: DesiredRoutine, catalog: ExerciseCatalog
) -> ChangeSet:
    problems: list[str] = []
    warnings: list[str] = []
    routine_changes = _routine_changes(current, desired, problems)
    added: list[AddedExercise] = []
    removed: list[RemovedExercise] = []
    updated: list[ExerciseUpdate] = []
    final_order: list[str] | None = None

    if desired.exercises is not None:
        active = current.active_exercises()
        by_id = {e.identifier: e for e in active}
        if not desired.exercises:
            problems.append("A routine needs at least one exercise.")
        seen: set[int] = set()
        order: list[str] = []
        for pos, want in enumerate(desired.exercises):
            label = f"Exercise #{pos + 1}"
            if want.sets is not None and not want.sets:
                problems.append(f"{label}: sets must not be empty (omit sets to keep them).")
                continue
            if want.exercise_id is not None:
                ex = by_id.get(want.exercise_id)
                if ex is None:
                    problems.append(
                        f"{label}: exercise_id {want.exercise_id} is not an active exercise "
                        f"of {current.name!r}."
                    )
                    continue
                if ex.identifier in seen:
                    problems.append(f"{label}: exercise_id {ex.identifier} appears more than once.")
                    continue
                seen.add(ex.identifier)
                order.append(f"id:{ex.identifier}")
                upd = _exercise_update(ex, want)
                if upd is not None:
                    updated.append(upd)
                continue
            assert want.exercise is not None
            try:
                res = catalog.resolve(want.exercise)
            except UnresolvedExercise as exc:
                problems.append(f"{label}: {exc}")
                continue
            if res.fuzzy:
                warnings.append(
                    f"Fuzzy match: {res.input!r} → {res.resolved_name!r} "
                    f"(confidence {res.confidence})."
                )
            if not want.sets:
                warnings.append(
                    f"{res.resolved_name!r}: no sets given — defaulting to 1 set of "
                    f"{DEFAULT_SET.reps:g} reps (bodyweight)."
                )
            order.append(f"new:{len(added)}")
            added.append(
                AddedExercise(
                    catalog_id=res.z_pk, name=res.resolved_name, position=pos,
                    rest_seconds=want.rest_seconds or 0, note=_clean(want.note or ""),
                    sets=want.sets or [DEFAULT_SET],
                )
            )
        removed = [
            RemovedExercise(identifier=e.identifier, name=e.name)
            for e in active
            if e.identifier not in seen
        ]
        kept_in_current_order = [f"id:{e.identifier}" for e in active if e.identifier in seen]
        if order != kept_in_current_order:
            final_order = order

    if problems:
        raise DiffError("Rejected — nothing was changed:\n- " + "\n- ".join(problems))
    return ChangeSet(
        routine_identifier=current.identifier, routine_name=current.name,
        routine_changes=routine_changes, added=added, removed=removed, updated=updated,
        final_order=final_order, warnings=warnings,
    )


__all__ = [
    "DEFAULT_SET",
    "AddedExercise",
    "AddedSet",
    "ChangeSet",
    "DesiredExercise",
    "DesiredRoutine",
    "DiffError",
    "ExerciseUpdate",
    "RemovedExercise",
    "UpdatedSet",
    "diff_routine",
]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_diff.py -v && uv run mypy src && uv run ruff check src tests && uv run ruff format src tests`
Expected: 13 passed; mypy/ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/smartgym_mcp/diff.py tests/test_diff.py
git commit -m "Add desired-vs-current routine diff"
```

---

### Task 5: Create and archive payloads (`payloads.py`)

Encodes only the formats already captured (`routine/add/`, `routine/archive/`, `routine/unarchive/`). The `routine/update/` encoder is Plan 2 (needs S2).

**Files:**
- Create: `src/smartgym_mcp/payloads.py`
- Test: `tests/test_payloads.py`

**Interfaces:**
- Consumes: `catalog.CatalogExercise` (Task 3); `models.RoutineSpec`, `models.ExerciseSpec`, `models.SetSpec` (existing); `diff.DEFAULT_SET` (Task 4).
- Produces:
  - `mint_unique_hashid(now: datetime) -> int` (`YYMMDD` + 8 random digits)
  - `new_routine_payload(spec: RoutineSpec, exercises: Sequence[CatalogExercise], *, number: int, now: datetime, mint: Callable[[datetime], int] = mint_unique_hashid) -> dict[str, Any]` — `exercises[i]` is the resolved catalog entry for `spec.exercises[i]`
  - `add_routines_form(routines: Sequence[dict[str, Any]], *, timezone: str) -> dict[str, str]` → `{"routines": <JSON array>, "timezone": ...}`
  - `archive_form(routine_identifier: int) -> dict[str, str]` → `{"routinesIDs": "<id>"}`
  - `unarchive_form(routine_identifier: int) -> dict[str, str]` → `{"routineID": "<id>"}`
  - Common fields (`appVersion`, `authID`, `requestDate`) are NOT added here — `api/client.py` adds them.

Shape source: the app's own `routine/add/` capture (2026-10-05). Keys `identifier` / `routineID` are omitted for new objects; `requiresBands` = equipment `38` present; `isSingleWeight` = 0 — both validated live by Task 1 S6.

- [ ] **Step 1: Write the failing tests**

`tests/test_payloads.py`:

```python
"""payloads.py: routine/add/ JSON in the app's captured shape + archive forms."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone

from smartgym_mcp.catalog import CatalogExercise
from smartgym_mcp.models import ExerciseSpec, RoutineSpec, SetSpec
from smartgym_mcp.payloads import (
    add_routines_form,
    archive_form,
    mint_unique_hashid,
    new_routine_payload,
    unarchive_form,
)

MADRID = timezone(timedelta(hours=2))
NOW = datetime(2026, 10, 5, 13, 30, 15, tzinfo=MADRID)

PUSH_UP = CatalogExercise(
    id=194, name="Push Up", type=1, category=11, sub_categories="9,12", two_sides=0,
    stretch=0, equipment_ids=(), images=("0194-1", "0194-2", "", "", "", ""),
)
BAND = CatalogExercise(
    id=363, name="Resistance Band Pull Apart", type=0, category=5, sub_categories="101",
    two_sides=0, stretch=0, equipment_ids=("38",), images=("0363-1", "0363-2", "", "", "", ""),
)


def _counter() -> object:
    n = iter(range(1, 100))
    return lambda _now: 26100500000000 + next(n)


def test_new_routine_payload_matches_captured_shape() -> None:
    spec = RoutineSpec(
        name="ZZ-FB — Test",
        days="0001",
        note="note",
        exercises=[
            ExerciseSpec(exercise="Push Up", rest_seconds=60,
                         sets=[SetSpec(reps=10), SetSpec(reps=8.5, weight_kg=12.5)]),
            ExerciseSpec(exercise="band", note="per arm"),
        ],
    )
    p = new_routine_payload(spec, [PUSH_UP, BAND], number=21, now=NOW, mint=_counter())

    assert {k: p[k] for k in ("name", "days", "goal", "note", "number", "reference",
                              "hasSynced", "migratedSets", "uniqueHashID", "dateCreated")} == {
        "name": "ZZ-FB — Test", "days": "0001", "goal": None, "note": "note", "number": 21,
        "reference": 0, "hasSynced": 0, "migratedSets": 1, "uniqueHashID": 26100500000001,
        "dateCreated": "2026-10-04T22:00:00",  # local midnight, sent as UTC
    }
    assert "identifier" not in p

    push, band = p["exercises"]
    assert {k: push[k] for k in ("id", "genericID", "name", "idx", "index", "pause", "type",
                                 "category", "subCategories", "twoSides", "stretch",
                                 "isStretch", "isCustom", "requiresBands", "isSingleWeight",
                                 "listGroup", "mode", "totalSets", "totalRealSets",
                                 "firstImage", "sixthImage", "dateAdded")} == {
        "id": 194, "genericID": 194, "name": "Push Up", "idx": 0, "index": 0, "pause": 60,
        "type": 1, "category": 11, "subCategories": "9,12", "twoSides": 0, "stretch": 0,
        "isStretch": False, "isCustom": 0, "requiresBands": False, "isSingleWeight": 0,
        "listGroup": 0, "mode": 0, "totalSets": 2, "totalRealSets": 2,
        "firstImage": "0194-1", "sixthImage": "", "dateAdded": "2026-10-05T11:30:15",
    }
    assert "note" not in push and "identifier" not in push and "routineID" not in push
    assert push["sets"] == [
        {"index": 0, "type": 0, "firstValue": 1, "secondValue": 10, "thirdValue": 0,
         "uniqueHashID": 26100500000003, "dateAdded": "2026-10-05T11:30:15"},
        {"index": 1, "type": 0, "firstValue": 1, "secondValue": 8.5, "thirdValue": 12.5,
         "uniqueHashID": 26100500000004, "dateAdded": "2026-10-05T11:30:15"},
    ]
    assert band["requiresBands"] is True
    assert band["note"] == "per arm"
    assert band["pause"] == 0
    assert [(s["secondValue"], s["thirdValue"]) for s in band["sets"]] == [(10, 0)]


def test_add_routines_form_is_utf8_json() -> None:
    form = add_routines_form([{"name": "FB-A — Return W1"}], timezone="Europe/Madrid")
    assert form["timezone"] == "Europe/Madrid"
    assert json.loads(form["routines"]) == [{"name": "FB-A — Return W1"}]
    assert "—" in form["routines"]  # not —-escaped


def test_archive_forms() -> None:
    assert archive_form(3681004) == {"routinesIDs": "3681004"}
    assert unarchive_form(3681004) == {"routineID": "3681004"}


def test_mint_unique_hashid_shape() -> None:
    h = mint_unique_hashid(datetime(2026, 10, 5, tzinfo=UTC))
    assert str(h).startswith("261005") and len(str(h)) == 14
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_payloads.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'smartgym_mcp.payloads'`

- [ ] **Step 3: Implement `payloads.py`**

`src/smartgym_mcp/payloads.py`:

```python
"""Wire payloads for the SmartGym API (spec 04 §3) — create and archive.

Shapes mirror the app's own captured requests (2026-10-05, v8.0.3). New
objects carry only a client `uniqueHashID`; the server assigns identifiers
and returns the mapping. Common fields (appVersion, authID, requestDate) are
added by api/client.py, not here.
"""

from __future__ import annotations

import json
import random
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from .catalog import CatalogExercise
from .diff import DEFAULT_SET
from .models import RoutineSpec, SetSpec

_BAND_EQUIPMENT_ID = "38"


def mint_unique_hashid(now: datetime) -> int:
    return int(now.strftime("%y%m%d") + f"{random.randint(0, 99_999_999):08d}")


def _utc(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S")


def _num(value: float) -> float | int:
    return int(value) if float(value).is_integer() else value


def _set_payload(index: int, s: SetSpec, *, hashid: int, added: str) -> dict[str, Any]:
    return {
        "index": index,
        "type": 0,
        "firstValue": 1,
        "secondValue": _num(s.reps),
        "thirdValue": _num(s.weight_kg),
        "uniqueHashID": hashid,
        "dateAdded": added,
    }


def new_routine_payload(
    spec: RoutineSpec,
    exercises: Sequence[CatalogExercise],
    *,
    number: int,
    now: datetime,
    mint: Callable[[datetime], int] = mint_unique_hashid,
) -> dict[str, Any]:
    added = _utc(now)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    routine: dict[str, Any] = {
        "name": spec.name,
        "days": spec.days,
        "goal": spec.goal,
        "note": spec.note,
        "number": number,
        "reference": 0,
        "hasSynced": 0,
        "migratedSets": 1,
        "uniqueHashID": mint(now),
        "dateCreated": _utc(midnight),
        "exercises": [],
    }
    for i, (ex_spec, cat) in enumerate(zip(spec.exercises, exercises, strict=True)):
        sets = ex_spec.sets or [DEFAULT_SET]
        first, second, third, fourth, fifth, sixth = cat.images
        ex: dict[str, Any] = {
            "id": cat.id,
            "genericID": cat.id,
            "name": cat.name,
            "idx": i,
            "index": i,
            "pause": ex_spec.rest_seconds or 0,
            "type": cat.type,
            "category": cat.category,
            "subCategories": cat.sub_categories,
            "twoSides": cat.two_sides,
            "stretch": cat.stretch,
            "isStretch": cat.stretch == 1,
            "isCustom": 0,
            "requiresBands": _BAND_EQUIPMENT_ID in cat.equipment_ids,
            "isSingleWeight": 0,
            "listGroup": 0,
            "mode": 0,
            "totalSets": len(sets),
            "totalRealSets": len(sets),
            "firstImage": first,
            "secondImage": second,
            "thirdImage": third,
            "fourthImage": fourth,
            "fifthImage": fifth,
            "sixthImage": sixth,
            "uniqueHashID": mint(now),
            "dateAdded": added,
        }
        if ex_spec.note:
            ex["note"] = ex_spec.note
        ex["sets"] = [
            _set_payload(k, s, hashid=mint(now), added=added) for k, s in enumerate(sets)
        ]
        routine["exercises"].append(ex)
    return routine


def add_routines_form(routines: Sequence[dict[str, Any]], *, timezone: str) -> dict[str, str]:
    return {"routines": json.dumps(list(routines), ensure_ascii=False), "timezone": timezone}


def archive_form(routine_identifier: int) -> dict[str, str]:
    return {"routinesIDs": str(routine_identifier)}


def unarchive_form(routine_identifier: int) -> dict[str, str]:
    return {"routineID": str(routine_identifier)}


__all__ = [
    "add_routines_form",
    "archive_form",
    "mint_unique_hashid",
    "new_routine_payload",
    "unarchive_form",
]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_payloads.py -v && uv run mypy src && uv run ruff check src tests && uv run ruff format src tests`
Expected: 4 passed; mypy/ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/smartgym_mcp/payloads.py tests/test_payloads.py
git commit -m "Add routine/add and archive payload builders"
```

---

### Task 6: HTTP client core (`api/client.py`)

**Files:**
- Create: `src/smartgym_mcp/api/__init__.py` (empty docstring module)
- Create: `src/smartgym_mcp/api/client.py`
- Modify: `pyproject.toml` (add `"httpx>=0.27"` to `dependencies`)
- Test: `tests/test_api_client.py`

**Interfaces:**
- Produces:
  - `BASE_URL = "https://api.smartgymapp.com/v1.1/"`
  - `class CredentialProvider(Protocol)`: `def auth_headers(self) -> Mapping[str, str]`; `@property def user_id(self) -> str`
  - `class ApiError(RuntimeError)`: attribute `code: str | None`
  - `class AuthError(ApiError)`
  - `class WriteOutcomeUnknown(ApiError)` — network failure during a write; caller must re-fetch
  - `class ApiClient`: `__init__(self, credentials: CredentialProvider, *, app_version: str, base_url: str = BASE_URL, transport: httpx.BaseTransport | None = None, clock: Callable[[], datetime] = _utcnow, sleep: Callable[[float], None] = time.sleep, max_read_attempts: int = 3)`; `get(self, path: str, params: Mapping[str, str] | None = None, *, require_success: bool = True) -> dict[str, Any]`; `post(self, path: str, form: Mapping[str, str]) -> dict[str, Any]`; `close(self) -> None`
  - Every request carries `appVersion`, `authID`, `requestDate` (UTC `%Y-%m-%dT%H:%M:%S`); GET in the query, POST as multipart fields (UTF-8).
  - Reads retry on transport errors and HTTP 5xx (`max_read_attempts`, sleep `0.5 * 2**n`); writes never retry.
  - HTTP 401/403 → `AuthError`; other non-2xx → `ApiError`; non-JSON → `ApiError`; `code != "SUCCESS"` (when `require_success`) → `ApiError(code=...)`. No message ever includes header values.

- [ ] **Step 1: Write the failing tests**

`tests/test_api_client.py`:

```python
"""api/client.py on httpx.MockTransport — never touches the network."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime

import httpx
import pytest

from smartgym_mcp.api.client import ApiClient, ApiError, AuthError, WriteOutcomeUnknown

SECRET = "Bearer s3cr3t-token"
PHRASE = "ph-r4se-value"


class FakeCreds:
    def auth_headers(self) -> Mapping[str, str]:
        return {"Authorization": SECRET, "phrase": PHRASE}

    @property
    def user_id(self) -> str:
        return "1"


def _client(handler: Callable[[httpx.Request], httpx.Response], **kw: object) -> ApiClient:
    return ApiClient(
        FakeCreds(),
        app_version="8.0.3",
        transport=httpx.MockTransport(handler),
        clock=lambda: datetime(2026, 10, 5, 11, 30, 0, tzinfo=UTC),
        sleep=lambda _s: None,
        **kw,  # type: ignore[arg-type]
    )


def test_get_adds_common_params_and_auth_headers() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"code": "SUCCESS", "routines": []})

    body = _client(handler).get("routine/single/42/")
    assert body["code"] == "SUCCESS"
    req = seen[0]
    assert req.url.path == "/v1.1/routine/single/42/"
    assert req.url.params["appVersion"] == "8.0.3"
    assert req.url.params["authID"] == "1"
    assert req.url.params["requestDate"] == "2026-10-05T11:30:00"
    assert req.headers["Authorization"] == SECRET and req.headers["phrase"] == PHRASE


def test_post_is_utf8_multipart_with_common_fields() -> None:
    seen: list[bytes] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req.read())
        assert req.headers["content-type"].startswith("multipart/form-data")
        return httpx.Response(200, json={"code": "SUCCESS"})

    _client(handler).post("routine/archive/", {"routinesIDs": "7", "note": "FB-A — W1"})
    body = seen[0].decode("utf-8")
    for field in ('name="routinesIDs"', 'name="authID"', 'name="appVersion"',
                  'name="requestDate"'):
        assert field in body
    assert "FB-A — W1" in body


def test_http_200_with_error_code_raises() -> None:
    client = _client(lambda _r: httpx.Response(200, json={"code": "ROUTINE_NOT_FOUND"}))
    with pytest.raises(ApiError) as exc:
        client.post("routine/archive/", {"routinesIDs": "7"})
    assert exc.value.code == "ROUTINE_NOT_FOUND"


def test_require_success_false_returns_body_without_code() -> None:
    client = _client(lambda _r: httpx.Response(200, json={"options": []}))
    assert client.get("options.json", require_success=False) == {"options": []}


@pytest.mark.parametrize("status", [401, 403])
def test_auth_failures_raise_auth_error_without_secrets(status: int) -> None:
    client = _client(lambda _r: httpx.Response(status, text=f"denied {SECRET} {PHRASE}"))
    with pytest.raises(AuthError) as exc:
        client.get("user/info/1")
    assert SECRET not in str(exc.value) and PHRASE not in str(exc.value)


def test_reads_retry_on_5xx_then_succeed() -> None:
    calls = {"n": 0}

    def handler(_req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"code": "SUCCESS"})

    assert _client(handler).get("routine/all/1/")["code"] == "SUCCESS"
    assert calls["n"] == 3


def test_reads_give_up_after_max_attempts() -> None:
    calls = {"n": 0}

    def handler(_req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ConnectError("offline")

    with pytest.raises(ApiError, match="network"):
        _client(handler, max_read_attempts=2).get("routine/all/1/")
    assert calls["n"] == 2


def test_writes_never_retry_and_report_unknown_outcome() -> None:
    calls = {"n": 0}

    def handler(_req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ReadTimeout("slow")

    with pytest.raises(WriteOutcomeUnknown):
        _client(handler).post("routine/update/", {"routineID": "7"})
    assert calls["n"] == 1


def test_non_json_body_raises_api_error() -> None:
    client = _client(lambda _r: httpx.Response(200, text="<html>maintenance</html>"))
    with pytest.raises(ApiError, match="non-JSON"):
        client.get("routine/all/1/")


def test_client_repr_hides_credentials() -> None:
    client = _client(lambda _r: httpx.Response(200, json={"code": "SUCCESS"}))
    assert SECRET not in repr(client) and PHRASE not in repr(client)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_api_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'smartgym_mcp.api'`

- [ ] **Step 3: Add the dependency**

In `pyproject.toml` `dependencies`, add `"httpx>=0.27",` after `"pydantic>=2",`, then:

Run: `uv sync`
Expected: resolves without changes to installed httpx (0.28.1 already present via mcp).

- [ ] **Step 4: Implement the client**

`src/smartgym_mcp/api/__init__.py`:

```python
"""SmartGym backend client (spec 04)."""
```

`src/smartgym_mcp/api/client.py`:

```python
"""HTTP core for the SmartGym backend (spec 04 §5, §7).

Adds the common fields every app request carries, maps failures to typed
errors, retries reads only, and never lets credential values reach a message,
log line, or repr. Writes are never retried: after a network failure the
outcome is unknown and the caller must re-fetch before trying again.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx

logger = logging.getLogger("smartgym_mcp.api")

BASE_URL = "https://api.smartgymapp.com/v1.1/"
_TIMEOUT_S = 30.0


class CredentialProvider(Protocol):
    def auth_headers(self) -> Mapping[str, str]: ...

    @property
    def user_id(self) -> str: ...


class ApiError(RuntimeError):
    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


class AuthError(ApiError):
    pass


class WriteOutcomeUnknown(ApiError):
    pass


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ApiClient:
    def __init__(
        self,
        credentials: CredentialProvider,
        *,
        app_version: str,
        base_url: str = BASE_URL,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], datetime] = _utcnow,
        sleep: Callable[[float], None] = time.sleep,
        max_read_attempts: int = 3,
    ) -> None:
        self._credentials = credentials
        self._app_version = app_version
        self._clock = clock
        self._sleep = sleep
        self._max_read_attempts = max_read_attempts
        self._http = httpx.Client(base_url=base_url, transport=transport, timeout=_TIMEOUT_S)

    def __repr__(self) -> str:
        return f"ApiClient(base_url={str(self._http.base_url)!r})"

    def close(self) -> None:
        self._http.close()

    def _common(self) -> dict[str, str]:
        return {
            "appVersion": self._app_version,
            "authID": self._credentials.user_id,
            "requestDate": self._clock().astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S"),
        }

    def get(
        self,
        path: str,
        params: Mapping[str, str] | None = None,
        *,
        require_success: bool = True,
    ) -> dict[str, Any]:
        query = {**(params or {}), **self._common()}
        last: Exception | None = None
        for attempt in range(self._max_read_attempts):
            if attempt:
                self._sleep(0.5 * 2 ** (attempt - 1))
            try:
                resp = self._http.get(
                    path, params=query, headers=dict(self._credentials.auth_headers())
                )
            except httpx.TransportError as exc:
                last = exc
                logger.info("GET %s network error (attempt %d)", path, attempt + 1)
                continue
            if resp.status_code >= 500:
                last = ApiError(f"SmartGym server error HTTP {resp.status_code} on {path}.")
                continue
            return self._decode(path, resp, require_success)
        raise ApiError(
            f"SmartGym unreachable (network error) for {path} after "
            f"{self._max_read_attempts} attempts: {type(last).__name__}."
        )

    def post(self, path: str, form: Mapping[str, str]) -> dict[str, Any]:
        fields = {**form, **self._common()}
        files = {k: (None, v.encode("utf-8")) for k, v in fields.items()}
        try:
            resp = self._http.post(
                path, files=files, headers=dict(self._credentials.auth_headers())
            )
        except httpx.TransportError as exc:
            raise WriteOutcomeUnknown(
                f"Network error during write to {path} ({type(exc).__name__}); the change "
                "may or may not have landed — re-fetch before retrying."
            ) from None
        return self._decode(path, resp, require_success=True)

    @staticmethod
    def _decode(path: str, resp: httpx.Response, require_success: bool) -> dict[str, Any]:
        if resp.status_code in (401, 403):
            raise AuthError(
                f"SmartGym rejected the credentials (HTTP {resp.status_code}) on {path}. "
                "Refresh the SmartGym credentials and retry."
            )
        if not 200 <= resp.status_code < 300:
            raise ApiError(f"SmartGym answered HTTP {resp.status_code} on {path}.")
        try:
            body = resp.json()
        except ValueError:
            raise ApiError(f"SmartGym returned a non-JSON response on {path}.") from None
        if not isinstance(body, dict):
            raise ApiError(f"SmartGym returned an unexpected JSON shape on {path}.")
        code = body.get("code")
        if require_success and code != "SUCCESS":
            raise ApiError(f"SmartGym answered {code!r} on {path}.", code=str(code))
        return body


__all__ = [
    "BASE_URL",
    "ApiClient",
    "ApiError",
    "AuthError",
    "CredentialProvider",
    "WriteOutcomeUnknown",
]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_api_client.py -v && uv run pytest -q && uv run mypy src && uv run ruff check src tests && uv run ruff format src tests`
Expected: 11 new tests pass; full suite passes; mypy/ruff clean.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock src/smartgym_mcp/api tests/test_api_client.py
git commit -m "Add SmartGym API HTTP client core"
```

---

## After this plan

Plan 2 is written from Task 1's recorded facts and covers: `api/auth.py` (credential provider per S1), the `routine/update/` encoder in `payloads.py` (golden-tested against the S2 fixtures), `snapshots.py`, the API-backed read and write tools in `server.py` (spec §6), the S4 read fallback if needed, `smartgym_health` version warning, renaming `ExerciseResolution.z_pk` → `catalog_id`, retiring `lifecycle.py` / `writes.py` / publish + tombstones, and the live E2E pass on `ZZ-` routines.
