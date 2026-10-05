# API Client — Plan 2: API-backed tools with routine sections

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the DB-write MCP with tools that read and edit routines directly through SmartGym's server — including warm-up / main / cool-down sections — then retire the DB path and fix the six "Return" routines in place.

**Architecture:** Pure layers (`model.py` parse → `diff.py` plan → `payloads.py` encode) under a thin orchestration layer (`api/store.py` reads with a short cache, `service.py` writes with fetch-fresh → plan → snapshot → send → re-fetch → verify) and thin MCP tools in `server.py`. Credentials come from a 0600 file (`api/auth.py`). Every test runs offline on recorded fixtures; live checks happen only on `ZZ-` routines with the user.

**Tech Stack:** Python ≥ 3.11, pydantic 2, httpx 0.28 (`MockTransport` / fake clients in tests), FastMCP (`mcp[cli]`), pytest, ruff, mypy strict, uv, PyInstaller (`scripts/build.sh`). Spike tooling: mitmproxy local capture.

**Spec:** `docs/superpowers/specs/2026-10-05-api-client-design.md` (§3 verified facts + Phase 0 findings, §4 S1–S7, §5 architecture, §6 tool surface, §7 safety, §10 sections).

## Global Constraints

- Server base URL `https://api.smartgymapp.com/v1.1/`; every request carries `appVersion`, `authID`, `requestDate`; the app's `user-agent` / `accept` / `accept-language` headers are REQUIRED (CDN answers non-JSON 403 without them); `phrase` is not checked by the server and is never sent.
- Credential values (`Authorization`, account id) are never printed, logged, committed, or put in exception messages, reprs, or tool output. Anything that captures them is run by the user.
- Automated tests never touch the network, the SmartGym account, or the real DB.
- Live checks only on routines whose name starts with `ZZ-`; the user confirms on the iPhone. The six real "Return" routines are touched only in Task 12, one at a time, each after an explicit user approval of the dry run.
- Every write tool defaults to `dry_run=true`; apply = fetch fresh → plan → snapshot → send → re-fetch → verify (spec §5 invariant 2). Writes are never auto-resent.
- Only template sets (`dateLogged` empty) are ever changed. No routine delete (archive only).
- Sections: `listGroup` 1 = warm-up, 0 = main, 2 = cool-down; `idx` runs globally warm-up → main → cool-down (spec §10.1).
- Python `>=3.11`; mypy `strict = true` on `src`; ruff `line-length = 95`, rules `I, UP, B, SIM`. Commands: `uv run pytest`, `uv run ruff check src tests && uv run ruff format src tests`, `uv run mypy src`.
- Commit messages: imperative, no AI/assistant attribution lines.
- When a step says "append" to an existing test file, merge any import lines it shows into that file's top import block (ruff E402); `tests/fakes.py` is imported as `from fakes import FakeClient` (pytest puts `tests/` on the path).
- The publish/tombstone workaround is committed (`1dca9f9`) and is what Task 10 removes; git history keeps it.

## Review Focus

1. A routine the server returns with sections out of `idx` order (the Return routines: everything `listGroup` 0, or a warm-up drill with a higher `idx` than a main exercise) → read and edited in section order, never shuffled across sections (Task 3 + Task 4 tests).
2. An edit whose second or third request fails after the first succeeded → error names what was already sent and where the snapshot is; the same exception class (`WriteOutcomeUnknown` stays `WriteOutcomeUnknown`) reaches the caller (Task 8 test).
3. The server accepts a write but stores something else (different rest, missing exercise, set values rounded) → `WriteVerifyError` naming each differing field instead of "Applied" (Task 8 test).
4. Exercise added in the middle (end of warm-up) of a routine → existing exercises are renumbered in one follow-up order request with the new server id, not left with colliding `idx` (Task 4 + Task 5 + Task 8 tests).
5. Missing, world-readable, or incomplete credentials file → every tool answers with the file path and the fix (`chmod 600`, capture script), and `smartgym_health` reports it instead of the server failing to start (Task 2 + Task 10 tests).

## File map

| File | Responsibility | Task |
|---|---|---|
| `scripts/spike/*`, `tests/fixtures/api/update_*.json` | S7 capture + fixtures | 1 |
| `src/smartgym_mcp/api/auth.py` (new) | `FileCredentials`, `CredentialsError` | 2 |
| `scripts/capture_credentials.py` (new) | user-run mitmproxy addon writing the credentials file | 2 |
| `src/smartgym_mcp/config.py` | `credentials_path`, `VERIFIED_APP_VERSION`; DB fields removed in Task 10 | 2, 10 |
| `src/smartgym_mcp/catalog.py` | `installed_app_version`, `CatalogEquipment`, `load_bundle_equipment` | 2, 9 |
| `src/smartgym_mcp/model.py` | sections, logged sets, workouts, equipment lists, `AccountData` | 3 |
| `src/smartgym_mcp/diff.py` | three-section desired routine, moves, `RoutineView` for verification | 4, 6 |
| `src/smartgym_mcp/models.py` | input specs (`RoutineSpec` gains `warmup` / `cooldown`) | 5, 10 |
| `src/smartgym_mcp/payloads.py` | create with sections; `encode_change`, `order_form` | 5, 6 |
| `src/smartgym_mcp/api/client.py` | `ApiCalls` protocol, `user_id`, error-code passthrough | 7 |
| `src/smartgym_mcp/api/store.py` (new) | cached `history/all`, fresh `routine/single`, routine resolution | 7 |
| `src/smartgym_mcp/snapshots.py` (new) | pre-write routine snapshots | 7 |
| `src/smartgym_mcp/service.py` (new) | edit / create / archive orchestration + verification | 8 |
| `src/smartgym_mcp/builders.py` (new) | single-field tool inputs → `DesiredRoutine` | 8 |
| `src/smartgym_mcp/reads.py` (new) | read-tool views over `AccountData` | 9 |
| `src/smartgym_mcp/server.py` | thin MCP tools over store / service / reads | 10 |
| `db.py`, `lifecycle.py`, `writes.py`, `queries.py` + their tests | removed | 10 |
| `DESIGN.md`, `README.md`, `FEATURES.md`, `smartgym-mcp.spec` | docs + build | 10 |

---

### Task 1: S7 section-move capture + S1 token lifetime (user in the loop)

Throwaway investigation, same tooling as Plan 1 Task 1. Output: scrubbed fixtures for Task 6, verified facts in spec §10.3 / §4. No product code.

**Files:**
- Create: `tests/fixtures/api/update_move_to_warmup.json`, `update_move_to_cooldown.json`, `update_reorder_warmup.json`, `update_add_to_warmup.json`, `update_add_to_cooldown.json`, `update_remove_two.json`, `update_clear_note.json`
- Modify: `docs/superpowers/specs/2026-10-05-api-client-design.md` (§4 outcome table S1 lifetime + S7 row; §10.3)

**Interfaces:**
- Produces for Task 6: the fixtures above, each `{"method", "path", "query", "form", "response"}` (scrub.py format), and the spec §10.3 statement of how a section move is encoded.
- Produces for Task 5: whether `removeExercises` takes a comma list (`update_remove_two`), whether clearing a note sends `"note": ""` (`update_clear_note`), whether an exercise added mid-routine comes with `exercisesOrder` (`update_add_to_warmup`).

- [ ] **Step 1: S1 lifetime check (≥ 24 h after 2026-10-05 15:23 UTC)**

User runs:

```bash
uv run python scripts/spike/send.py "user/info/{authID}" --get --no-phrase
```

Expected: `200 code = SUCCESS …` → token survives a day; record "S1 lifetime: ≥ 24 h" in spec §4. Anything else (`401`/`403` JSON) → record it; Task 2's provider still works, but README must tell the user to re-run the capture when tools report `AuthError`.

- [ ] **Step 2: Re-enable capture (user)**

```bash
sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain <scratchpad>/mitmconf/mitmproxy-ca-cert.pem
SPIKE_SAVE_CREDENTIALS=1 mitmdump --mode local:SmartGym -s scripts/spike/capture.py --set flow_detail=0 --set confdir=<scratchpad>/mitmconf
```

Enable the Mitmproxy Redirector network extension if macOS asks. Relaunch SmartGym on the Mac.

- [ ] **Step 3: S7 edits (user, Mac app)**

Create `ZZ-S7` with warm-up: Shoulder Circling, Bridge; main: Push Up, Squat, Plank; cool-down: Cross Arm Stretch. Save. Then, Save after each, ~5 s apart:
1. move Push Up from main into warm-up (end);
2. move Bridge from warm-up to cool-down;
3. swap the two warm-up exercises;
4. add a new exercise at the END of warm-up (this is mid-routine globally);
5. add a new exercise at the end of cool-down;
6. give Squat the note "x", Save; then clear the note, Save;
7. remove two exercises in ONE save.

- [ ] **Step 4: Locate and scrub the records**

```bash
python3 -c "
import json
for i, r in enumerate(map(json.loads, open('scripts/spike/spike-flows.jsonl'))):
    if r['method'] == 'POST':
        print(i, r['t'][11:], r['path'], sorted(k for k in r['form'] if k not in ('appVersion','authID','requestDate','timezone')))
"
```

Map records to edits by order and content, then (example indices):

```bash
uv run python scripts/spike/scrub.py <i1> update_move_to_warmup
uv run python scripts/spike/scrub.py <i2> update_move_to_cooldown
uv run python scripts/spike/scrub.py <i3> update_reorder_warmup
uv run python scripts/spike/scrub.py <i4> update_add_to_warmup
uv run python scripts/spike/scrub.py <i5> update_add_to_cooldown
uv run python scripts/spike/scrub.py <i6b> update_clear_note
uv run python scripts/spike/scrub.py <i7> update_remove_two
grep -l "REDACTED" tests/fixtures/api/*.json; echo scan-done
```

Expected: 7 fixtures, `scan-done` with no file listed, no account id inside (scrub.py guarantees it).

- [ ] **Step 5: Record S7 facts**

In spec §10.3 replace the "Move / reorder" bullet with the observed encoding, e.g. "move = `updateExercises=[{"exerciseID":…,"listGroup":"1"}]` (+ `exercisesOrder` …)". Also record: `removeExercises` multi-id format, note-clear value, whether `update_add_to_warmup` carried `exercisesOrder`. Add the S1 lifetime result and an S7 outcome row to §4. If a move is NOT expressible in place (e.g. the app sent remove + add), write "no in-place move — §10.4 fallback applies".

- [ ] **Step 6: Clean up + commit**

User deletes `ZZ-S7` in-app, stops mitmdump, removes the CA trust (`sudo security remove-trusted-cert -d <pem>`).

```bash
git add tests/fixtures/api docs/superpowers/specs/2026-10-05-api-client-design.md
git commit -m "Record S7 section-move captures and S1 token lifetime"
```

---

### Task 2: Credentials file provider, app version, capture script

**Files:**
- Create: `src/smartgym_mcp/api/auth.py`
- Create: `scripts/capture_credentials.py`
- Modify: `src/smartgym_mcp/config.py` (add `credentials_path`, `VERIFIED_APP_VERSION`)
- Modify: `src/smartgym_mcp/catalog.py` (add `installed_app_version`)
- Test: `tests/test_api_auth.py`, `tests/test_capture_credentials.py`

**Interfaces:**
- Consumes: `api.client.CredentialProvider` protocol (Plan 1).
- Produces:
  - `config.Config.credentials_path: Path` (env `SMARTGYM_CREDENTIALS`, default `~/.smartgym-mcp/credentials.json`); `config.VERIFIED_APP_VERSION = "8.0.3"`
  - `class api.auth.CredentialsError(RuntimeError)`
  - `class api.auth.FileCredentials(path: Path)` implementing `auth_headers() -> Mapping[str, str]` (keys `Authorization`, `user-agent`, `accept`, `accept-language`; never `phrase`) and `user_id: str`
  - `catalog.installed_app_version(cfg: Config) -> str | None`
  - Credentials file format: `{"authorization": str, "authID": str, "app_headers": {"user-agent": str, "accept": str, "accept-language": str}}` (extra keys such as the spike's `phrase` / `requestDate` are ignored).

- [ ] **Step 1: Write the failing tests**

`tests/test_api_auth.py`:

```python
"""api/auth.py: credentials file → headers; secrets never leak into messages."""

from __future__ import annotations

import json
import plistlib
from dataclasses import replace
from pathlib import Path

import pytest

from smartgym_mcp.api.auth import CredentialsError, FileCredentials
from smartgym_mcp.catalog import installed_app_version
from smartgym_mcp.config import load_config

SECRET = "Bearer s3cr3t-token"
APP = {"user-agent": "SmartGym/8.0.3", "accept": "*/*", "accept-language": "en-GB"}


def _write(path: Path, data: object, mode: int = 0o600) -> Path:
    path.write_text(json.dumps(data), encoding="utf-8")
    path.chmod(mode)
    return path


def _good(tmp_path: Path) -> Path:
    return _write(
        tmp_path / "credentials.json",
        {"authorization": SECRET, "authID": "42", "phrase": "p", "app_headers": APP},
    )


def test_headers_carry_authorization_and_app_headers_without_phrase(tmp_path: Path) -> None:
    creds = FileCredentials(_good(tmp_path))
    headers = creds.auth_headers()
    assert headers["Authorization"] == SECRET
    assert {k: headers[k] for k in APP} == APP
    assert "phrase" not in {k.lower() for k in headers}
    assert creds.user_id == "42"


def test_missing_file_names_path_and_capture_script(tmp_path: Path) -> None:
    with pytest.raises(CredentialsError, match="capture_credentials.py") as exc:
        FileCredentials(tmp_path / "nope.json")
    assert "nope.json" in str(exc.value)


def test_group_or_world_readable_file_is_refused(tmp_path: Path) -> None:
    path = _good(tmp_path)
    path.chmod(0o644)
    with pytest.raises(CredentialsError, match="chmod 600") as exc:
        FileCredentials(path)
    assert SECRET not in str(exc.value)


@pytest.mark.parametrize("drop", ["authorization", "authID", "user-agent"])
def test_incomplete_file_names_the_missing_key(tmp_path: Path, drop: str) -> None:
    data: dict[str, object] = {"authorization": SECRET, "authID": "42", "app_headers": dict(APP)}
    if drop == "user-agent":
        del data["app_headers"]["user-agent"]  # type: ignore[attr-defined]
    else:
        del data[drop]
    with pytest.raises(CredentialsError, match=drop) as exc:
        FileCredentials(_write(tmp_path / "c.json", data))
    assert SECRET not in str(exc.value)


def test_invalid_json_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    path.write_text("{not json", encoding="utf-8")
    path.chmod(0o600)
    with pytest.raises(CredentialsError, match="not valid JSON"):
        FileCredentials(path)


def test_repr_hides_secret(tmp_path: Path) -> None:
    assert SECRET not in repr(FileCredentials(_good(tmp_path)))


def test_installed_app_version_reads_info_plist(tmp_path: Path) -> None:
    contents = tmp_path / "SmartGym.app" / "Contents"
    contents.mkdir(parents=True)
    with (contents / "Info.plist").open("wb") as f:
        plistlib.dump({"CFBundleShortVersionString": "8.0.3"}, f)
    cfg = replace(load_config(), app_bundle=tmp_path / "SmartGym.app")
    assert installed_app_version(cfg) == "8.0.3"
    assert installed_app_version(replace(cfg, app_bundle=tmp_path / "Missing.app")) is None


def test_credentials_path_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SMARTGYM_CREDENTIALS", str(tmp_path / "x.json"))
    assert load_config().credentials_path == (tmp_path / "x.json").resolve()
```

`tests/test_capture_credentials.py`:

```python
"""scripts/capture_credentials.py with a stub mitmproxy — no proxy, no network."""

from __future__ import annotations

import importlib.util
import json
import stat
import sys
import types
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "capture_credentials.py"
APP = {"user-agent": "SmartGym/8.0.3", "accept": "*/*", "accept-language": "en-GB"}


class _Headers(dict[str, str]):
    def get(self, key: str, default: Any = None) -> Any:  # type: ignore[override]
        return super().get(key.lower(), default)


class _Req:
    def __init__(self, headers: dict[str, str], query: dict[str, str]) -> None:
        self.pretty_host = "api.smartgymapp.com"
        self.headers = _Headers({k.lower(): v for k, v in headers.items()})
        self.query = query
        self.multipart_form: dict[bytes, bytes] = {}


class _Flow:
    def __init__(self, req: _Req) -> None:
        self.request = req


@pytest.fixture
def addon(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    mitm = types.ModuleType("mitmproxy")
    mitm.http = types.SimpleNamespace(HTTPFlow=object, Request=object)  # type: ignore[attr-defined]
    mitm.ctx = types.SimpleNamespace(log=types.SimpleNamespace(info=lambda _m: None))  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mitmproxy", mitm)
    monkeypatch.setenv("SMARTGYM_CREDENTIALS", str(tmp_path / "credentials.json"))
    spec = importlib.util.spec_from_file_location("capture_credentials", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_saves_complete_credentials_with_mode_600(addon: Any, tmp_path: Path) -> None:
    addon.request(_Flow(_Req({"Authorization": "Bearer t", **APP}, {"authID": "42"})))
    path = tmp_path / "credentials.json"
    assert json.loads(path.read_text()) == {
        "authorization": "Bearer t",
        "authID": "42",
        "app_headers": APP,
    }
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_ignores_requests_without_authorization(addon: Any, tmp_path: Path) -> None:
    addon.request(_Flow(_Req(dict(APP), {"authID": "42"})))
    assert not (tmp_path / "credentials.json").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_api_auth.py tests/test_capture_credentials.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'smartgym_mcp.api.auth'` and `FileNotFoundError` for the script.

- [ ] **Step 3: Implement**

`src/smartgym_mcp/config.py` — add the constant and the field (field LAST, with a default, so existing `Config(...)` calls keep working until Task 10):

```python
DEFAULT_CREDENTIALS = "~/.smartgym-mcp/credentials.json"
# SmartGym version whose wire format the API client was verified against (spec §4).
VERIFIED_APP_VERSION = "8.0.3"
```

```python
@dataclass(frozen=True)
class Config:
    db_path: Path
    app_bundle: Path
    backup_dir: Path
    allow_write_while_running: bool
    credentials_path: Path = field(default_factory=lambda: _resolve(DEFAULT_CREDENTIALS))
```

(add `field` to the `dataclasses` import) and in `load_config()` pass
`credentials_path=_resolve(os.environ.get("SMARTGYM_CREDENTIALS", DEFAULT_CREDENTIALS)),`.

`src/smartgym_mcp/catalog.py` — add `import plistlib` and:

```python
def installed_app_version(cfg: Config) -> str | None:
    """CFBundleShortVersionString of the installed SmartGym app, None if unreadable."""
    plist = cfg.app_bundle / "Contents" / "Info.plist"
    try:
        with plist.open("rb") as f:
            value = plistlib.load(f).get("CFBundleShortVersionString")
    except (OSError, plistlib.InvalidFileException):
        return None
    return str(value) if value else None
```

`src/smartgym_mcp/api/auth.py`:

```python
"""Credentials for the SmartGym backend (API-client spec §4 S1).

The server authenticates with the static `Authorization` header; the CDN in
front of it rejects requests without the app's own `user-agent` / `accept` /
`accept-language`. `phrase` is not checked and is never sent. Values come from
a 0600 JSON file the user creates with scripts/capture_credentials.py and are
never logged, printed, or put in exception messages.
"""

from __future__ import annotations

import json
import stat
from collections.abc import Mapping
from pathlib import Path

REQUIRED_APP_HEADERS = ("user-agent", "accept", "accept-language")
_HINT = "Create it with scripts/capture_credentials.py (README: 'Connecting to your account')."


class CredentialsError(RuntimeError):
    """Credentials file missing, unreadable, too permissive, or incomplete."""


class FileCredentials:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._headers, self._user_id = self._load()

    def __repr__(self) -> str:
        return f"FileCredentials(path={str(self._path)!r})"

    def _load(self) -> tuple[dict[str, str], str]:
        if not self._path.exists():
            raise CredentialsError(f"SmartGym credentials not found at {self._path}. {_HINT}")
        mode = stat.S_IMODE(self._path.stat().st_mode)
        if mode & 0o077:
            raise CredentialsError(
                f"{self._path} is readable by other users (mode {mode:o}); "
                f"run: chmod 600 {self._path}"
            )
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise CredentialsError(f"{self._path} is not valid JSON. {_HINT}") from None
        if not isinstance(raw, dict):
            raise CredentialsError(f"{self._path} is not valid JSON. {_HINT}")
        app = raw.get("app_headers") if isinstance(raw.get("app_headers"), dict) else {}
        missing = [k for k in ("authorization", "authID") if not raw.get(k)]
        missing += [f"app_headers.{h}" for h in REQUIRED_APP_HEADERS if not app.get(h)]
        if missing:
            raise CredentialsError(f"{self._path} lacks {', '.join(missing)}. {_HINT}")
        headers = {h: str(app[h]) for h in REQUIRED_APP_HEADERS}
        headers["Authorization"] = str(raw["authorization"])
        return headers, str(raw["authID"])

    def auth_headers(self) -> Mapping[str, str]:
        return dict(self._headers)

    @property
    def user_id(self) -> str:
        return self._user_id


__all__ = ["REQUIRED_APP_HEADERS", "CredentialsError", "FileCredentials"]
```

`scripts/capture_credentials.py`:

```python
"""mitmproxy addon that writes the SmartGym credentials file. RUN BY THE USER.

  mitmdump --mode local:SmartGym -s scripts/capture_credentials.py --set confdir=<CA dir>

then open SmartGym. The first request carrying an Authorization header is saved
to ~/.smartgym-mcp/credentials.json (override: SMARTGYM_CREDENTIALS), mode
0600: the Authorization value, the account id, and the app's user-agent /
accept / accept-language. Nothing else is stored; no value is printed.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from mitmproxy import ctx, http

CRED = Path(
    os.environ.get("SMARTGYM_CREDENTIALS", "~/.smartgym-mcp/credentials.json")
).expanduser()
APP_HEADERS = ("user-agent", "accept", "accept-language")
_saved = False


def _account(req: http.Request) -> str | None:
    if req.query.get("authID"):
        return str(req.query["authID"])
    try:
        value = req.multipart_form.get(b"authID")
    except ValueError:
        return None
    return value.decode() if value else None


def request(flow: http.HTTPFlow) -> None:
    global _saved
    req = flow.request
    if "smartgymapp.com" not in req.pretty_host:
        return
    auth = req.headers.get("authorization")
    account = _account(req)
    app = {h: req.headers.get(h) for h in APP_HEADERS}
    if not auth or not account or not all(app.values()):
        return
    CRED.parent.mkdir(parents=True, exist_ok=True)
    tmp = CRED.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"authorization": auth, "authID": account, "app_headers": app}, f)
    os.replace(tmp, CRED)
    if not _saved:
        _saved = True
        ctx.log.info(f"SmartGym credentials saved to {CRED} — you can stop mitmdump now.")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_api_auth.py tests/test_capture_credentials.py -v && uv run pytest -q && uv run mypy src && uv run ruff check src tests scripts && uv run ruff format src tests scripts`
Expected: 12 new tests pass; full suite passes; mypy/ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/smartgym_mcp/api/auth.py src/smartgym_mcp/config.py src/smartgym_mcp/catalog.py scripts/capture_credentials.py tests/test_api_auth.py tests/test_capture_credentials.py
git commit -m "Add file credentials provider, app version probe and capture script"
```

---

### Task 3: Model — sections, logged sets, workouts, account data

**Files:**
- Modify: `src/smartgym_mcp/model.py` (full replacement below)
- Create: `tests/fixtures/api/history_all_synthetic.json`
- Test: `tests/test_api_model.py` (extend)

**Interfaces:**
- Consumes: Plan 1 `model.py` (`ApiPayloadError`, `parse_routine`, `parse_routines_response`, coercion helpers).
- Produces (later tasks rely on these exact names):
  - `Section = Literal["warmup", "main", "cooldown"]`; `SECTIONS: tuple[Section, ...] = ("warmup", "main", "cooldown")`; `LIST_GROUP_BY_SECTION: dict[Section, int] = {"warmup": 1, "main": 0, "cooldown": 2}`
  - `TemplateSet`: + `date_added: str | None` (server format `"YYYY-MM-DD HH:MM:SS"`)
  - `LoggedSet(BaseModel)`: `identifier: int`, `index: int`, `reps: float`, `weight_kg: float`, `logged_at: str`
  - `RoutineExercise`: + `section: Section`, `logged_sets: list[LoggedSet]`
  - `Routine`: + `number: int`; `active_exercises()` sorts by (section rank, `index`); new `section(section: Section) -> list[RoutineExercise]`
  - `Workout(BaseModel)`: `identifier: int`, `routine_identifier: int | None`, `start: str`, `end: str | None`, `duration_s: int`, `calories: int | None`, `avg_hr: int | None`, `max_hr: int | None`, `set_ids: list[int]`
  - `EquipmentList(BaseModel)`: `identifier: int`, `name: str`, `selected: bool`, `equipment_ids: list[int]`, `dumbbell_weights: str | None`, `kettlebell_weights: str | None`
  - `AccountData(BaseModel)`: `routines: list[Routine]`, `workouts: list[Workout]`, `equipment_lists: list[EquipmentList]`, `last_modified: str | None`
  - `parse_history_all(raw: Mapping[str, Any]) -> AccountData` (raises `ApiPayloadError` on non-SUCCESS and on `hasMore` true)
  - Empty-string dates (`""`) count as absent everywhere (fixes Plan 1 deferred minor).

- [ ] **Step 1: Write the synthetic account fixture**

`tests/fixtures/api/history_all_synthetic.json` (shape of `history/all/<id>/`, values invented):

```json
{
  "code": "SUCCESS",
  "lastModified": "2026-10-05 15:56:48",
  "hasMore": false,
  "routines": [
    {
      "identifier": "3000001", "uniqueHashID": "26091100000001", "name": "ZZ-FB — Test",
      "days": "2,4,6", "goal": "", "note": "Routine note", "number": "18", "reference": "0",
      "dateArchived": null, "dateRemoved": null, "userID": "1",
      "exercises": [
        {"id": "400", "name": "Cross Arm Stretch", "identifier": "40000014", "uniqueHashID": "26091100000014",
         "idx": "4", "pause": "0", "note": null, "dateRemoved": null, "listGroup": "2",
         "sets": [{"identifier": "50000041", "uniqueHashID": "26091100000141", "firstValue": "1",
                   "secondValue": "30", "thirdValue": "0", "type": "0", "index": "0",
                   "dateAdded": "2026-09-11 08:34:19", "dateRemoved": null, "dateLogged": null}]},
        {"id": "300", "name": "Shoulder Circling", "identifier": "40000010", "uniqueHashID": "26091100000010",
         "idx": "7", "pause": "10", "note": "", "dateRemoved": null, "listGroup": "1",
         "sets": [{"identifier": "50000001", "uniqueHashID": "26091100000101", "firstValue": "1",
                   "secondValue": "10", "thirdValue": "0", "type": "0", "index": "0",
                   "dateAdded": "2026-09-11 08:34:19", "dateRemoved": null, "dateLogged": ""}]},
        {"id": "207", "name": "Cable Chest Press", "identifier": "40000012", "uniqueHashID": "26091100000012",
         "idx": "2", "pause": "90", "note": "Slow", "dateRemoved": null, "listGroup": "0",
         "sets": [
           {"identifier": "50000011", "uniqueHashID": "26091100000111", "firstValue": "1",
            "secondValue": "10", "thirdValue": "42.5", "type": "0", "index": "0",
            "dateAdded": "2026-09-11 08:34:19", "dateRemoved": null, "dateLogged": null},
           {"identifier": "50000021", "uniqueHashID": "26091100000121", "firstValue": "1",
            "secondValue": "10", "thirdValue": "40", "type": "0", "index": "0",
            "dateAdded": "2026-09-14 08:10:00", "dateRemoved": null, "dateLogged": "2026-09-14 08:10:00"},
           {"identifier": "50000022", "uniqueHashID": "26091100000122", "firstValue": "1",
            "secondValue": "8", "thirdValue": "42.5", "type": "0", "index": "1",
            "dateAdded": "2026-09-14 08:14:00", "dateRemoved": null, "dateLogged": "2026-09-14 08:14:00"},
           {"identifier": "50000023", "uniqueHashID": "26091100000123", "firstValue": "1",
            "secondValue": "10", "thirdValue": "42.5", "type": "0", "index": "0",
            "dateAdded": "2026-09-21 08:10:00", "dateRemoved": null, "dateLogged": "2026-09-21 08:10:00"}
         ]},
        {"id": "194", "name": "Push Up", "identifier": "40000013", "uniqueHashID": "26091100000013",
         "idx": "3", "pause": "60", "note": null, "dateRemoved": "2026-09-20 00:00:00", "listGroup": "0",
         "sets": []}
      ]
    },
    {
      "identifier": "3000002", "uniqueHashID": "26091100000002", "name": "ZZ-Old",
      "days": "", "goal": null, "note": null, "number": "12", "reference": "0",
      "dateArchived": "2026-09-30 10:00:00", "dateRemoved": null, "userID": "1", "exercises": []
    },
    {
      "identifier": "3000003", "uniqueHashID": "26091100000003", "name": "ZZ-Gone",
      "days": null, "goal": null, "note": null, "number": "11", "reference": "0",
      "dateArchived": "", "dateRemoved": "2026-09-01 10:00:00", "userID": "1", "exercises": []
    }
  ],
  "histories": [
    {"identifier": "7000001", "dateAdded": "2026-09-14 09:00:00", "dateRemoved": null,
     "routine": {"identifier": "3000001", "source": "cloud",
                 "exercises": [{"identifier": "40000012",
                                "sets": [{"identifier": "50000021"}, {"identifier": "50000022"}]}]},
     "workout": {"startDate": "2026-09-14 08:00:00", "endDate": "2026-09-14 09:00:00",
                 "duration": "3600", "calories": "350", "averageHeartRate": "120",
                 "maxHeartRate": "160", "minHeartRate": "80", "distance": "0"}},
    {"identifier": "7000002", "dateAdded": "2026-09-21 09:00:00", "dateRemoved": null,
     "routine": {"identifier": "3000001", "source": "cloud",
                 "exercises": [{"identifier": "40000012", "sets": [{"identifier": "50000023"}]}]},
     "workout": {"startDate": "2026-09-21 08:00:00", "endDate": "", "duration": "2700.5",
                 "calories": "", "averageHeartRate": "131.6", "maxHeartRate": "",
                 "minHeartRate": "", "distance": "0"}},
    {"identifier": "7000003", "dateAdded": "2026-09-22 09:00:00", "dateRemoved": "2026-09-22 10:00:00",
     "routine": null,
     "workout": {"startDate": "2026-09-22 08:00:00", "endDate": null, "duration": "60",
                 "calories": "1", "averageHeartRate": "1", "maxHeartRate": "1",
                 "minHeartRate": "1", "distance": "0"}}
  ],
  "equipmentLists": [
    {"identifier": "200109", "name": "Equipment", "selectedEquipment": "1,2,38",
     "dumbbellWeights": "", "kettlebellWeights": "8,12", "lastModified": "2026-04-30 14:51:28",
     "isSelected": "1"}
  ],
  "goal": {"week": {"goal": 3}},
  "customExercises": []
}
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_api_model.py` (and add `parse_history_all` to its import line):

```python
SYNTH = Path(__file__).parent / "fixtures" / "api" / "history_all_synthetic.json"


def _account() -> dict:
    return json.loads(SYNTH.read_text(encoding="utf-8"))


def test_sections_map_from_list_group_and_order_active_exercises() -> None:
    data = parse_history_all(_account())
    routine = data.routines[0]
    assert [(e.section, e.name) for e in routine.active_exercises()] == [
        ("warmup", "Shoulder Circling"),
        ("main", "Cable Chest Press"),
        ("cooldown", "Cross Arm Stretch"),
    ]
    assert [e.name for e in routine.section("cooldown")] == ["Cross Arm Stretch"]


def test_unknown_list_group_is_rejected_naming_the_value() -> None:
    raw = _account()["routines"][0]
    raw["exercises"][0]["listGroup"] = "7"
    with pytest.raises(ApiPayloadError, match="listGroup.*7"):
        parse_routine(raw)


def test_logged_sets_are_separated_from_template_sets() -> None:
    chest = next(e for e in parse_history_all(_account()).routines[0].exercises if e.catalog_id == 207)
    assert [(s.identifier, s.reps, s.weight_kg) for s in chest.template_sets] == [
        (50000011, 10.0, 42.5)
    ]
    assert chest.template_sets[0].date_added == "2026-09-11 08:34:19"
    assert [s.identifier for s in chest.logged_sets] == [50000021, 50000022, 50000023]
    assert chest.logged_sets[0].logged_at == "2026-09-14 08:10:00"


def test_empty_string_dates_count_as_absent() -> None:
    data = parse_history_all(_account())
    circling = next(e for e in data.routines[0].exercises if e.catalog_id == 300)
    assert len(circling.template_sets) == 1 and circling.logged_sets == []
    gone = next(r for r in data.routines if r.name == "ZZ-Gone")
    assert not gone.archived and gone.removed
    assert next(r for r in data.routines if r.name == "ZZ-Old").archived


def test_workouts_parse_numbers_and_skip_removed_histories() -> None:
    data = parse_history_all(_account())
    assert [w.identifier for w in data.workouts] == [7000001, 7000002]
    first, second = data.workouts
    assert (first.routine_identifier, first.duration_s, first.calories) == (3000001, 3600, 350)
    assert (first.avg_hr, first.max_hr, first.end) == (120, 160, "2026-09-14 09:00:00")
    assert first.set_ids == [50000021, 50000022]
    assert (second.duration_s, second.calories, second.avg_hr, second.max_hr, second.end) == (
        2700, None, 132, None, None
    )


def test_equipment_lists_parse() -> None:
    (eq,) = parse_history_all(_account()).equipment_lists
    assert (eq.identifier, eq.selected, eq.equipment_ids) == (200109, True, [1, 2, 38])
    assert (eq.dumbbell_weights, eq.kettlebell_weights) == (None, "8,12")


def test_has_more_is_refused_rather_than_truncated() -> None:
    raw = _account()
    raw["hasMore"] = True
    with pytest.raises(ApiPayloadError, match="hasMore"):
        parse_history_all(raw)


def test_history_non_success_raises() -> None:
    with pytest.raises(ApiPayloadError, match="INVALID_TOKEN"):
        parse_history_all({"code": "INVALID_TOKEN"})
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_api_model.py -v`
Expected: FAIL — `ImportError: cannot import name 'parse_history_all'`.

- [ ] **Step 4: Replace `src/smartgym_mcp/model.py`**

```python
"""Server-side data model (API-client spec §3, §10), parsed from SmartGym API JSON.

The API sends every scalar as a string ("pause": "10"), mixes template, logged
and removed sets in one `sets[]`, and marks absent dates as null or "". Parsing
coerces and filters once, here, so nothing downstream sees raw API strings.
Sections: listGroup 1 = warm-up, 0 = main, 2 = cool-down (spec §10.1).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel

Section = Literal["warmup", "main", "cooldown"]
SECTIONS: tuple[Section, ...] = ("warmup", "main", "cooldown")
LIST_GROUP_BY_SECTION: dict[Section, int] = {"warmup": 1, "main": 0, "cooldown": 2}
_SECTION_BY_LIST_GROUP: dict[int, Section] = {g: s for s, g in LIST_GROUP_BY_SECTION.items()}


class ApiPayloadError(ValueError):
    """The server answered with an unexpected shape or a non-SUCCESS code."""


class TemplateSet(BaseModel):
    identifier: int
    unique_hashid: int
    index: int
    reps: float
    weight_kg: float
    date_added: str | None


class LoggedSet(BaseModel):
    identifier: int
    index: int
    reps: float
    weight_kg: float
    logged_at: str


class RoutineExercise(BaseModel):
    identifier: int
    unique_hashid: int
    catalog_id: int
    name: str
    section: Section
    index: int
    rest_seconds: int
    note: str | None
    removed: bool
    template_sets: list[TemplateSet]
    logged_sets: list[LoggedSet]


class Routine(BaseModel):
    identifier: int
    unique_hashid: int
    name: str
    days: str | None
    goal: str | None
    note: str | None
    number: int
    archived: bool
    removed: bool
    exercises: list[RoutineExercise]

    def active_exercises(self) -> list[RoutineExercise]:
        return sorted(
            (e for e in self.exercises if not e.removed),
            key=lambda e: (SECTIONS.index(e.section), e.index),
        )

    def section(self, section: Section) -> list[RoutineExercise]:
        return [e for e in self.active_exercises() if e.section == section]


class Workout(BaseModel):
    identifier: int
    routine_identifier: int | None
    start: str
    end: str | None
    duration_s: int
    calories: int | None
    avg_hr: int | None
    max_hr: int | None
    set_ids: list[int]


class EquipmentList(BaseModel):
    identifier: int
    name: str
    selected: bool
    equipment_ids: list[int]
    dumbbell_weights: str | None
    kettlebell_weights: str | None


class AccountData(BaseModel):
    routines: list[Routine]
    workouts: list[Workout]
    equipment_lists: list[EquipmentList]
    last_modified: str | None


def _present(value: Any) -> bool:
    return value is not None and str(value).strip() != ""


def _req(raw: Mapping[str, Any], key: str) -> Any:
    if key not in raw or raw[key] is None:
        raise ApiPayloadError(f"API object is missing required field {key!r}.")
    return raw[key]


def _float_value(key: str, value: Any) -> float:
    try:
        return float(str(value))
    except ValueError:
        raise ApiPayloadError(f"API field {key!r} = {value!r} is not a number.") from None


def _int_value(key: str, value: Any) -> int:
    number = _float_value(key, value)
    if not number.is_integer():
        raise ApiPayloadError(f"API field {key!r} = {value!r} is not a whole number.")
    return int(number)


def _int(raw: Mapping[str, Any], key: str) -> int:
    return _int_value(key, _req(raw, key))


def _float(raw: Mapping[str, Any], key: str) -> float:
    return _float_value(key, _req(raw, key))


def _opt_round(raw: Mapping[str, Any], key: str) -> int | None:
    value = raw.get(key)
    return round(_float_value(key, value)) if _present(value) else None


def _text(value: Any) -> str | None:
    return str(value) if _present(value) else None


def _section(raw: Mapping[str, Any]) -> Section:
    group = _int(raw, "listGroup")
    if group not in _SECTION_BY_LIST_GROUP:
        raise ApiPayloadError(f"API field 'listGroup' = {group!r} is not a known section.")
    return _SECTION_BY_LIST_GROUP[group]


def _parse_exercise(raw: Mapping[str, Any]) -> RoutineExercise:
    template: list[TemplateSet] = []
    logged: list[LoggedSet] = []
    for s in raw.get("sets") or []:
        if _present(s.get("dateRemoved")):
            continue
        if _present(s.get("dateLogged")):
            logged.append(
                LoggedSet(
                    identifier=_int(s, "identifier"),
                    index=_int(s, "index"),
                    reps=_float(s, "secondValue"),
                    weight_kg=_float(s, "thirdValue"),
                    logged_at=str(s["dateLogged"]),
                )
            )
        else:
            template.append(
                TemplateSet(
                    identifier=_int(s, "identifier"),
                    unique_hashid=_int(s, "uniqueHashID"),
                    index=_int(s, "index"),
                    reps=_float(s, "secondValue"),
                    weight_kg=_float(s, "thirdValue"),
                    date_added=_text(s.get("dateAdded")),
                )
            )
    return RoutineExercise(
        identifier=_int(raw, "identifier"),
        unique_hashid=_int(raw, "uniqueHashID"),
        catalog_id=_int(raw, "id"),
        name=str(_req(raw, "name")),
        section=_section(raw),
        index=_int(raw, "idx"),
        rest_seconds=_int_value("pause", raw.get("pause") or 0),
        note=_text(raw.get("note")),
        removed=_present(raw.get("dateRemoved")),
        template_sets=sorted(template, key=lambda s: s.index),
        logged_sets=sorted(logged, key=lambda s: (s.logged_at, s.index)),
    )


def parse_routine(raw: Mapping[str, Any]) -> Routine:
    return Routine(
        identifier=_int(raw, "identifier"),
        unique_hashid=_int(raw, "uniqueHashID"),
        name=str(_req(raw, "name")),
        days=_text(raw.get("days")),
        goal=_text(raw.get("goal")),
        note=_text(raw.get("note")),
        number=_int_value("number", raw.get("number") or 0),
        archived=_present(raw.get("dateArchived")),
        removed=_present(raw.get("dateRemoved")),
        exercises=[_parse_exercise(e) for e in raw.get("exercises") or []],
    )


def _check_success(raw: Mapping[str, Any]) -> None:
    code = raw.get("code")
    if code != "SUCCESS":
        raise ApiPayloadError(f"SmartGym API answered {code!r} instead of SUCCESS.")


def parse_routines_response(raw: Mapping[str, Any]) -> list[Routine]:
    _check_success(raw)
    return [parse_routine(r) for r in raw.get("routines") or []]


def _parse_workout(raw: Mapping[str, Any]) -> Workout:
    workout = raw.get("workout") or {}
    routine = raw.get("routine") or {}
    set_ids = [
        _int(s, "identifier")
        for e in routine.get("exercises") or []
        for s in e.get("sets") or []
    ]
    duration = workout.get("duration")
    return Workout(
        identifier=_int(raw, "identifier"),
        routine_identifier=_int(routine, "identifier") if routine.get("identifier") else None,
        start=str(_req(workout, "startDate")),
        end=_text(workout.get("endDate")),
        duration_s=int(_float_value("duration", duration)) if _present(duration) else 0,
        calories=_opt_round(workout, "calories"),
        avg_hr=_opt_round(workout, "averageHeartRate"),
        max_hr=_opt_round(workout, "maxHeartRate"),
        set_ids=set_ids,
    )


def _parse_equipment_list(raw: Mapping[str, Any]) -> EquipmentList:
    selected = str(raw.get("selectedEquipment") or "")
    return EquipmentList(
        identifier=_int(raw, "identifier"),
        name=str(raw.get("name") or ""),
        selected=str(raw.get("isSelected") or "0") == "1",
        equipment_ids=[_int_value("selectedEquipment", x) for x in selected.split(",") if x],
        dumbbell_weights=_text(raw.get("dumbbellWeights")),
        kettlebell_weights=_text(raw.get("kettlebellWeights")),
    )


def parse_history_all(raw: Mapping[str, Any]) -> AccountData:
    _check_success(raw)
    if str(raw.get("hasMore", False)).lower() in ("true", "1"):
        raise ApiPayloadError(
            "SmartGym returned a partial history (hasMore=true); paging is not supported, "
            "so nothing is shown rather than an incomplete picture."
        )
    return AccountData(
        routines=[parse_routine(r) for r in raw.get("routines") or []],
        workouts=[
            _parse_workout(h)
            for h in raw.get("histories") or []
            if not _present(h.get("dateRemoved"))
        ],
        equipment_lists=[_parse_equipment_list(e) for e in raw.get("equipmentLists") or []],
        last_modified=_text(raw.get("lastModified")),
    )


__all__ = [
    "LIST_GROUP_BY_SECTION",
    "SECTIONS",
    "AccountData",
    "ApiPayloadError",
    "EquipmentList",
    "LoggedSet",
    "Routine",
    "RoutineExercise",
    "Section",
    "TemplateSet",
    "Workout",
    "parse_history_all",
    "parse_routine",
    "parse_routines_response",
]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_api_model.py -v && uv run pytest -q --ignore=tests/test_diff.py && uv run mypy src && uv run ruff check src tests && uv run ruff format src tests`
Expected: all model tests pass (Plan 1's 13 + 8 new). `tests/test_diff.py` now fails while building `RoutineExercise` without `section` / `logged_sets`; it is rewritten in Task 4, so the suite check for this task is `uv run pytest -q --ignore=tests/test_diff.py` → all pass. mypy clean (`diff.py` / `payloads.py` only read model fields).

- [ ] **Step 6: Commit**

```bash
git add src/smartgym_mcp/model.py tests/test_api_model.py tests/fixtures/api/history_all_synthetic.json
git commit -m "Parse routine sections, logged sets, workouts and equipment from history/all"
```

---

### Task 4: Change calculation with sections (`diff.py`)

**Files:**
- Modify: `src/smartgym_mcp/diff.py` (full replacement below)
- Test: `tests/test_diff.py` (full replacement below)

**Interfaces:**
- Consumes: `model.Routine`, `RoutineExercise`, `Section`, `SECTIONS` (Task 3); `matching.ExerciseCatalog`, `UnresolvedExercise`; `models.ExerciseResolution` (field `z_pk` = catalog id until Task 10 renames it), `FieldChange`, `SetSpec`.
- Produces (replaces Plan 1's `diff.py` interface):
  - `DesiredExercise` (unchanged), `DEFAULT_SET`, `DiffError`, `AddedSet`, `UpdatedSet`, `RemovedExercise` (unchanged)
  - `DesiredRoutine`: `name / days / goal / note: str | None`, `warmup / main / cooldown: list[DesiredExercise] | None`; method `section_list(section: Section) -> list[DesiredExercise] | None`. (Plan 1's `exercises` field is gone.)
  - `ExerciseUpdate`: + `new_section: Section | None` (a move also appears as `FieldChange("section", old, new)` in `changes`)
  - `AddedExercise`: + `section: Section`; `position` = global index in the final order
  - `ExerciseView(BaseModel)`: `catalog_id: int`, `section: Section`, `rest_seconds: int`, `note: str | None`, `sets: list[tuple[float, float]]` (reps, kg rounded to 3 decimals)
  - `RoutineView(BaseModel)`: `name: str`, `days / goal / note: str | None`, `exercises: list[ExerciseView]` (global order)
  - `view_of(routine: Routine) -> RoutineView`
  - `ChangeSet`: + `expected: RoutineView` (what the routine must look like after a successful apply); + `order: list[str]` (the full final order, always set); `final_order: list[str] | None` is set only when the relative order of kept exercises changes OR an exercise is added before the last kept one (keys `"id:<identifier>"` / `"new:<index into added>"`)
  - `diff_routine(current: Routine, desired: DesiredRoutine, catalog: ExerciseCatalog) -> ChangeSet`

- [ ] **Step 1: Replace `tests/test_diff.py` with the failing tests**

```python
"""diff.py: (current routine, desired three-section routine) → ChangeSet. Pure, no I/O."""

from __future__ import annotations

import pytest

from smartgym_mcp.diff import (
    DesiredExercise,
    DesiredRoutine,
    DiffError,
    diff_routine,
    view_of,
)
from smartgym_mcp.matching import ExerciseCatalog
from smartgym_mcp.model import Routine, RoutineExercise, TemplateSet
from smartgym_mcp.models import SetSpec

CATALOG = ExerciseCatalog(
    [
        (194, "Push Up"),
        (20, "Plank"),
        (207, "Cable Chest Press"),
        (300, "Shoulder Circling"),
        (400, "Cross Arm Stretch"),
    ]
)
LAYOUT = {"warmup": [10], "main": [11, 12, 13], "cooldown": [14]}


def _sets(*pairs: tuple[float, float], base: int) -> list[TemplateSet]:
    return [
        TemplateSet(
            identifier=base + i, unique_hashid=base + i, index=i, reps=r, weight_kg=w,
            date_added=None,
        )
        for i, (r, w) in enumerate(pairs)
    ]


def _ex(
    ident: int, cat: int, name: str, section: str, idx: int, sets: list[TemplateSet],
    note: str | None = None,
) -> RoutineExercise:
    return RoutineExercise(
        identifier=ident, unique_hashid=ident, catalog_id=cat, name=name, section=section,
        index=idx, rest_seconds=60, note=note, removed=False, template_sets=sets,
        logged_sets=[],
    )


def _routine() -> Routine:
    return Routine(
        identifier=1, unique_hashid=1, name="ZZ-R", days="2,4,6", goal=None, note="old",
        number=1, archived=False, removed=False,
        exercises=[
            _ex(10, 300, "Shoulder Circling", "warmup", 0, _sets((10, 0), base=100)),
            _ex(11, 207, "Cable Chest Press", "main", 1, _sets((12, 20), base=110)),
            _ex(12, 207, "Cable Chest Press", "main", 2,
                _sets((10, 40), (10, 40), (8, 42.5), base=200)),
            _ex(13, 194, "Push Up", "main", 3, _sets((15, 0), base=300), note="Slow"),
            _ex(14, 400, "Cross Arm Stretch", "cooldown", 4, _sets((30, 0), base=400)),
        ],
    )


def _same(**over: DesiredExercise) -> dict[str, list[DesiredExercise]]:
    return {
        s: [over.get(f"e{i}", DesiredExercise(exercise_id=i)) for i in ids]
        for s, ids in LAYOUT.items()
    }


def _ids(*idents: int) -> list[DesiredExercise]:
    return [DesiredExercise(exercise_id=i) for i in idents]


def test_nothing_requested_is_empty_and_expected_equals_current() -> None:
    cs = diff_routine(_routine(), DesiredRoutine(), CATALOG)
    assert cs.is_empty
    assert cs.expected == view_of(_routine())


def test_listing_every_section_unchanged_is_empty() -> None:
    assert diff_routine(_routine(), DesiredRoutine(**_same()), CATALOG).is_empty


def test_routine_fields_change_and_empty_string_clears() -> None:
    cs = diff_routine(_routine(), DesiredRoutine(name="ZZ-R2", note="", days="2,4,6"), CATALOG)
    assert [(c.field, c.old, c.new) for c in cs.routine_changes] == [
        ("name", "ZZ-R", "ZZ-R2"),
        ("note", "old", None),
    ]
    assert (cs.expected.name, cs.expected.note, cs.expected.days) == ("ZZ-R2", None, "2,4,6")


def test_empty_name_rejected() -> None:
    with pytest.raises(DiffError, match="name must be non-empty"):
        diff_routine(_routine(), DesiredRoutine(name="  "), CATALOG)


def test_reorder_within_main() -> None:
    cs = diff_routine(_routine(), DesiredRoutine(main=_ids(13, 11, 12)), CATALOG)
    assert cs.final_order == ["id:10", "id:13", "id:11", "id:12", "id:14"]
    assert not (cs.added or cs.removed or cs.updated)


def test_omitted_from_given_section_is_removed_and_other_sections_kept() -> None:
    cs = diff_routine(_routine(), DesiredRoutine(main=_ids(11, 13)), CATALOG)
    assert [(r.identifier, r.name) for r in cs.removed] == [(12, "Cable Chest Press")]
    assert cs.final_order is None
    assert not cs.updated


def test_move_main_to_warmup_listing_only_warmup() -> None:
    cs = diff_routine(_routine(), DesiredRoutine(warmup=_ids(10, 13)), CATALOG)
    (upd,) = cs.updated
    assert (upd.identifier, upd.new_section) == (13, "warmup")
    assert [(c.field, c.old, c.new) for c in upd.changes] == [("section", "main", "warmup")]
    assert cs.removed == []
    assert cs.final_order == ["id:10", "id:13", "id:11", "id:12", "id:14"]
    assert [e.section for e in cs.expected.exercises] == [
        "warmup", "warmup", "main", "main", "cooldown"
    ]


def test_move_keeping_relative_order_needs_no_order_request() -> None:
    cs = diff_routine(_routine(), DesiredRoutine(warmup=_ids(10, 11)), CATALOG)
    (upd,) = cs.updated
    assert (upd.identifier, upd.new_section) == (11, "warmup")
    assert cs.final_order is None


def test_add_new_exercise_at_end_of_cooldown() -> None:
    plank = DesiredExercise(exercise="plank", rest_seconds=30, sets=[SetSpec(reps=30)])
    cs = diff_routine(
        _routine(), DesiredRoutine(cooldown=[DesiredExercise(exercise_id=14), plank]), CATALOG
    )
    (added,) = cs.added
    assert (added.catalog_id, added.name, added.section, added.position, added.rest_seconds) == (
        20, "Plank", "cooldown", 5, 30
    )
    assert cs.final_order is None


def test_add_at_end_of_warmup_is_mid_routine_and_needs_order() -> None:
    cs = diff_routine(
        _routine(),
        DesiredRoutine(warmup=[DesiredExercise(exercise_id=10), DesiredExercise(exercise="Plank")]),
        CATALOG,
    )
    assert cs.added[0].position == 1
    assert cs.final_order == ["id:10", "new:0", "id:11", "id:12", "id:13", "id:14"]


def test_new_exercise_without_sets_gets_default_set_and_warning() -> None:
    wanted = _same()
    wanted["main"].append(DesiredExercise(exercise="Plank"))
    cs = diff_routine(_routine(), DesiredRoutine(**wanted), CATALOG)
    assert [(s.reps, s.weight_kg) for s in cs.added[0].sets] == [(10, 0.0)]
    assert any("default" in w for w in cs.warnings)


def test_duplicate_catalog_exercise_matched_by_identifier() -> None:
    working = DesiredExercise(exercise_id=12, rest_seconds=120)
    cs = diff_routine(_routine(), DesiredRoutine(**_same(e12=working)), CATALOG)
    (upd,) = cs.updated
    assert upd.identifier == 12
    assert [(c.field, c.old, c.new) for c in upd.changes] == [("rest_seconds", "60", "120")]
    assert cs.expected.exercises[2].rest_seconds == 120
    assert cs.expected.exercises[2].sets == [(10.0, 40.0), (10.0, 40.0), (8.0, 42.5)]


def test_sets_update_add_and_remove_by_position() -> None:
    sets = [SetSpec(reps=10, weight_kg=40), SetSpec(reps=10, weight_kg=45)]
    cs = diff_routine(
        _routine(), DesiredRoutine(**_same(e12=DesiredExercise(exercise_id=12, sets=sets))), CATALOG
    )
    (upd,) = cs.updated
    assert [(u.identifier, u.index, u.reps, u.weight_kg) for u in upd.updated_sets] == [
        (201, 1, 10, 45)
    ]
    assert upd.removed_set_ids == [202]
    assert upd.added_sets == []

    more = [SetSpec(reps=15), SetSpec(reps=12)]
    cs = diff_routine(
        _routine(), DesiredRoutine(**_same(e13=DesiredExercise(exercise_id=13, sets=more))), CATALOG
    )
    (upd,) = cs.updated
    assert [(a.index, a.reps) for a in upd.added_sets] == [(1, 12)]
    assert upd.updated_sets == [] and upd.removed_set_ids == []


def test_set_index_gaps_are_renumbered_so_added_sets_never_collide() -> None:
    routine = _routine()
    routine.exercises[2].template_sets = [
        TemplateSet(identifier=200 + i, unique_hashid=200 + i, index=idx, reps=10,
                    weight_kg=40, date_added=None)
        for i, idx in enumerate((0, 2, 3))
    ]
    want = DesiredExercise(exercise_id=12, sets=[SetSpec(reps=10, weight_kg=40)] * 4)
    (upd,) = diff_routine(routine, DesiredRoutine(**_same(e12=want)), CATALOG).updated
    assert [(u.identifier, u.index) for u in upd.updated_sets] == [(201, 1), (202, 2)]
    assert [a.index for a in upd.added_sets] == [3]


def test_exercise_note_change_and_clear() -> None:
    cs = diff_routine(
        _routine(),
        DesiredRoutine(**_same(
            e13=DesiredExercise(exercise_id=13, note=""),
            e10=DesiredExercise(exercise_id=10, note="Fast"),
        )),
        CATALOG,
    )
    changes = {u.identifier: [(c.field, c.old, c.new) for c in u.changes] for u in cs.updated}
    assert changes == {10: [("note", None, "Fast")], 13: [("note", "Slow", None)]}


def test_empty_sets_list_rejected() -> None:
    with pytest.raises(DiffError, match="sets must not be empty"):
        diff_routine(
            _routine(),
            DesiredRoutine(**_same(e13=DesiredExercise(exercise_id=13, sets=[]))),
            CATALOG,
        )


def test_all_problems_reported_together() -> None:
    wanted = DesiredRoutine(
        warmup=_ids(11),
        main=[*_ids(999), DesiredExercise(exercise="Zercher squat"), *_ids(11)],
    )
    with pytest.raises(DiffError) as exc:
        diff_routine(_routine(), wanted, CATALOG)
    msg = str(exc.value)
    assert "999" in msg and "Zercher" in msg and "more than once" in msg


def test_empty_routine_rejected() -> None:
    with pytest.raises(DiffError, match="at least one exercise"):
        diff_routine(_routine(), DesiredRoutine(warmup=[], main=[], cooldown=[]), CATALOG)


def test_desired_exercise_needs_exactly_one_reference() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        DesiredExercise()
    with pytest.raises(ValueError, match="exactly one"):
        DesiredExercise(exercise_id=1, exercise="Plank")


def test_float_noise_from_server_is_not_a_change() -> None:
    routine = _routine()
    routine.exercises[1].template_sets = _sets((12.300000190734863, 20), base=110)
    want = DesiredExercise(exercise_id=11, sets=[SetSpec(reps=12.3, weight_kg=20)])
    assert diff_routine(routine, DesiredRoutine(**_same(e11=want)), CATALOG).is_empty


def test_section_order_wins_over_inconsistent_server_indexes() -> None:
    routine = _routine()
    routine.exercises[0].index = 9  # warm-up drill numbered after main (Return routines)
    assert [e.identifier for e in routine.active_exercises()] == [10, 11, 12, 13, 14]
    assert diff_routine(routine, DesiredRoutine(**_same()), CATALOG).is_empty
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_diff.py -v`
Expected: FAIL — `ImportError: cannot import name 'view_of' from 'smartgym_mcp.diff'`.

- [ ] **Step 3: Replace `src/smartgym_mcp/diff.py`**

```python
"""Desired-vs-current routine diff (API-client spec §5, §10). Pure — no I/O, no wire format.

Tools describe a *desired* routine as three ordered sections (warm-up, main,
cool-down); this module turns it into one ChangeSet that payloads.py encodes.
Exercises are matched by server identifier only, so a routine holding the same
catalog exercise twice stays unambiguous. A section left as None keeps its
current members; a given list defines its section completely, so an existing
exercise listed under another section is a move and one listed nowhere (and
not in a kept section) is removed. Sets are matched by position: overlap →
update, extra desired → add, extra current → remove; kept sets are renumbered
0..n-1. Reps/weights compare at 3 decimals. Validation is all-or-nothing.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field, model_validator

from .matching import ExerciseCatalog, UnresolvedExercise
from .model import SECTIONS, Routine, RoutineExercise, Section
from .models import ExerciseResolution, FieldChange, SetSpec

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
    warmup: list[DesiredExercise] | None = None
    main: list[DesiredExercise] | None = None
    cooldown: list[DesiredExercise] | None = None

    def section_list(self, section: Section) -> list[DesiredExercise] | None:
        value: list[DesiredExercise] | None = getattr(self, section)
        return value


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
    new_section: Section | None
    changes: list[FieldChange]
    added_sets: list[AddedSet]
    updated_sets: list[UpdatedSet]
    removed_set_ids: list[int]


class AddedExercise(BaseModel):
    catalog_id: int
    name: str
    section: Section
    position: int
    rest_seconds: int
    note: str | None
    sets: list[SetSpec]


class RemovedExercise(BaseModel):
    identifier: int
    name: str


class ExerciseView(BaseModel):
    catalog_id: int
    section: Section
    rest_seconds: int
    note: str | None
    sets: list[tuple[float, float]]


class RoutineView(BaseModel):
    name: str
    days: str | None
    goal: str | None
    note: str | None
    exercises: list[ExerciseView]


class ChangeSet(BaseModel):
    routine_identifier: int
    routine_name: str
    routine_changes: list[FieldChange]
    added: list[AddedExercise]
    removed: list[RemovedExercise]
    updated: list[ExerciseUpdate]
    order: list[str]
    final_order: list[str] | None
    warnings: list[str]
    expected: RoutineView

    @property
    def is_empty(self) -> bool:
        return not (
            self.routine_changes
            or self.added
            or self.removed
            or self.updated
            or self.final_order is not None
        )


@dataclass(frozen=True)
class _Slot:
    existing: RoutineExercise | None
    want: DesiredExercise | None
    resolution: ExerciseResolution | None


def _r3(value: float) -> float:
    return round(float(value), 3)


def _clean(value: str) -> str | None:
    return value.strip() or None


def view_of(routine: Routine) -> RoutineView:
    return RoutineView(
        name=routine.name,
        days=routine.days,
        goal=routine.goal,
        note=routine.note,
        exercises=[
            ExerciseView(
                catalog_id=e.catalog_id,
                section=e.section,
                rest_seconds=e.rest_seconds,
                note=e.note,
                sets=[(_r3(s.reps), _r3(s.weight_kg)) for s in e.template_sets],
            )
            for e in routine.active_exercises()
        ],
    )


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


def _set_edits(
    ex: RoutineExercise, sets: list[SetSpec]
) -> tuple[list[AddedSet], list[UpdatedSet], list[int]]:
    current = ex.template_sets
    added: list[AddedSet] = []
    updated: list[UpdatedSet] = []
    for i, s in enumerate(sets):
        if i >= len(current):
            added.append(AddedSet(index=i, reps=s.reps, weight_kg=s.weight_kg))
            continue
        cur = current[i]
        if (cur.index, _r3(cur.reps), _r3(cur.weight_kg)) != (i, _r3(s.reps), _r3(s.weight_kg)):
            updated.append(
                UpdatedSet(identifier=cur.identifier, index=i, reps=s.reps, weight_kg=s.weight_kg)
            )
    return added, updated, [s.identifier for s in current[len(sets) :]]


def _exercise_update(
    ex: RoutineExercise, want: DesiredExercise | None, section: Section
) -> ExerciseUpdate | None:
    changes: list[FieldChange] = []
    new_section = section if section != ex.section else None
    if new_section is not None:
        changes.append(FieldChange(field="section", old=ex.section, new=section))
    added: list[AddedSet] = []
    updated: list[UpdatedSet] = []
    removed: list[int] = []
    if want is not None:
        if want.rest_seconds is not None and want.rest_seconds != ex.rest_seconds:
            changes.append(
                FieldChange(
                    field="rest_seconds", old=str(ex.rest_seconds), new=str(want.rest_seconds)
                )
            )
        if want.note is not None and _clean(want.note) != ex.note:
            changes.append(FieldChange(field="note", old=ex.note, new=_clean(want.note)))
        if want.sets is not None:
            added, updated, removed = _set_edits(ex, want.sets)
    if not (changes or added or updated or removed):
        return None
    return ExerciseUpdate(
        identifier=ex.identifier,
        name=ex.name,
        new_section=new_section,
        changes=changes,
        added_sets=added,
        updated_sets=updated,
        removed_set_ids=removed,
    )


def _expected_existing(
    ex: RoutineExercise, want: DesiredExercise | None, section: Section
) -> ExerciseView:
    rest = ex.rest_seconds
    note = ex.note
    sets = [(_r3(s.reps), _r3(s.weight_kg)) for s in ex.template_sets]
    if want is not None:
        if want.rest_seconds is not None:
            rest = want.rest_seconds
        if want.note is not None:
            note = _clean(want.note)
        if want.sets is not None:
            sets = [(_r3(s.reps), _r3(s.weight_kg)) for s in want.sets]
    return ExerciseView(
        catalog_id=ex.catalog_id, section=section, rest_seconds=rest, note=note, sets=sets
    )


def _claims(
    current: Routine,
    given: dict[Section, list[DesiredExercise] | None],
    by_id: dict[int, RoutineExercise],
    problems: list[str],
) -> dict[int, Section]:
    claimed: dict[int, Section] = {}
    for section in SECTIONS:
        for pos, want in enumerate(given[section] or []):
            if want.exercise_id is None:
                continue
            label = f"{section} #{pos + 1}"
            if want.exercise_id not in by_id:
                problems.append(
                    f"{label}: exercise_id {want.exercise_id} is not an active exercise "
                    f"of {current.name!r}."
                )
            elif want.exercise_id in claimed:
                problems.append(
                    f"{label}: exercise_id {want.exercise_id} appears more than once."
                )
            else:
                claimed[want.exercise_id] = section
    return claimed


def _slots(
    section: Section,
    wanted: list[DesiredExercise] | None,
    current: Routine,
    claimed: dict[int, Section],
    catalog: ExerciseCatalog,
    problems: list[str],
    warnings: list[str],
) -> list[_Slot]:
    if wanted is None:
        return [
            _Slot(e, None, None) for e in current.section(section) if e.identifier not in claimed
        ]
    by_id = {e.identifier: e for e in current.active_exercises()}
    slots: list[_Slot] = []
    for pos, want in enumerate(wanted):
        label = f"{section} #{pos + 1}"
        if want.sets is not None and not want.sets:
            problems.append(f"{label}: sets must not be empty (omit sets to keep them).")
            continue
        if want.exercise_id is not None:
            if claimed.get(want.exercise_id) == section:
                slots.append(_Slot(by_id[want.exercise_id], want, None))
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
        slots.append(_Slot(None, want, res))
    return slots


def diff_routine(
    current: Routine, desired: DesiredRoutine, catalog: ExerciseCatalog
) -> ChangeSet:
    problems: list[str] = []
    warnings: list[str] = []
    routine_changes = _routine_changes(current, desired, problems)
    active = current.active_exercises()
    by_id = {e.identifier: e for e in active}
    given = {s: desired.section_list(s) for s in SECTIONS}
    claimed = _claims(current, given, by_id, problems)
    slots = {
        s: _slots(s, given[s], current, claimed, catalog, problems, warnings) for s in SECTIONS
    }
    if any(v is not None for v in given.values()) and not any(slots.values()):
        problems.append("A routine needs at least one exercise.")
    if problems:
        raise DiffError("Rejected — nothing was changed:\n- " + "\n- ".join(problems))

    added: list[AddedExercise] = []
    updated: list[ExerciseUpdate] = []
    expected: list[ExerciseView] = []
    order: list[str] = []
    for section in SECTIONS:
        for slot in slots[section]:
            if slot.existing is not None:
                upd = _exercise_update(slot.existing, slot.want, section)
                if upd is not None:
                    updated.append(upd)
                order.append(f"id:{slot.existing.identifier}")
                expected.append(_expected_existing(slot.existing, slot.want, section))
                continue
            assert slot.want is not None and slot.resolution is not None
            sets = slot.want.sets or [DEFAULT_SET]
            new = AddedExercise(
                catalog_id=slot.resolution.z_pk,
                name=slot.resolution.resolved_name,
                section=section,
                position=len(order),
                rest_seconds=slot.want.rest_seconds or 0,
                note=_clean(slot.want.note or ""),
                sets=sets,
            )
            order.append(f"new:{len(added)}")
            added.append(new)
            expected.append(
                ExerciseView(
                    catalog_id=new.catalog_id,
                    section=section,
                    rest_seconds=new.rest_seconds,
                    note=new.note,
                    sets=[(_r3(s.reps), _r3(s.weight_kg)) for s in sets],
                )
            )

    kept = [k for k in order if k.startswith("id:")]
    current_kept = [f"id:{e.identifier}" for e in active if f"id:{e.identifier}" in kept]
    appended_only = all(a.position >= len(kept) for a in added)
    final_order = None if kept == current_kept and appended_only else order
    removed = [
        RemovedExercise(identifier=e.identifier, name=e.name)
        for e in active
        if f"id:{e.identifier}" not in kept
    ]
    fields = {c.field: c.new for c in routine_changes}

    def pick(field: str, old: str | None) -> str | None:
        return fields[field] if field in fields else old

    return ChangeSet(
        routine_identifier=current.identifier,
        routine_name=current.name,
        routine_changes=routine_changes,
        added=added,
        removed=removed,
        updated=updated,
        order=order,
        final_order=final_order,
        warnings=warnings,
        expected=RoutineView(
            name=pick("name", current.name) or current.name,
            days=pick("days", current.days),
            goal=pick("goal", current.goal),
            note=pick("note", current.note),
            exercises=expected,
        ),
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
    "ExerciseView",
    "RemovedExercise",
    "RoutineView",
    "UpdatedSet",
    "diff_routine",
    "view_of",
]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_diff.py -v && uv run pytest -q && uv run mypy src && uv run ruff check src tests && uv run ruff format src tests`
Expected: 22 passed; full suite passes; mypy/ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/smartgym_mcp/diff.py tests/test_diff.py
git commit -m "Diff routines by warm-up / main / cool-down sections"
```

---

### Task 5: Payloads — create with sections, edit encoders (`payloads.py`)

**Files:**
- Modify: `src/smartgym_mcp/models.py` (`RoutineSpec.warmup` / `cooldown`; `ExerciseSpec.sets` min length 1)
- Modify: `src/smartgym_mcp/payloads.py` (full replacement below)
- Test: `tests/test_payloads.py` (extend)

**Interfaces:**
- Consumes: `diff.ChangeSet`, `ExerciseUpdate`, `DEFAULT_SET` (Task 4); `model.Routine`, `TemplateSet`, `Section`, `LIST_GROUP_BY_SECTION` (Task 3); `catalog.CatalogExercise` (Plan 1).
- Produces:
  - `models.RoutineSpec`: + `warmup: list[ExerciseSpec]` (default `[]`), `cooldown: list[ExerciseSpec]` (default `[]`); `exercises` = main
  - `Mint = Callable[[datetime], int]`; `mint_unique_hashid(now) -> int`; `local_timezone_name() -> str` (IANA name from `/etc/localtime`, `"UTC"` fallback)
  - `routine_entries(spec: RoutineSpec) -> list[tuple[Section, ExerciseSpec]]` (warm-up, main, cool-down order)
  - `exercise_payload(cat, *, section, idx, rest_seconds, note, sets, now, mint, routine_identifier: int | None = None) -> dict[str, Any]`
  - `new_routine_payload(spec, exercises: Sequence[CatalogExercise], *, number, now, mint=mint_unique_hashid) -> dict[str, Any]` — `exercises[i]` is the catalog entry of `routine_entries(spec)[i]`
  - `@dataclass(frozen=True) EncodedChange`: `structure: dict[str, str] | None` (→ `routine/update/`), `exercise_edits: dict[str, str] | None` (→ `routine/updateExercise/`), `added_hashids: list[int]` (client `uniqueHashID` per `ChangeSet.added`, in order)
  - `encode_change(cs, current, catalog: Mapping[int, CatalogExercise], *, timezone, now, mint=mint_unique_hashid) -> EncodedChange`
  - `order_form(cs, current, added_ids: Sequence[int], *, timezone) -> dict[str, str] | None` (`added_ids[k]` = server id of `cs.added[k]`; None when `cs.final_order` is None)
  - `add_routines_form`, `archive_form`, `unarchive_form` unchanged.
  - Wire rules (from the S2 fixtures): routine-level forms always carry `routineID`, `timezone`, and the routine's (new or current) `days` / `goal` when not empty; a cleared field is sent as `""`; JSON fields are compact (`separators=(",", ":")`), UTF-8.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_payloads.py` (extend its imports as shown):

```python
from pathlib import Path

import pytest
from pydantic import ValidationError

from smartgym_mcp.diff import DesiredExercise, DesiredRoutine, diff_routine
from smartgym_mcp.matching import ExerciseCatalog
from smartgym_mcp.model import Routine, RoutineExercise, TemplateSet
from smartgym_mcp.payloads import encode_change, local_timezone_name, order_form

FIX = Path(__file__).parent / "fixtures" / "api"
JSON_FIELDS = {"exercises", "updateExercises", "routines"}
TZ = "Europe/Madrid"
ABDOMINAL = CatalogExercise(
    id=22, name="Abdominal 4 points Drawing In", type=1, category=1, sub_categories="",
    two_sides=0, stretch=0, equipment_ids=(), images=("0022-1", "0022-2", "", "", "", ""),
)
BUNDLE = {22: ABDOMINAL}
CATALOG = ExerciseCatalog(
    [(194, "Push Up"), (20, "Plank"), (207, "Cable Chest Press"), (256, "Ab Machine"),
     (22, "Abdominal 4 points Drawing In")]
)


def _norm(form: dict[str, str]) -> dict[str, object]:
    return {k: json.loads(v) if k in JSON_FIELDS else v for k, v in form.items()}


def _fixture_form(name: str) -> dict[str, object]:
    form = json.loads((FIX / f"{name}.json").read_text(encoding="utf-8"))["form"]
    return _norm({k: v for k, v in form.items() if k not in ("authID", "appVersion", "requestDate")})


def _ex(ident: int, cat: int, name: str, idx: int, *, rest: int = 15,
        sets: tuple[TemplateSet, ...] = ()) -> RoutineExercise:
    return RoutineExercise(
        identifier=ident, unique_hashid=ident, catalog_id=cat, name=name, section="main",
        index=idx, rest_seconds=rest, note=None, removed=False, template_sets=list(sets),
        logged_sets=[],
    )


def _routine(*exs: RoutineExercise, name: str = "ZZ-SPIKE2", days: str | None = "2,4,6",
             goal: str | None = "changed goal", note: str | None = None) -> Routine:
    return Routine(
        identifier=3681209, unique_hashid=26100512239894, name=name, days=days, goal=goal,
        note=note, number=17, archived=False, removed=False, exercises=list(exs),
    )


SPIKE = (
    _ex(34048391, 194, "Push Up", 0),
    _ex(34048392, 20, "Plank", 1),
    _ex(34048393, 207, "Cable Chest Press", 2),
)


def _ts(ident: int, hashid: int, idx: int, reps: float, kg: float, added: str) -> TemplateSet:
    return TemplateSet(identifier=ident, unique_hashid=hashid, index=idx, reps=reps,
                       weight_kg=kg, date_added=added)


AB3 = (
    _ts(149091969, 26100557160269, 0, 15, 36, "2026-10-05 15:16:23"),
    _ts(149091970, 26100549229340, 1, 15, 36, "2026-10-05 15:16:23"),
    _ts(149091971, 26100526419289, 2, 15, 36, "2026-10-05 15:16:23"),
)
AB_FOURTH = _ts(149092034, 26100568239109, 3, 10, 32, "2026-10-05 15:17:10")


def _encode(current: Routine, desired: DesiredRoutine, mint=None):  # type: ignore[no-untyped-def]
    cs = diff_routine(current, desired, CATALOG)
    enc = encode_change(cs, current, BUNDLE, timezone=TZ, now=NOW, mint=mint or _counter())
    return cs, enc


def _main(*items: DesiredExercise | int) -> DesiredRoutine:
    return DesiredRoutine(
        main=[i if isinstance(i, DesiredExercise) else DesiredExercise(exercise_id=i) for i in items]
    )


def test_update_rest_matches_capture() -> None:
    rest = DesiredExercise(exercise_id=34048393, rest_seconds=30)
    _, enc = _encode(_routine(*SPIKE), _main(34048391, 34048392, rest))
    assert _norm(enc.structure or {}) == _fixture_form("update_rest")
    assert enc.exercise_edits is None


def test_reorder_goes_to_order_form_matching_capture() -> None:
    cs, enc = _encode(_routine(*SPIKE), _main(34048393, 34048391, 34048392))
    assert enc.structure is None and enc.exercise_edits is None
    assert order_form(cs, _routine(*SPIKE), [], timezone=TZ) == _fixture_form("update_reorder")


def test_remove_exercise_matches_capture() -> None:
    extra = _ex(34048426, 22, "Abdominal 4 points Drawing In", 3)
    cs, enc = _encode(_routine(*SPIKE, extra), _main(34048391, 34048392, 34048393))
    assert _norm(enc.structure or {}) == _fixture_form("update_remove_exercise")
    assert cs.final_order is None


@pytest.mark.parametrize(
    ("fixture", "before", "desired"),
    [
        ("update_rename", {"name": "ZZ-SPIKE", "days": None, "goal": "asd"},
         DesiredRoutine(name="ZZ-SPIKE2")),
        ("update_days", {"days": None, "goal": "asd"}, DesiredRoutine(days="2,4,6")),
        ("update_goal", {"goal": "asd"}, DesiredRoutine(goal="changed goal")),
        ("update_note", {}, DesiredRoutine(note="changed routine note")),
    ],
)
def test_routine_field_edits_match_capture(
    fixture: str, before: dict[str, str | None], desired: DesiredRoutine
) -> None:
    _, enc = _encode(_routine(*SPIKE, **before), desired)  # type: ignore[arg-type]
    assert _norm(enc.structure or {}) == _fixture_form(fixture)


def test_exercise_note_goes_to_update_exercise_matching_capture() -> None:
    note = DesiredExercise(exercise_id=34048393, note="cahnge note")
    _, enc = _encode(_routine(*SPIKE), _main(34048391, 34048392, note))
    assert enc.structure is None
    assert _norm(enc.exercise_edits or {}) == _fixture_form("update_exercise_note")


def test_add_set_matches_capture() -> None:
    current = _routine(_ex(34048425, 256, "Ab Machine", 0, sets=AB3))
    sets = [SetSpec(reps=15, weight_kg=36)] * 3 + [SetSpec(reps=10, weight_kg=32)]
    _, enc = _encode(
        current, _main(DesiredExercise(exercise_id=34048425, sets=sets)),
        mint=lambda _now: 26100568239109,
    )
    assert _norm(enc.exercise_edits or {}) == _fixture_form("update_add_set")


def test_change_set_matches_capture() -> None:
    current = _routine(_ex(34048425, 256, "Ab Machine", 0, sets=(*AB3, AB_FOURTH)))
    sets = [SetSpec(reps=15, weight_kg=36)] * 2 + [
        SetSpec(reps=5, weight_kg=6), SetSpec(reps=10, weight_kg=32)
    ]
    _, enc = _encode(current, _main(DesiredExercise(exercise_id=34048425, sets=sets)))
    assert _norm(enc.exercise_edits or {}) == _fixture_form("update_change_set")


def test_remove_set_matches_capture() -> None:
    third = _ts(149091971, 26100526419289, 2, 5, 6, "2026-10-05 15:16:23")
    current = _routine(_ex(34048425, 256, "Ab Machine", 0, sets=(*AB3[:2], third, AB_FOURTH)))
    sets = [SetSpec(reps=15, weight_kg=36)] * 2 + [SetSpec(reps=5, weight_kg=6)]
    _, enc = _encode(current, _main(DesiredExercise(exercise_id=34048425, sets=sets)))
    assert _norm(enc.exercise_edits or {}) == _fixture_form("update_remove_set")


def test_add_exercise_matches_capture_shape() -> None:
    new = DesiredExercise(exercise="Abdominal 4 points Drawing In", rest_seconds=0,
                          sets=[SetSpec(reps=1)])
    cs, enc = _encode(_routine(*SPIKE), _main(34048391, 34048392, 34048393, new))
    app = _fixture_form("update_add_exercise")
    ours = _norm(enc.structure or {})
    assert {k: v for k, v in ours.items() if k != "exercises"} == {
        k: v for k, v in app.items() if k != "exercises"
    }
    (app_ex,) = app["exercises"]  # type: ignore[misc]
    (our_ex,) = ours["exercises"]  # type: ignore[misc]
    assert set(app_ex) - set(our_ex) == {"identifier"}
    assert set(our_ex) - set(app_ex) <= {"subCategories", "mode"}  # both accepted in S6
    for key in ("id", "genericID", "name", "idx", "index", "pause", "listGroup", "routineID",
                "type", "category", "isCustom", "isSingleWeight", "requiresBands", "isStretch",
                "stretch", "twoSides", "firstImage", "secondImage"):
        assert our_ex[key] == app_ex[key], key
    assert set(our_ex["sets"][0]) == set(app_ex["sets"][0])
    assert enc.added_hashids == [our_ex["uniqueHashID"]]
    assert cs.final_order is None


def test_mid_routine_add_produces_order_form_with_server_id() -> None:
    new = DesiredExercise(exercise="Abdominal 4 points Drawing In")
    cs, enc = _encode(_routine(*SPIKE), _main(34048391, new, 34048392, 34048393))
    assert _norm(enc.structure or {})["exercises"][0]["idx"] == 1  # type: ignore[index]
    form = order_form(cs, _routine(*SPIKE), [34049999], timezone=TZ)
    assert form is not None
    assert form["exercisesOrder"] == "34048391:0,34049999:1,34048392:2,34048393:3"


def test_cleared_routine_note_is_sent_as_empty_string() -> None:
    _, enc = _encode(_routine(*SPIKE, note="old"), DesiredRoutine(note=""))
    assert (enc.structure or {})["note"] == ""


def test_create_payload_sections_set_list_group_and_global_idx() -> None:
    spec = RoutineSpec(
        name="ZZ-Sections",
        warmup=[ExerciseSpec(exercise="band")],
        exercises=[ExerciseSpec(exercise="Push Up")],
        cooldown=[ExerciseSpec(exercise="band")],
    )
    p = new_routine_payload(spec, [BAND, PUSH_UP, BAND], number=1, now=NOW, mint=_counter())
    assert [(e["name"], e["listGroup"], e["idx"]) for e in p["exercises"]] == [
        ("Resistance Band Pull Apart", 1, 0),
        ("Push Up", 0, 1),
        ("Resistance Band Pull Apart", 2, 2),
    ]


def test_create_payload_rejects_misaligned_catalog_entries() -> None:
    spec = RoutineSpec(name="ZZ", exercises=[ExerciseSpec(exercise="Push Up")])
    with pytest.raises(ValueError, match="catalog entries"):
        new_routine_payload(spec, [PUSH_UP, BAND], number=1, now=NOW)


def test_exercise_spec_rejects_empty_sets() -> None:
    with pytest.raises(ValidationError):
        ExerciseSpec(exercise="Push Up", sets=[])


def test_local_timezone_name_is_iana_or_utc() -> None:
    name = local_timezone_name()
    assert name == "UTC" or "/" in name
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_payloads.py -v`
Expected: FAIL — `ImportError: cannot import name 'encode_change' from 'smartgym_mcp.payloads'`.

- [ ] **Step 3: Update `models.py` input specs**

In `ExerciseSpec` replace the `sets` field with:

```python
    sets: list[SetSpec] | None = Field(
        default=None,
        min_length=1,
        description="Template sets; omitted = one default set (flagged in dry-run)",
    )
```

In `RoutineSpec` add after `note`:

```python
    warmup: list[ExerciseSpec] = Field(
        default_factory=list, description="Warm-up section, in order (optional)"
    )
```

and after `exercises` (which stays the MAIN section, `min_length=1`):

```python
    cooldown: list[ExerciseSpec] = Field(
        default_factory=list, description="Cool-down section, in order (optional)"
    )
```

- [ ] **Step 4: Replace `src/smartgym_mcp/payloads.py`**

```python
"""Wire payloads for the SmartGym API (API-client spec §3, §10).

Shapes mirror the app's own captured requests (2026-10-05, v8.0.3; fixtures in
tests/fixtures/api/). New objects carry only a client `uniqueHashID`; the
server assigns identifiers and returns the mapping. Edits go to two endpoints:
`routine/update/` (routine fields, rest, sections, added and removed exercises,
order) and `routine/updateExercise/` (exercise notes and template sets).
Common fields (appVersion, authID, requestDate) are added by api/client.py.
"""

from __future__ import annotations

import json
import os
import random
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .catalog import CatalogExercise
from .diff import DEFAULT_SET, ChangeSet, ExerciseUpdate
from .model import LIST_GROUP_BY_SECTION, Routine, Section, TemplateSet
from .models import ExerciseSpec, RoutineSpec, SetSpec

_BAND_EQUIPMENT_ID = "38"
Mint = Callable[[datetime], int]


def mint_unique_hashid(now: datetime) -> int:
    return int(now.strftime("%y%m%d") + f"{random.randint(0, 99_999_999):08d}")


def local_timezone_name() -> str:
    target = os.path.realpath("/etc/localtime")
    marker = "zoneinfo/"
    return target.split(marker, 1)[1] if marker in target else "UTC"


def _utc(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S")


def _wire_date(server_date: str | None) -> str | None:
    return server_date.replace(" ", "T") if server_date else None


def _num(value: float) -> float | int:
    return int(value) if float(value).is_integer() else value


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def routine_entries(spec: RoutineSpec) -> list[tuple[Section, ExerciseSpec]]:
    groups: tuple[tuple[Section, list[ExerciseSpec]], ...] = (
        ("warmup", spec.warmup),
        ("main", spec.exercises),
        ("cooldown", spec.cooldown),
    )
    return [(section, e) for section, items in groups for e in items]


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


def exercise_payload(
    cat: CatalogExercise,
    *,
    section: Section,
    idx: int,
    rest_seconds: int,
    note: str | None,
    sets: Sequence[SetSpec],
    now: datetime,
    mint: Mint,
    routine_identifier: int | None = None,
) -> dict[str, Any]:
    added = _utc(now)
    first, second, third, fourth, fifth, sixth = cat.images
    ex: dict[str, Any] = {
        "id": cat.id,
        "genericID": cat.id,
        "name": cat.name,
        "idx": idx,
        "index": idx,
        "pause": rest_seconds,
        "type": cat.type,
        "category": cat.category,
        "subCategories": cat.sub_categories,
        "twoSides": cat.two_sides,
        "stretch": cat.stretch,
        "isStretch": cat.stretch == 1,
        "isCustom": 0,
        "requiresBands": _BAND_EQUIPMENT_ID in cat.equipment_ids,
        "isSingleWeight": 0,
        "listGroup": LIST_GROUP_BY_SECTION[section],
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
    if routine_identifier is not None:
        ex["routineID"] = routine_identifier
    if note:
        ex["note"] = note
    ex["sets"] = [_set_payload(k, s, hashid=mint(now), added=added) for k, s in enumerate(sets)]
    return ex


def new_routine_payload(
    spec: RoutineSpec,
    exercises: Sequence[CatalogExercise],
    *,
    number: int,
    now: datetime,
    mint: Mint = mint_unique_hashid,
) -> dict[str, Any]:
    entries = routine_entries(spec)
    if len(entries) != len(exercises):
        raise ValueError(f"{len(exercises)} catalog entries for {len(entries)} exercises.")
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
    for i, ((section, ex_spec), cat) in enumerate(zip(entries, exercises, strict=True)):
        routine["exercises"].append(
            exercise_payload(
                cat,
                section=section,
                idx=i,
                rest_seconds=ex_spec.rest_seconds or 0,
                note=ex_spec.note,
                sets=ex_spec.sets or [DEFAULT_SET],
                now=now,
                mint=mint,
            )
        )
    return routine


def add_routines_form(routines: Sequence[dict[str, Any]], *, timezone: str) -> dict[str, str]:
    return {"routines": json.dumps(list(routines), ensure_ascii=False), "timezone": timezone}


def archive_form(routine_identifier: int) -> dict[str, str]:
    return {"routinesIDs": str(routine_identifier)}


def unarchive_form(routine_identifier: int) -> dict[str, str]:
    return {"routineID": str(routine_identifier)}


@dataclass(frozen=True)
class EncodedChange:
    structure: dict[str, str] | None
    exercise_edits: dict[str, str] | None
    added_hashids: list[int]


def _routine_base(cs: ChangeSet, current: Routine, timezone: str) -> dict[str, str]:
    form = {"routineID": str(current.identifier), "timezone": timezone}
    if cs.expected.days is not None:
        form["days"] = cs.expected.days
    if cs.expected.goal is not None:
        form["goal"] = cs.expected.goal
    return form


def _update_entry(u: ExerciseUpdate) -> dict[str, Any] | None:
    entry: dict[str, Any] = {}
    for c in u.changes:
        if c.field == "rest_seconds":
            entry["pause"] = c.new
    if not entry:
        return None
    entry["exerciseID"] = u.identifier
    return entry


def _structure(
    cs: ChangeSet,
    current: Routine,
    catalog: Mapping[int, CatalogExercise],
    *,
    timezone: str,
    now: datetime,
    mint: Mint,
) -> tuple[dict[str, str] | None, list[int]]:
    form = _routine_base(cs, current, timezone)
    for c in cs.routine_changes:
        form[c.field] = c.new or ""
    entries = [e for e in (_update_entry(u) for u in cs.updated) if e is not None]
    if entries:
        form["updateExercises"] = _json(entries)
    if cs.removed:
        form["removeExercises"] = ",".join(str(r.identifier) for r in cs.removed)
    hashids: list[int] = []
    if cs.added:
        payloads = []
        for a in cs.added:
            cat = catalog.get(a.catalog_id)
            if cat is None:
                raise ValueError(f"Catalog exercise {a.catalog_id} is not in the app bundle.")
            p = exercise_payload(
                cat,
                section=a.section,
                idx=a.position,
                rest_seconds=a.rest_seconds,
                note=a.note,
                sets=a.sets,
                now=now,
                mint=mint,
                routine_identifier=current.identifier,
            )
            payloads.append(p)
            hashids.append(int(p["uniqueHashID"]))
        form["exercises"] = _json(payloads)
    meaningful = cs.routine_changes or entries or cs.removed or cs.added
    return (form if meaningful else None), hashids


def _existing_set(
    cur: TemplateSet, *, index: int, reps: float, weight_kg: float
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "thirdValue": _num(weight_kg),
        "uniqueHashID": cur.unique_hashid,
        "firstValue": 1,
        "identifier": cur.identifier,
        "secondValue": _num(reps),
        "type": 0,
        "index": index,
    }
    date = _wire_date(cur.date_added)
    if date:
        out["dateAdded"] = date
    return out


def _exercise_edits(
    cs: ChangeSet, current: Routine, *, timezone: str, now: datetime, mint: Mint
) -> dict[str, str] | None:
    by_id = {e.identifier: e for e in current.exercises}
    items: list[dict[str, Any]] = []
    for u in cs.updated:
        sets_by_id = {s.identifier: s for s in by_id[u.identifier].template_sets}
        item: dict[str, Any] = {}
        for c in u.changes:
            if c.field == "note":
                item["note"] = c.new or ""
        if u.added_sets:
            item["addedSets"] = [
                {
                    "routineID": current.identifier,
                    "index": a.index,
                    "uniqueHashID": mint(now),
                    "firstValue": 1,
                    "secondValue": _num(a.reps),
                    "type": 0,
                    "uniqueExerciseID": u.identifier,
                    "thirdValue": _num(a.weight_kg),
                }
                for a in u.added_sets
            ]
        if u.updated_sets:
            item["updatedSets"] = [
                _existing_set(sets_by_id[s.identifier], index=s.index, reps=s.reps,
                              weight_kg=s.weight_kg)
                for s in u.updated_sets
            ]
        if u.removed_set_ids:
            item["removedSets"] = [
                _existing_set(cur, index=cur.index, reps=cur.reps, weight_kg=cur.weight_kg)
                for cur in (sets_by_id[i] for i in u.removed_set_ids)
            ]
        if item:
            item["exerciseID"] = u.identifier
            items.append(item)
    return {"timezone": timezone, "exercises": _json(items)} if items else None


def encode_change(
    cs: ChangeSet,
    current: Routine,
    catalog: Mapping[int, CatalogExercise],
    *,
    timezone: str,
    now: datetime,
    mint: Mint = mint_unique_hashid,
) -> EncodedChange:
    structure, hashids = _structure(cs, current, catalog, timezone=timezone, now=now, mint=mint)
    return EncodedChange(
        structure=structure,
        exercise_edits=_exercise_edits(cs, current, timezone=timezone, now=now, mint=mint),
        added_hashids=hashids,
    )


def order_form(
    cs: ChangeSet, current: Routine, added_ids: Sequence[int], *, timezone: str
) -> dict[str, str] | None:
    if cs.final_order is None:
        return None
    ids: list[int] = []
    for key in cs.final_order:
        kind, _, value = key.partition(":")
        ids.append(int(value) if kind == "id" else added_ids[int(value)])
    form = _routine_base(cs, current, timezone)
    form["exercisesOrder"] = ",".join(f"{ident}:{pos}" for pos, ident in enumerate(ids))
    return form


__all__ = [
    "EncodedChange",
    "Mint",
    "add_routines_form",
    "archive_form",
    "encode_change",
    "exercise_payload",
    "local_timezone_name",
    "mint_unique_hashid",
    "new_routine_payload",
    "order_form",
    "routine_entries",
    "unarchive_form",
]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_payloads.py -v && uv run pytest -q && uv run mypy src && uv run ruff check src tests && uv run ruff format src tests`
Expected: Plan 1's 4 + 18 new pass (the parametrized routine-field test counts 4); full suite passes; mypy/ruff clean. If a golden test differs only in a key the app sends that the fixture shows and the code omits, align the code to the fixture (the fixture is the authority) and ledger the ruling.

- [ ] **Step 6: Commit**

```bash
git add src/smartgym_mcp/models.py src/smartgym_mcp/payloads.py tests/test_payloads.py
git commit -m "Encode sectioned creates and routine edits in the app's wire format"
```

---

### Task 6: Section moves on the wire (from S7)

The S7 captures (Task 1) decide the encoding. The code below implements the expected shape — `updateExercises=[{"exerciseID": <id>, "listGroup": "<1|0|2>"}]`, merged with `pause` when both change — and the spec §10.4 fallback for the case where S7 showed no in-place move. The fixtures are the authority: where a golden test disagrees, change the encoder to emit exactly the fixture's keys and ledger the ruling.

**Files:**
- Modify: `src/smartgym_mcp/payloads.py` (`_update_entry`, `SECTION_MOVE_IN_PLACE`)
- Modify: `src/smartgym_mcp/diff.py` (`moves_as_readd` — fallback)
- Test: `tests/test_payloads.py`, `tests/test_diff.py` (extend)

**Interfaces:**
- Consumes: Task 1 fixtures `update_move_to_warmup`, `update_move_to_cooldown`, `update_reorder_warmup`, `update_add_to_warmup`, `update_remove_two`, `update_clear_note`; Task 4 `ChangeSet.order` / `final_order`; Task 5 `encode_change`, `order_form`.
- Produces:
  - `payloads.SECTION_MOVE_IN_PLACE: bool` — `True` when S7 showed an in-place move, else `False`
  - `diff.moves_as_readd(cs: ChangeSet, current: Routine) -> ChangeSet` — turns every section move into remove + re-add (same catalog exercise, rest, note, template sets) with a warning per moved exercise
  - Task 8's service applies `moves_as_readd` before encoding when `SECTION_MOVE_IN_PLACE` is `False`.

- [ ] **Step 1: Write the failing golden tests**

Append to `tests/test_payloads.py`:

```python
def _sx(ident: int, section: str, idx: int) -> RoutineExercise:
    return RoutineExercise(
        identifier=ident, unique_hashid=ident, catalog_id=194, name=f"Ex {ident}",
        section=section, index=idx, rest_seconds=15, note=None, removed=False,
        template_sets=[], logged_sets=[],
    )


def _capture_routine(app: dict[str, object], layout: list[tuple[int, str]]) -> Routine:
    return Routine(
        identifier=int(str(app["routineID"])), unique_hashid=1, name="ZZ-S7",
        days=app.get("days"), goal=app.get("goal"), note=None, number=1,  # type: ignore[arg-type]
        archived=False, removed=False,
        exercises=[_sx(i, sec, idx) for idx, (i, sec) in enumerate(layout)],
    )


def _moved_id(app: dict[str, object]) -> int:
    (entry,) = app["updateExercises"]  # type: ignore[misc]
    return int(entry["exerciseID"])


def test_move_main_to_warmup_matches_capture() -> None:
    app = _fixture_form("update_move_to_warmup")
    moved = _moved_id(app)
    current = _capture_routine(app, [(1, "warmup"), (2, "warmup"), (moved, "main"), (3, "main")])
    desired = DesiredRoutine(warmup=[DesiredExercise(exercise_id=i) for i in (1, 2, moved)])
    cs, enc = _encode(current, desired)
    assert _norm(enc.structure or {}) == {k: v for k, v in app.items() if k != "exercisesOrder"}


def test_move_warmup_to_cooldown_matches_capture() -> None:
    app = _fixture_form("update_move_to_cooldown")
    moved = _moved_id(app)
    current = _capture_routine(app, [(moved, "warmup"), (1, "main"), (2, "cooldown")])
    desired = DesiredRoutine(cooldown=[DesiredExercise(exercise_id=i) for i in (2, moved)])
    _, enc = _encode(current, desired)
    assert _norm(enc.structure or {}) == {k: v for k, v in app.items() if k != "exercisesOrder"}


def test_reorder_inside_warmup_matches_capture() -> None:
    app = _fixture_form("update_reorder_warmup")
    new_order = [int(pair.split(":")[0]) for pair in str(app["exercisesOrder"]).split(",")]
    sections = ["warmup", "warmup"] + ["main"] * (len(new_order) - 4) + ["cooldown", "cooldown"]
    old = [new_order[1], new_order[0], *new_order[2:]]
    current = _capture_routine(app, list(zip(old, sections, strict=True)))
    desired = DesiredRoutine(warmup=[DesiredExercise(exercise_id=i) for i in new_order[:2]])
    cs, _ = _encode(current, desired)
    assert order_form(cs, current, [], timezone=TZ) == app


def test_remove_two_matches_capture() -> None:
    app = _fixture_form("update_remove_two")
    gone = [int(x) for x in str(app["removeExercises"]).split(",")]
    current = _capture_routine(app, [(9, "main"), *((g, "main") for g in gone)])
    _, enc = _encode(current, DesiredRoutine(main=[DesiredExercise(exercise_id=9)]))
    assert _norm(enc.structure or {}) == app


def test_clear_note_matches_capture() -> None:
    app = _fixture_form("update_clear_note")
    (item,) = app["exercises"]  # type: ignore[misc]
    ident = int(item["exerciseID"])
    ex = _sx(ident, "main", 0).model_copy(update={"note": "x"})
    current = _capture_routine({"routineID": "1"}, []).model_copy(update={"exercises": [ex]})
    desired = DesiredRoutine(main=[DesiredExercise(exercise_id=ident, note="")])
    _, enc = _encode(current, desired)
    assert _norm(enc.exercise_edits or {}) == app


def test_rest_and_move_merge_into_one_entry() -> None:
    current = _capture_routine({"routineID": "5"}, [(1, "warmup"), (2, "main")])
    desired = DesiredRoutine(warmup=[DesiredExercise(exercise_id=1),
                                     DesiredExercise(exercise_id=2, rest_seconds=45)])
    _, enc = _encode(current, desired)
    assert _norm(enc.structure or {})["updateExercises"] == [
        {"exerciseID": 2, "pause": "45", "listGroup": "1"}
    ]
```

Append to `tests/test_diff.py`:

```python
from smartgym_mcp.diff import moves_as_readd


def test_moves_as_readd_turns_a_move_into_remove_plus_add() -> None:
    cs = diff_routine(_routine(), DesiredRoutine(warmup=_ids(10, 13)), CATALOG)
    fallback = moves_as_readd(cs, _routine())
    assert [r.identifier for r in fallback.removed] == [13]
    (added,) = fallback.added
    assert (added.catalog_id, added.section, added.position, added.note) == (
        194, "warmup", 1, "Slow"
    )
    assert [(s.reps, s.weight_kg) for s in added.sets] == [(15, 0)]
    assert fallback.updated == []
    assert fallback.final_order == ["id:10", "new:0", "id:11", "id:12", "id:14"]
    assert any("re-add" in w for w in fallback.warnings)
    assert fallback.expected == cs.expected


def test_moves_as_readd_is_identity_without_moves() -> None:
    cs = diff_routine(_routine(), DesiredRoutine(main=_ids(13, 11, 12)), CATALOG)
    assert moves_as_readd(cs, _routine()) == cs
```

The S7 layouts in the first three tests mirror Task 1 Step 3's scenario (warm-up: Shoulder Circling, Bridge; main: Push Up, Squat, Plank; cool-down: Cross Arm Stretch). If the user's capture used a different layout, adjust the `layout` lists to it — the assertions stay the same.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_payloads.py tests/test_diff.py -v`
Expected: FAIL — `tests/test_diff.py`: `ImportError: cannot import name 'moves_as_readd'`; `tests/test_payloads.py`: the two move goldens and `test_rest_and_move_merge_into_one_entry` fail because `updateExercises` has no `listGroup`.

- [ ] **Step 3: Implement the in-place move encoding**

In `src/smartgym_mcp/payloads.py` add after `Mint = …`:

```python
# Spec §10.3 / S7: True = the server moves an exercise between sections in place.
SECTION_MOVE_IN_PLACE = True
```

and replace `_update_entry`:

```python
def _update_entry(u: ExerciseUpdate) -> dict[str, Any] | None:
    entry: dict[str, Any] = {}
    for c in u.changes:
        if c.field == "rest_seconds":
            entry["pause"] = c.new
    if u.new_section is not None:
        entry["listGroup"] = str(LIST_GROUP_BY_SECTION[u.new_section])
    if not entry:
        return None
    entry["exerciseID"] = u.identifier
    return entry
```

Add `"SECTION_MOVE_IN_PLACE"` to `__all__`.

- [ ] **Step 4: Implement the fallback in `src/smartgym_mcp/diff.py`**

Add after `diff_routine` (and `"moves_as_readd"` to `__all__`):

```python
def moves_as_readd(cs: ChangeSet, current: Routine) -> ChangeSet:
    """Spec §10.4 fallback: express every section move as remove + re-add."""
    moved = [u for u in cs.updated if u.new_section is not None]
    if not moved:
        return cs
    by_id = {e.identifier: e for e in current.active_exercises()}
    added = list(cs.added)
    removed = list(cs.removed)
    updated = [u for u in cs.updated if u.new_section is None]
    order = list(cs.order)
    for u in moved:
        ex = by_id[u.identifier]
        pos = order.index(f"id:{u.identifier}")
        view = cs.expected.exercises[pos]
        removed.append(RemovedExercise(identifier=ex.identifier, name=ex.name))
        order[pos] = f"new:{len(added)}"
        added.append(
            AddedExercise(
                catalog_id=ex.catalog_id,
                name=ex.name,
                section=view.section,
                position=pos,
                rest_seconds=view.rest_seconds,
                note=view.note,
                sets=[SetSpec.model_construct(reps=r, weight_kg=w) for r, w in view.sets],
            )
        )
    kept = [k for k in order if k.startswith("id:")]
    current_kept = [
        f"id:{e.identifier}" for e in current.active_exercises() if f"id:{e.identifier}" in kept
    ]
    appended_only = all(a.position >= len(kept) for a in added)
    warnings = cs.warnings + [
        f"{u.name!r} moves by remove + re-add (no in-place section move); its recent "
        "sessions in this routine restart."
        for u in moved
    ]
    return cs.model_copy(
        update={
            "added": added,
            "removed": removed,
            "updated": updated,
            "order": order,
            "final_order": None if kept == current_kept and appended_only else order,
            "warnings": warnings,
        }
    )
```

If Task 1 recorded "no in-place move", set `SECTION_MOVE_IN_PLACE = False` and delete the two move goldens (`test_move_main_to_warmup_matches_capture`, `test_move_warmup_to_cooldown_matches_capture`), ledgering why.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_payloads.py tests/test_diff.py -v && uv run pytest -q && uv run mypy src && uv run ruff check src tests && uv run ruff format src tests`
Expected: all pass; mypy/ruff clean. A golden that still differs means the capture shows a different shape: encode exactly the fixture's keys (it is the authority), update spec §10.3 if needed, ledger the ruling.

- [ ] **Step 6: Commit**

```bash
git add src/smartgym_mcp/payloads.py src/smartgym_mcp/diff.py tests/test_payloads.py tests/test_diff.py
git commit -m "Encode section moves (S7) with remove + re-add fallback"
```

---

### Task 7: Client additions, account store, snapshots

**Files:**
- Modify: `src/smartgym_mcp/api/client.py` (`ApiCalls` protocol, `user_id`, error-code passthrough)
- Create: `src/smartgym_mcp/api/store.py`
- Create: `src/smartgym_mcp/snapshots.py`
- Create: `tests/fakes.py` (offline client stand-in, shared with Task 8)
- Test: `tests/test_api_client.py` (extend), `tests/test_store.py`, `tests/test_snapshots.py`

**Interfaces:**
- Consumes: `model.parse_history_all`, `AccountData`, `Routine` (Task 3); Plan 1 `ApiClient`.
- Produces:
  - `api.client.ApiCalls(Protocol)`: `user_id: str` (property), `get(path, params=None, *, require_success=True) -> dict[str, Any]`, `post(path, form) -> dict[str, Any]`; `ApiClient.user_id` property
  - Non-2xx JSON bodies pass their `code` through (`ApiError.code`, also on `AuthError`); a body without `code` leaves `ApiError.code` as `None`
  - `api.store.RoutineNotFound(ValueError)`, `api.store.AmbiguousRoutine(ValueError)`
  - `api.store.AccountStore(client: ApiCalls, *, ttl_s: float = 30.0, clock: Callable[[], float] = time.monotonic)`: `data() -> AccountData` (cached `history/all/<user_id>/`, thread-safe), `invalidate()`, `routine_raw(identifier: int) -> dict[str, Any]` (fresh `routine/single/<id>/`), `resolve(ref: str | int) -> Routine` (id, exact name, then substring; removed routines excluded; archived included), `routine_of_exercise(exercise_id: int) -> Routine`
  - `snapshots.save_snapshot(backup_dir: Path, routine_raw: Mapping[str, Any], *, now: datetime) -> Path` (`<backup_dir>/<YYYYmmdd-HHMMSS-ffffff>/routine-<identifier>.json`)
  - `tests/fakes.FakeClient(gets: Mapping[str, dict | list[dict]] | None = None, posts: list[dict | Exception] | None = None, user_id: str = "1")` with `.sent: list[tuple[str, dict[str, str]]]` and `.get_calls: list[str]`; a list of GET bodies is served in order, the last one repeats.

- [ ] **Step 1: Write the shared fake**

`tests/fakes.py`:

```python
"""Offline stand-in for api.client.ApiClient: scripted responses, recorded posts."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any


class FakeClient:
    def __init__(
        self,
        gets: Mapping[str, dict[str, Any] | list[dict[str, Any]]] | None = None,
        posts: list[dict[str, Any] | Exception] | None = None,
        user_id: str = "1",
    ) -> None:
        self._gets = {k: (list(v) if isinstance(v, list) else [v]) for k, v in (gets or {}).items()}
        self._posts = list(posts or [])
        self.user_id = user_id
        self.sent: list[tuple[str, dict[str, str]]] = []
        self.get_calls: list[str] = []

    def get(
        self, path: str, params: Mapping[str, str] | None = None, *, require_success: bool = True
    ) -> dict[str, Any]:
        self.get_calls.append(path)
        queue = self._gets[path]
        body = queue.pop(0) if len(queue) > 1 else queue[0]
        return copy.deepcopy(body)

    def post(self, path: str, form: Mapping[str, str]) -> dict[str, Any]:
        self.sent.append((path, dict(form)))
        if not self._posts:
            return {"code": "SUCCESS"}
        nxt = self._posts.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_api_client.py`:

```python
def test_http_error_passes_server_code_through() -> None:
    client = _client(lambda _r: httpx.Response(400, json={"code": "TOO_MANY_ROUTINES"}))
    with pytest.raises(ApiError, match="TOO_MANY_ROUTINES") as exc:
        client.post("routine/add/", {"routines": "[]"})
    assert exc.value.code == "TOO_MANY_ROUTINES"


def test_auth_error_keeps_code_without_secrets() -> None:
    client = _client(lambda _r: httpx.Response(401, json={"code": "INVALID_TOKEN"}))
    with pytest.raises(AuthError) as exc:
        client.get("user/info/1")
    assert exc.value.code == "INVALID_TOKEN"
    assert SECRET not in str(exc.value)


def test_missing_code_stays_none() -> None:
    client = _client(lambda _r: httpx.Response(200, json={"routines": []}))
    with pytest.raises(ApiError) as exc:
        client.get("routine/all/1/")
    assert exc.value.code is None


def test_user_id_is_exposed() -> None:
    assert _client(lambda _r: httpx.Response(200, json={})).user_id == "1"
```

`tests/test_store.py`:

```python
"""api/store.py on a fake client — cache, resolution, fresh routine fetches."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fakes import FakeClient

from smartgym_mcp.api.store import AccountStore, AmbiguousRoutine, RoutineNotFound
from smartgym_mcp.model import ApiPayloadError

FIX = Path(__file__).parent / "fixtures" / "api"
ACCOUNT = json.loads((FIX / "history_all_synthetic.json").read_text(encoding="utf-8"))
SINGLE = json.loads((FIX / "routine_single_synthetic.json").read_text(encoding="utf-8"))
HISTORY = "history/all/1/"


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def _store(client: FakeClient, clock: Clock | None = None) -> AccountStore:
    return AccountStore(client, ttl_s=30, clock=clock or Clock())


def test_data_is_cached_until_ttl_or_invalidate() -> None:
    client, clock = FakeClient({HISTORY: ACCOUNT}), Clock()
    store = _store(client, clock)
    store.data()
    store.data()
    assert client.get_calls == [HISTORY]
    clock.t = 31
    store.data()
    store.invalidate()
    store.data()
    assert client.get_calls == [HISTORY] * 3


def test_resolve_by_id_exact_name_and_substring() -> None:
    store = _store(FakeClient({HISTORY: ACCOUNT}))
    assert store.resolve("3000001").name == "ZZ-FB — Test"
    assert store.resolve("zz-fb — test").identifier == 3000001
    assert store.resolve("Old").identifier == 3000002  # archived routines resolve too


def test_resolve_excludes_removed_and_reports_ambiguity_with_ids() -> None:
    store = _store(FakeClient({HISTORY: ACCOUNT}))
    with pytest.raises(RoutineNotFound):
        store.resolve("ZZ-Gone")
    with pytest.raises(AmbiguousRoutine, match="3000001.*3000002"):
        store.resolve("ZZ-")


def test_routine_raw_is_fetched_fresh_each_time() -> None:
    client = FakeClient({"routine/single/3000001/": SINGLE})
    store = _store(client)
    assert store.routine_raw(3000001)["identifier"] == "3000001"
    store.routine_raw(3000001)
    assert client.get_calls == ["routine/single/3000001/"] * 2


def test_routine_raw_missing_routine() -> None:
    store = _store(FakeClient({"routine/single/9/": {"code": "SUCCESS", "routines": []}}))
    with pytest.raises(RoutineNotFound, match="9"):
        store.routine_raw(9)


def test_routine_of_exercise() -> None:
    store = _store(FakeClient({HISTORY: ACCOUNT}))
    assert store.routine_of_exercise(40000012).identifier == 3000001
    with pytest.raises(RoutineNotFound):
        store.routine_of_exercise(40000013)  # removed exercise


def test_partial_history_is_an_error() -> None:
    store = _store(FakeClient({HISTORY: {**ACCOUNT, "hasMore": True}}))
    with pytest.raises(ApiPayloadError):
        store.data()
```

`tests/test_snapshots.py`:

```python
"""snapshots.py: the fetched routine JSON is saved verbatim before a write."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from smartgym_mcp.snapshots import save_snapshot


def test_snapshot_written_under_timestamped_folder(tmp_path: Path) -> None:
    raw = {"identifier": "3000001", "name": "FB-A — Return W1", "exercises": []}
    path = save_snapshot(tmp_path, raw, now=datetime(2026, 10, 6, 9, 30, 1, 5))
    assert path == tmp_path / "20261006-093001-000005" / "routine-3000001.json"
    assert json.loads(path.read_text(encoding="utf-8")) == raw
    assert "—" in path.read_text(encoding="utf-8")
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_api_client.py tests/test_store.py tests/test_snapshots.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'smartgym_mcp.api.store'` / `'smartgym_mcp.snapshots'`; the new client tests fail (`code` not passed through, no `user_id`).

- [ ] **Step 4: Implement**

In `src/smartgym_mcp/api/client.py`:

- add the protocol after `CredentialProvider`:

```python
class ApiCalls(Protocol):
    """What the store and the service need from a client (ApiClient or a test fake)."""

    @property
    def user_id(self) -> str: ...

    def get(
        self, path: str, params: Mapping[str, str] | None = None, *, require_success: bool = True
    ) -> dict[str, Any]: ...

    def post(self, path: str, form: Mapping[str, str]) -> dict[str, Any]: ...
```

- add to `ApiClient` after `close`:

```python
    @property
    def user_id(self) -> str:
        return self._credentials.user_id
```

- replace `_decode`:

```python
    @staticmethod
    def _server_code(resp: httpx.Response) -> str | None:
        try:
            body = resp.json()
        except ValueError:
            return None
        code = body.get("code") if isinstance(body, dict) else None
        return str(code) if code is not None else None

    @classmethod
    def _decode(cls, path: str, resp: httpx.Response, require_success: bool) -> dict[str, Any]:
        if resp.status_code in (401, 403):
            code = cls._server_code(resp)
            raise AuthError(
                f"SmartGym rejected the credentials (HTTP {resp.status_code}"
                f"{', ' + code if code else ''}) on {path}. "
                "Re-run scripts/capture_credentials.py and retry.",
                code=code,
            )
        if not 200 <= resp.status_code < 300:
            code = cls._server_code(resp)
            raise ApiError(
                f"SmartGym answered HTTP {resp.status_code}{' ' + code if code else ''} "
                f"on {path}.",
                code=code,
            )
        try:
            body = resp.json()
        except ValueError:
            raise ApiError(f"SmartGym returned a non-JSON response on {path}.") from None
        if not isinstance(body, dict):
            raise ApiError(f"SmartGym returned an unexpected JSON shape on {path}.")
        code = body.get("code")
        if require_success and code != "SUCCESS":
            raise ApiError(
                f"SmartGym answered {code!r} on {path}.",
                code=str(code) if code is not None else None,
            )
        return body
```

- add `"ApiCalls"` to `__all__`.

`src/smartgym_mcp/api/store.py`:

```python
"""Account reads over the SmartGym API (API-client spec §5).

`history/all/<id>/` returns every routine, workout and equipment list in one
answer (S4); it is cached briefly and dropped after every write. Writes always
work on a fresh `routine/single/<id>/` instead.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

from ..model import AccountData, Routine, parse_history_all
from .client import ApiCalls


class RoutineNotFound(ValueError):
    pass


class AmbiguousRoutine(ValueError):
    pass


class AccountStore:
    def __init__(
        self,
        client: ApiCalls,
        *,
        ttl_s: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._ttl = ttl_s
        self._clock = clock
        self._lock = threading.Lock()
        self._cached: AccountData | None = None
        self._at = 0.0

    def data(self) -> AccountData:
        with self._lock:
            if self._cached is None or self._clock() - self._at > self._ttl:
                raw = self._client.get(f"history/all/{self._client.user_id}/")
                self._cached = parse_history_all(raw)
                self._at = self._clock()
            return self._cached

    def invalidate(self) -> None:
        with self._lock:
            self._cached = None

    def routine_raw(self, identifier: int) -> dict[str, Any]:
        body = self._client.get(f"routine/single/{identifier}/")
        routines = body.get("routines") or []
        if not routines:
            raise RoutineNotFound(f"SmartGym has no routine with id {identifier}.")
        return dict(routines[0])

    def resolve(self, ref: str | int) -> Routine:
        live = [r for r in self.data().routines if not r.removed]
        s = str(ref).strip()
        if s.isdigit():
            for r in live:
                if r.identifier == int(s):
                    return r
            raise RoutineNotFound(f"No routine with id {s}. Use smartgym_list_routines.")
        exact = [r for r in live if r.name.lower() == s.lower()]
        matches = exact or [r for r in live if s.lower() in r.name.lower()]
        if not matches:
            raise RoutineNotFound(
                f"No routine matching {ref!r}. Use smartgym_list_routines to see names."
            )
        if len(matches) > 1:
            listed = ", ".join(
                f"{r.name} (id={r.identifier}{', archived' if r.archived else ''})"
                for r in matches
            )
            raise AmbiguousRoutine(
                f"Multiple routines match {ref!r}: {listed}. Pass the id to disambiguate."
            )
        return matches[0]

    def routine_of_exercise(self, exercise_id: int) -> Routine:
        for r in self.data().routines:
            if not r.removed and any(
                e.identifier == exercise_id and not e.removed for e in r.exercises
            ):
                return r
        raise RoutineNotFound(
            f"No routine contains exercise {exercise_id}. "
            "Use smartgym_get_routine to see exercise ids."
        )


__all__ = ["AccountStore", "AmbiguousRoutine", "RoutineNotFound"]
```

`src/smartgym_mcp/snapshots.py`:

```python
"""Pre-write snapshots of the fetched routine JSON (API-client spec §7).

Restore = smartgym_apply_routine from the snapshot's content.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any


def save_snapshot(backup_dir: Path, routine_raw: Mapping[str, Any], *, now: datetime) -> Path:
    folder = backup_dir / now.strftime("%Y%m%d-%H%M%S-%f")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"routine-{routine_raw.get('identifier', 'unknown')}.json"
    path.write_text(json.dumps(routine_raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


__all__ = ["save_snapshot"]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_api_client.py tests/test_store.py tests/test_snapshots.py -v && uv run pytest -q && uv run mypy src && uv run ruff check src tests && uv run ruff format src tests`
Expected: 4 + 7 + 1 new tests pass; the Plan 1 auth test still passes (`AuthError` message keeps no secrets); full suite passes; mypy/ruff clean.

- [ ] **Step 6: Commit**

```bash
git add src/smartgym_mcp/api/client.py src/smartgym_mcp/api/store.py src/smartgym_mcp/snapshots.py tests/fakes.py tests/test_api_client.py tests/test_store.py tests/test_snapshots.py
git commit -m "Add account store, routine snapshots and server error codes"
```

---

### Task 8: Write service + single-field builders

**Files:**
- Create: `src/smartgym_mcp/service.py`
- Create: `src/smartgym_mcp/builders.py`
- Test: `tests/test_service.py`, `tests/test_builders.py`

**Interfaces:**
- Consumes: `api.client.ApiCalls`, `ApiError` (+ subclasses) (Task 7 / Plan 1); `api.store.AccountStore` (Task 7); `snapshots.save_snapshot` (Task 7); `diff.diff_routine`, `moves_as_readd`, `view_of`, `DesiredRoutine`, `DesiredExercise`, `ChangeSet`, `RoutineView`, `ExerciseView`, `DiffError`, `DEFAULT_SET` (Tasks 4, 6); `payloads.encode_change`, `order_form`, `new_routine_payload`, `add_routines_form`, `archive_form`, `unarchive_form`, `routine_entries`, `mint_unique_hashid`, `Mint`, `SECTION_MOVE_IN_PLACE` (Tasks 5, 6); `model.parse_routine`, `ApiPayloadError`, `LIST_GROUP_BY_SECTION`, `Routine`, `Section` (Task 3); `matching.ExerciseCatalog`, `UnresolvedExercise`; `models.RoutineSpec`, `ExerciseResolution`, `SetSpec`; `catalog.CatalogExercise`; `tests/fakes.FakeClient`.
- Produces:
  - `service.WriteVerifyError(RuntimeError)`
  - `service.RoutineId(BaseModel)`: `identifier: int`, `name: str`
  - `service.EditResult(BaseModel)`: `dry_run: bool`, `routine: RoutineId`, `changes: ChangeSet`, `requests: list[str]`, `snapshot: str | None`, `notice: str`
  - `service.CreatePlan(BaseModel)`: `name: str`, `resolutions: list[ExerciseResolution]`, `exercise_count: int`, `set_count: int`, `warnings: list[str]`
  - `service.CreateResult(BaseModel)`: `dry_run: bool`, `plan: list[CreatePlan]`, `created: list[RoutineId]`, `notice: str`
  - `service.ArchiveResult(BaseModel)`: `dry_run: bool`, `archived: bool`, `routines: list[RoutineId]`, `skipped: list[RoutineId]`, `notice: str`
  - `service.compare_views(expected: RoutineView, actual: RoutineView) -> list[str]`
  - `service.RoutineService(client, store, catalog, bundle: Mapping[int, CatalogExercise], *, backup_dir: Path, timezone: str, now: Callable[[], datetime] = <local now>, mint: Mint = mint_unique_hashid, move_in_place: bool = SECTION_MOVE_IN_PLACE)` with
    - `edit(ref: str | int, build: Callable[[Routine], DesiredRoutine], *, dry_run: bool) -> EditResult`
    - `edit_exercise(exercise_id: int, build: Callable[[Routine], DesiredRoutine], *, dry_run: bool) -> EditResult` (routine found by exercise)
    - `create(specs: Sequence[RoutineSpec], *, dry_run: bool) -> CreateResult`
    - `set_archived(refs: Sequence[str | int], *, archived: bool, dry_run: bool) -> ArchiveResult`
  - `builders.add_exercise(routine, exercise, *, section, position, rest_seconds, note, sets) -> DesiredRoutine`, `builders.move_exercise(routine, exercise_id, *, section, position) -> DesiredRoutine`, `builders.remove_exercise(routine, exercise_id) -> DesiredRoutine`, `builders.update_exercise(routine, exercise_id, *, rest_seconds, note, sets) -> DesiredRoutine`, `builders.reorder(routine, *, warmup, main, cooldown) -> DesiredRoutine`, `builders.update_routine(*, name, days, goal, note) -> DesiredRoutine`. `position` counts within the section, `None` = last.

- [ ] **Step 1: Write the failing builder tests**

`tests/test_builders.py`:

```python
"""builders.py: single-field tool inputs → DesiredRoutine (thin wrappers over apply)."""

from __future__ import annotations

import pytest

from smartgym_mcp import builders
from smartgym_mcp.diff import DiffError, diff_routine
from smartgym_mcp.matching import ExerciseCatalog
from smartgym_mcp.model import Routine, RoutineExercise
from smartgym_mcp.models import SetSpec

CATALOG = ExerciseCatalog([(300, "Shoulder Circling"), (207, "Cable Chest Press"), (20, "Plank")])


def _ex(ident: int, section: str, idx: int) -> RoutineExercise:
    return RoutineExercise(
        identifier=ident, unique_hashid=ident, catalog_id=207, name=f"Ex {ident}",
        section=section, index=idx, rest_seconds=60, note=None, removed=False,
        template_sets=[], logged_sets=[],
    )


ROUTINE = Routine(
    identifier=1, unique_hashid=1, name="ZZ-B", days=None, goal=None, note=None, number=1,
    archived=False, removed=False,
    exercises=[_ex(10, "warmup", 0), _ex(11, "main", 1), _ex(12, "main", 2), _ex(14, "cooldown", 3)],
)


def _ids(items: list | None) -> list:  # type: ignore[type-arg]
    return [d.exercise_id or d.exercise for d in items] if items is not None else None  # type: ignore[return-value]


def test_add_exercise_into_section_at_position() -> None:
    d = builders.add_exercise(ROUTINE, "Plank", section="warmup", position=0, rest_seconds=30,
                              note=None, sets=[SetSpec(reps=30)])
    assert _ids(d.warmup) == ["Plank", 10]
    assert d.main is None and d.cooldown is None
    assert d.warmup[0].rest_seconds == 30  # type: ignore[index]


def test_add_exercise_defaults_to_end() -> None:
    d = builders.add_exercise(ROUTINE, "Plank", section="main", position=None, rest_seconds=None,
                              note=None, sets=None)
    assert _ids(d.main) == [11, 12, "Plank"]


def test_move_between_sections_lists_both() -> None:
    d = builders.move_exercise(ROUTINE, 11, section="cooldown", position=0)
    assert (_ids(d.cooldown), _ids(d.main), d.warmup) == ([11, 14], [12], None)
    (upd,) = diff_routine(ROUTINE, d, CATALOG).updated
    assert (upd.identifier, upd.new_section) == (11, "cooldown")


def test_move_within_section_reorders() -> None:
    d = builders.move_exercise(ROUTINE, 12, section="main", position=0)
    assert (_ids(d.main), d.warmup, d.cooldown) == ([12, 11], None, None)


def test_remove_exercise_lists_its_section_without_it() -> None:
    d = builders.remove_exercise(ROUTINE, 12)
    assert (_ids(d.main), d.warmup, d.cooldown) == ([11], None, None)


def test_update_exercise_carries_only_given_fields() -> None:
    d = builders.update_exercise(ROUTINE, 11, rest_seconds=90, note=None, sets=None)
    first, second = d.main  # type: ignore[misc]
    assert (first.exercise_id, first.rest_seconds, second.rest_seconds) == (11, 90, None)


def test_update_exercise_needs_a_field() -> None:
    with pytest.raises(DiffError, match="at least one"):
        builders.update_exercise(ROUTINE, 11, rest_seconds=None, note=None, sets=None)


def test_unknown_exercise_is_rejected() -> None:
    with pytest.raises(DiffError, match="999"):
        builders.remove_exercise(ROUTINE, 999)


def test_reorder_must_keep_section_members() -> None:
    assert _ids(builders.reorder(ROUTINE, warmup=None, main=[12, 11], cooldown=None).main) == [12, 11]
    with pytest.raises(DiffError, match="smartgym_move_exercise"):
        builders.reorder(ROUTINE, warmup=None, main=[12, 11, 14], cooldown=None)
    with pytest.raises(DiffError, match="at least one section"):
        builders.reorder(ROUTINE, warmup=None, main=None, cooldown=None)


def test_update_routine_needs_a_field() -> None:
    assert builders.update_routine(name=None, days="1,3", goal=None, note=None).days == "1,3"
    with pytest.raises(DiffError, match="at least one"):
        builders.update_routine(name=None, days=None, goal=None, note=None)
```

- [ ] **Step 2: Write the failing service tests**

`tests/test_service.py`:

```python
"""service.py on a fake client: dry run, snapshot, ordered sends, verification, failures."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fakes import FakeClient

from smartgym_mcp.api.client import WriteOutcomeUnknown
from smartgym_mcp.api.store import AccountStore
from smartgym_mcp.catalog import CatalogExercise
from smartgym_mcp.diff import DesiredExercise, DesiredRoutine, DiffError
from smartgym_mcp.matching import ExerciseCatalog
from smartgym_mcp.models import ExerciseSpec, RoutineSpec, SetSpec
from smartgym_mcp.service import RoutineService, WriteVerifyError

HISTORY = "history/all/1/"
SINGLE = "routine/single/3000001/"
NOW = datetime(2026, 10, 6, 10, 0, tzinfo=UTC)
FIRST_HASH = 26100600000001


def _cat(ident: int, name: str) -> CatalogExercise:
    return CatalogExercise(id=ident, name=name, type=0, category=1, sub_categories="",
                           two_sides=0, stretch=0, equipment_ids=(),
                           images=(f"{ident}-1", "", "", "", "", ""))


BUNDLE = {300: _cat(300, "Shoulder Circling"), 207: _cat(207, "Cable Chest Press"),
          20: _cat(20, "Plank")}
CATALOG = ExerciseCatalog([(i, c.name) for i, c in BUNDLE.items()])


def _raw_ex(ident: int, cat: int, group: int, idx: int, sets: list[tuple[int, float, float]],
            *, pause: str = "60", note: str | None = None) -> dict[str, Any]:
    return {
        "id": str(cat), "name": BUNDLE[cat].name, "identifier": str(ident),
        "uniqueHashID": str(ident), "idx": str(idx), "pause": pause, "note": note,
        "dateRemoved": None, "listGroup": str(group),
        "sets": [
            {"identifier": str(sid), "uniqueHashID": str(sid), "firstValue": "1",
             "secondValue": str(r), "thirdValue": str(w), "type": "0", "index": str(i),
             "dateAdded": "2026-10-05 10:00:00", "dateRemoved": None, "dateLogged": None}
            for i, (sid, r, w) in enumerate(sets)
        ],
    }


def _raw_routine(*exs: dict[str, Any], ident: str = "3000001", name: str = "ZZ-Svc",
                 hashid: str = "26100500000001", archived: str | None = None) -> dict[str, Any]:
    return {"identifier": ident, "uniqueHashID": hashid, "name": name, "days": "2,4,6",
            "goal": "", "note": None, "number": "5", "dateArchived": archived,
            "dateRemoved": None, "exercises": list(exs)}


def _account(*routines: dict[str, Any]) -> dict[str, Any]:
    return {"code": "SUCCESS", "hasMore": False, "routines": list(routines), "histories": [],
            "equipmentLists": []}


WARM = _raw_ex(10, 300, 1, 0, [(100, 10, 0)])
CHEST = _raw_ex(11, 207, 0, 1, [(110, 10, 40)])
BEFORE = _raw_routine(WARM, CHEST)


def _single(*bodies: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"code": "SUCCESS", "routines": [b]} for b in bodies]


def _counter():  # type: ignore[no-untyped-def]
    n = iter(range(FIRST_HASH, FIRST_HASH + 1000))
    return lambda _now: next(n)


def _service(client: FakeClient, tmp_path: Path, *, move_in_place: bool = True) -> RoutineService:
    return RoutineService(
        client, AccountStore(client), CATALOG, BUNDLE, backup_dir=tmp_path,
        timezone="Europe/Madrid", now=lambda: NOW, mint=_counter(), move_in_place=move_in_place,
    )


def _rest(seconds: int):  # type: ignore[no-untyped-def]
    return lambda _r: DesiredRoutine(main=[DesiredExercise(exercise_id=11, rest_seconds=seconds)])


def test_dry_run_sends_nothing(tmp_path: Path) -> None:
    client = FakeClient({HISTORY: _account(BEFORE), SINGLE: _single(BEFORE)})
    result = _service(client, tmp_path).edit("ZZ-Svc", _rest(90), dry_run=True)
    assert result.dry_run and result.requests == ["routine/update/"]
    assert client.sent == [] and result.snapshot is None


def test_apply_snapshots_sends_and_verifies(tmp_path: Path) -> None:
    after = _raw_routine(WARM, {**CHEST, "pause": "90"})
    client = FakeClient({HISTORY: _account(BEFORE), SINGLE: _single(BEFORE, after)})
    result = _service(client, tmp_path).edit("ZZ-Svc", _rest(90), dry_run=False)
    assert [path for path, _ in client.sent] == ["routine/update/"]
    assert json.loads(client.sent[0][1]["updateExercises"]) == [{"pause": "90", "exerciseID": 11}]
    assert result.snapshot is not None
    assert json.loads(Path(result.snapshot).read_text(encoding="utf-8")) == BEFORE


def test_server_ignoring_the_edit_is_a_verify_error(tmp_path: Path) -> None:
    client = FakeClient({HISTORY: _account(BEFORE), SINGLE: _single(BEFORE, BEFORE)})
    with pytest.raises(WriteVerifyError, match="rest_seconds: expected 90, got 60"):
        _service(client, tmp_path).edit("ZZ-Svc", _rest(90), dry_run=False)


def test_failure_after_first_request_keeps_class_and_reports_progress(tmp_path: Path) -> None:
    client = FakeClient(
        {HISTORY: _account(BEFORE), SINGLE: _single(BEFORE)},
        posts=[{"code": "SUCCESS"}, WriteOutcomeUnknown("timeout")],
    )
    build = lambda _r: DesiredRoutine(  # noqa: E731
        main=[DesiredExercise(exercise_id=11, rest_seconds=90, note="Slow")]
    )
    with pytest.raises(WriteOutcomeUnknown) as exc:
        _service(client, tmp_path).edit("ZZ-Svc", build, dry_run=False)
    message = str(exc.value)
    assert "Already sent: routine/update/" in message and "Snapshot" in message
    assert [path for path, _ in client.sent] == ["routine/update/", "routine/updateExercise/"]


def test_mid_routine_add_sends_order_with_new_server_id(tmp_path: Path) -> None:
    plank = _raw_ex(555, 20, 1, 1, [(600, 30, 0)], pause="0")
    after = _raw_routine(WARM, plank, {**CHEST, "idx": "2"})
    client = FakeClient(
        {HISTORY: _account(BEFORE), SINGLE: _single(BEFORE, after)},
        posts=[{"code": "SUCCESS", "exercises": [{"original_id": FIRST_HASH, "server_id": "555"}]}],
    )
    build = lambda _r: DesiredRoutine(  # noqa: E731
        warmup=[DesiredExercise(exercise_id=10),
                DesiredExercise(exercise="Plank", sets=[SetSpec(reps=30)])]
    )
    result = _service(client, tmp_path).edit("ZZ-Svc", build, dry_run=False)
    assert result.requests == ["routine/update/", "routine/update/ (order)"]
    assert client.sent[1][1]["exercisesOrder"] == "10:0,555:1,11:2"


def test_nothing_to_change_sends_nothing(tmp_path: Path) -> None:
    client = FakeClient({HISTORY: _account(BEFORE), SINGLE: _single(BEFORE)})
    result = _service(client, tmp_path).edit("ZZ-Svc", _rest(60), dry_run=False)
    assert client.sent == [] and "Nothing to change" in result.notice


def test_moves_fall_back_to_readd_when_not_in_place(tmp_path: Path) -> None:
    client = FakeClient({HISTORY: _account(BEFORE), SINGLE: _single(BEFORE)})
    build = lambda _r: DesiredRoutine(warmup=[DesiredExercise(exercise_id=10),  # noqa: E731
                                              DesiredExercise(exercise_id=11)])
    result = _service(client, tmp_path, move_in_place=False).edit("ZZ-Svc", build, dry_run=True)
    assert [r.identifier for r in result.changes.removed] == [11]
    assert [(a.catalog_id, a.section) for a in result.changes.added] == [(207, "warmup")]


def test_create_rejects_existing_name(tmp_path: Path) -> None:
    client = FakeClient({HISTORY: _account(BEFORE)})
    spec = RoutineSpec(name="zz-svc", exercises=[ExerciseSpec(exercise="Plank")])
    with pytest.raises(DiffError, match="already exists"):
        _service(client, tmp_path).create([spec], dry_run=True)


def test_create_sends_sections_and_verifies(tmp_path: Path) -> None:
    created = _raw_routine(
        _raw_ex(900, 300, 1, 0, [(901, 10, 0)], pause="0"),
        _raw_ex(902, 20, 0, 1, [(903, 30, 0)], pause="0"),
        ident="3000009", name="ZZ-New", hashid=str(FIRST_HASH),
    )
    created["days"] = ""
    client = FakeClient({HISTORY: [_account(BEFORE), _account(BEFORE, created)]})
    spec = RoutineSpec(
        name="ZZ-New",
        warmup=[ExerciseSpec(exercise="Shoulder Circling", sets=[SetSpec(reps=10)])],
        exercises=[ExerciseSpec(exercise="Plank", sets=[SetSpec(reps=30)])],
    )
    result = _service(client, tmp_path).create([spec], dry_run=False)
    (path, form), = client.sent
    (payload,) = json.loads(form["routines"])
    assert path == "routine/add/" and payload["number"] == 6
    assert [e["listGroup"] for e in payload["exercises"]] == [1, 0]
    assert [(c.identifier, c.name) for c in result.created] == [(3000009, "ZZ-New")]


def test_archive_and_skip_already_archived(tmp_path: Path) -> None:
    old = _raw_routine(ident="3000002", name="ZZ-Old", hashid="2", archived="2026-09-30 10:00:00")
    archived_now = {**BEFORE, "dateArchived": "2026-10-06 10:00:00"}
    client = FakeClient({HISTORY: [_account(BEFORE, old), _account(archived_now, old)]})
    result = _service(client, tmp_path).set_archived(
        ["ZZ-Svc", "ZZ-Old"], archived=True, dry_run=False
    )
    assert client.sent == [("routine/archive/", {"routinesIDs": "3000001"})]
    assert [r.name for r in result.routines] == ["ZZ-Svc"]
    assert [r.name for r in result.skipped] == ["ZZ-Old"]


def test_archive_not_reflected_is_a_verify_error(tmp_path: Path) -> None:
    client = FakeClient({HISTORY: _account(BEFORE)})
    with pytest.raises(WriteVerifyError, match="ZZ-Svc"):
        _service(client, tmp_path).set_archived(["ZZ-Svc"], archived=True, dry_run=False)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_builders.py tests/test_service.py -v`
Expected: FAIL — `ImportError: cannot import name 'builders'` / `No module named 'smartgym_mcp.service'`.

- [ ] **Step 4: Implement `src/smartgym_mcp/builders.py`**

```python
"""Single-field tool inputs → a DesiredRoutine (API-client spec §6).

Each single-field tool is a thin wrapper over apply_routine: it lists only the
section(s) it touches and leaves the others as None (kept).
"""

from __future__ import annotations

from .diff import DesiredExercise, DesiredRoutine, DiffError
from .model import Routine, Section
from .models import SetSpec


def _ids(routine: Routine, section: Section) -> list[DesiredExercise]:
    return [DesiredExercise(exercise_id=e.identifier) for e in routine.section(section)]


def _section_of(routine: Routine, exercise_id: int) -> Section:
    for e in routine.active_exercises():
        if e.identifier == exercise_id:
            return e.section
    raise DiffError(f"Exercise {exercise_id} is not an active exercise of {routine.name!r}.")


def _insert(
    items: list[DesiredExercise], item: DesiredExercise, position: int | None
) -> list[DesiredExercise]:
    out = list(items)
    if position is None or position >= len(out):
        out.append(item)
    else:
        out.insert(max(position, 0), item)
    return out


def add_exercise(
    routine: Routine,
    exercise: str,
    *,
    section: Section,
    position: int | None,
    rest_seconds: int | None,
    note: str | None,
    sets: list[SetSpec] | None,
) -> DesiredRoutine:
    new = DesiredExercise(exercise=exercise, rest_seconds=rest_seconds, note=note, sets=sets)
    return DesiredRoutine(**{section: _insert(_ids(routine, section), new, position)})


def move_exercise(
    routine: Routine, exercise_id: int, *, section: Section, position: int | None
) -> DesiredRoutine:
    source = _section_of(routine, exercise_id)
    target = [d for d in _ids(routine, section) if d.exercise_id != exercise_id]
    lists = {section: _insert(target, DesiredExercise(exercise_id=exercise_id), position)}
    if source != section:
        lists[source] = [d for d in _ids(routine, source) if d.exercise_id != exercise_id]
    return DesiredRoutine(**lists)


def remove_exercise(routine: Routine, exercise_id: int) -> DesiredRoutine:
    section = _section_of(routine, exercise_id)
    kept = [d for d in _ids(routine, section) if d.exercise_id != exercise_id]
    return DesiredRoutine(**{section: kept})


def update_exercise(
    routine: Routine,
    exercise_id: int,
    *,
    rest_seconds: int | None,
    note: str | None,
    sets: list[SetSpec] | None,
) -> DesiredRoutine:
    if rest_seconds is None and note is None and sets is None:
        raise DiffError("Pass at least one of rest_seconds, note, sets.")
    section = _section_of(routine, exercise_id)
    items = [
        DesiredExercise(exercise_id=exercise_id, rest_seconds=rest_seconds, note=note, sets=sets)
        if d.exercise_id == exercise_id
        else d
        for d in _ids(routine, section)
    ]
    return DesiredRoutine(**{section: items})


def reorder(
    routine: Routine,
    *,
    warmup: list[int] | None,
    main: list[int] | None,
    cooldown: list[int] | None,
) -> DesiredRoutine:
    given: dict[Section, list[int] | None] = {"warmup": warmup, "main": main, "cooldown": cooldown}
    if all(v is None for v in given.values()):
        raise DiffError("Pass the new order for at least one section.")
    problems: list[str] = []
    lists: dict[Section, list[DesiredExercise]] = {}
    for section, ids in given.items():
        if ids is None:
            continue
        members = sorted(e.identifier for e in routine.section(section))
        if sorted(ids) != members:
            problems.append(
                f"{section}: must list exactly its current exercises {members} (got {ids}); "
                "use smartgym_move_exercise to change sections."
            )
        lists[section] = [DesiredExercise(exercise_id=i) for i in ids]
    if problems:
        raise DiffError("Rejected — nothing was changed:\n- " + "\n- ".join(problems))
    return DesiredRoutine(**lists)


def update_routine(
    *, name: str | None, days: str | None, goal: str | None, note: str | None
) -> DesiredRoutine:
    if all(v is None for v in (name, days, goal, note)):
        raise DiffError("Pass at least one of name, days, goal, note.")
    return DesiredRoutine(name=name, days=days, goal=goal, note=note)


__all__ = [
    "add_exercise",
    "move_exercise",
    "remove_exercise",
    "reorder",
    "update_exercise",
    "update_routine",
]
```

- [ ] **Step 5: Implement `src/smartgym_mcp/service.py`**

```python
"""Write orchestration over the SmartGym API (API-client spec §5 invariant 2, §7).

Every edit: resolve → fetch the routine fresh → plan (diff) → dry run returns
the plan → snapshot → send (structure, order, exercise edits) → re-fetch →
verify against the plan's expected view. Writes are never re-sent; a failure
part-way names what was already sent and where the snapshot is.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .api.client import ApiCalls, ApiError
from .api.store import AccountStore
from .catalog import CatalogExercise
from .diff import (
    DEFAULT_SET,
    ChangeSet,
    DesiredRoutine,
    DiffError,
    ExerciseView,
    RoutineView,
    diff_routine,
    moves_as_readd,
    view_of,
)
from .matching import ExerciseCatalog, UnresolvedExercise
from .model import LIST_GROUP_BY_SECTION, ApiPayloadError, Routine, Section, parse_routine
from .models import ExerciseResolution, RoutineSpec
from .payloads import (
    SECTION_MOVE_IN_PLACE,
    Mint,
    add_routines_form,
    archive_form,
    encode_change,
    mint_unique_hashid,
    new_routine_payload,
    order_form,
    routine_entries,
    unarchive_form,
)
from .snapshots import save_snapshot

_SECTION_BY_GROUP: dict[int, Section] = {g: s for s, g in LIST_GROUP_BY_SECTION.items()}
_DRY = "Dry run — nothing sent. Re-run with dry_run=false to apply."
_APPLIED = (
    "Applied on the SmartGym server and verified; your iPhone and Mac show it after their "
    "next refresh."
)


class WriteVerifyError(RuntimeError):
    """The server accepted a write but the routine now differs from the plan."""


class RoutineId(BaseModel):
    identifier: int
    name: str


class EditResult(BaseModel):
    dry_run: bool
    routine: RoutineId
    changes: ChangeSet
    requests: list[str]
    snapshot: str | None
    notice: str


class CreatePlan(BaseModel):
    name: str
    resolutions: list[ExerciseResolution]
    exercise_count: int
    set_count: int
    warnings: list[str]


class CreateResult(BaseModel):
    dry_run: bool
    plan: list[CreatePlan]
    created: list[RoutineId]
    notice: str


class ArchiveResult(BaseModel):
    dry_run: bool
    archived: bool
    routines: list[RoutineId]
    skipped: list[RoutineId]
    notice: str


def _now_local() -> datetime:
    return datetime.now().astimezone()


def compare_views(expected: RoutineView, actual: RoutineView) -> list[str]:
    problems: list[str] = []
    for field in ("name", "days", "goal", "note"):
        exp, act = getattr(expected, field), getattr(actual, field)
        if exp != act:
            problems.append(f"{field}: expected {exp!r}, got {act!r}")
    if len(expected.exercises) != len(actual.exercises):
        problems.append(
            f"exercise count: expected {len(expected.exercises)}, got {len(actual.exercises)}"
        )
    for i, (e, a) in enumerate(zip(expected.exercises, actual.exercises, strict=False)):
        for field in ("catalog_id", "section", "rest_seconds", "note", "sets"):
            ev, av = getattr(e, field), getattr(a, field)
            if ev != av:
                problems.append(
                    f"exercise #{i + 1} (catalog {e.catalog_id}) {field}: "
                    f"expected {ev!r}, got {av!r}"
                )
    return problems


def _added_server_ids(response: Mapping[str, Any], hashids: Sequence[int]) -> list[int]:
    mapping: dict[int, int] = {}
    for item in response.get("exercises") or []:
        try:
            mapping[int(item["original_id"])] = int(item["server_id"])
        except (KeyError, TypeError, ValueError):
            continue
    missing = [h for h in hashids if h not in mapping]
    if missing:
        raise ApiPayloadError(
            f"routine/update/ returned no server ids for the added exercises {missing}."
        )
    return [mapping[h] for h in hashids]


def _payload_view(payload: Mapping[str, Any]) -> RoutineView:
    return RoutineView(
        name=str(payload["name"]),
        days=payload.get("days") or None,
        goal=payload.get("goal") or None,
        note=payload.get("note") or None,
        exercises=[
            ExerciseView(
                catalog_id=int(e["id"]),
                section=_SECTION_BY_GROUP[int(e["listGroup"])],
                rest_seconds=int(e["pause"]),
                note=e.get("note") or None,
                sets=[
                    (round(float(s["secondValue"]), 3), round(float(s["thirdValue"]), 3))
                    for s in e["sets"]
                ],
            )
            for e in payload["exercises"]
        ],
    )


class RoutineService:
    def __init__(
        self,
        client: ApiCalls,
        store: AccountStore,
        catalog: ExerciseCatalog,
        bundle: Mapping[int, CatalogExercise],
        *,
        backup_dir: Path,
        timezone: str,
        now: Callable[[], datetime] = _now_local,
        mint: Mint = mint_unique_hashid,
        move_in_place: bool = SECTION_MOVE_IN_PLACE,
    ) -> None:
        self._client = client
        self._store = store
        self._catalog = catalog
        self._bundle = bundle
        self._backup_dir = backup_dir
        self._tz = timezone
        self._now = now
        self._mint = mint
        self._move_in_place = move_in_place

    # ------------------------------------------------------------------ edits
    def edit(
        self, ref: str | int, build: Callable[[Routine], DesiredRoutine], *, dry_run: bool
    ) -> EditResult:
        return self._edit(self._store.resolve(ref).identifier, build, dry_run=dry_run)

    def edit_exercise(
        self, exercise_id: int, build: Callable[[Routine], DesiredRoutine], *, dry_run: bool
    ) -> EditResult:
        routine = self._store.routine_of_exercise(exercise_id)
        return self._edit(routine.identifier, build, dry_run=dry_run)

    def _edit(
        self, identifier: int, build: Callable[[Routine], DesiredRoutine], *, dry_run: bool
    ) -> EditResult:
        raw = self._store.routine_raw(identifier)
        current = parse_routine(raw)
        handle = RoutineId(identifier=current.identifier, name=current.name)
        cs = diff_routine(current, build(current), self._catalog)
        if not self._move_in_place:
            cs = moves_as_readd(cs, current)
        if cs.is_empty:
            return EditResult(
                dry_run=dry_run, routine=handle, changes=cs, requests=[], snapshot=None,
                notice="Nothing to change — the routine already looks like that.",
            )
        now = self._now()
        enc = encode_change(cs, current, self._bundle, timezone=self._tz, now=now, mint=self._mint)
        planned = [
            name
            for name, needed in (
                ("routine/update/", enc.structure is not None),
                ("routine/update/ (order)", cs.final_order is not None),
                ("routine/updateExercise/", enc.exercise_edits is not None),
            )
            if needed
        ]
        if dry_run:
            return EditResult(
                dry_run=True, routine=handle, changes=cs, requests=planned, snapshot=None,
                notice=_DRY,
            )
        snapshot = save_snapshot(self._backup_dir, raw, now=now)
        sent: list[str] = []
        try:
            added_ids: list[int] = []
            if enc.structure is not None:
                response = self._client.post("routine/update/", enc.structure)
                sent.append("routine/update/")
                added_ids = _added_server_ids(response, enc.added_hashids)
            order = order_form(cs, current, added_ids, timezone=self._tz)
            if order is not None:
                self._client.post("routine/update/", order)
                sent.append("routine/update/ (order)")
            if enc.exercise_edits is not None:
                self._client.post("routine/updateExercise/", enc.exercise_edits)
                sent.append("routine/updateExercise/")
        except (ApiError, ApiPayloadError) as exc:
            self._store.invalidate()
            progress = (
                f" Already sent: {', '.join(sent) or 'nothing'}. Snapshot of the routine "
                f"before this edit: {snapshot}. Re-read the routine before retrying."
            )
            if isinstance(exc, ApiError):
                raise type(exc)(f"{exc}{progress}", code=exc.code) from None
            raise ApiPayloadError(f"{exc}{progress}") from None
        self._store.invalidate()
        after = view_of(parse_routine(self._store.routine_raw(current.identifier)))
        problems = compare_views(cs.expected, after)
        if problems:
            raise WriteVerifyError(
                "SmartGym accepted the edit, but the routine now differs from the plan: "
                + "; ".join(problems)
                + f". Snapshot of the routine before this edit: {snapshot}."
            )
        return EditResult(
            dry_run=False, routine=handle, changes=cs, requests=sent, snapshot=str(snapshot),
            notice=_APPLIED,
        )

    # ----------------------------------------------------------------- create
    def create(self, specs: Sequence[RoutineSpec], *, dry_run: bool) -> CreateResult:
        data = self._store.data()
        taken = {r.name.strip().lower() for r in data.routines if not r.removed}
        problems: list[str] = []
        plans: list[CreatePlan] = []
        resolved: list[list[CatalogExercise]] = []
        seen: set[str] = set()
        for spec in specs:
            key = spec.name.strip().lower()
            if key in taken:
                problems.append(f"Routine {spec.name!r} already exists (archive or rename it).")
            if key in seen:
                problems.append(f"Routine {spec.name!r} appears twice in this program.")
            seen.add(key)
            entries = routine_entries(spec)
            resolutions: list[ExerciseResolution] = []
            cats: list[CatalogExercise] = []
            warnings: list[str] = []
            for section, ex in entries:
                try:
                    res = self._catalog.resolve(ex.exercise)
                except UnresolvedExercise as exc:
                    problems.append(f"{spec.name} / {section}: {exc}")
                    continue
                cat = self._bundle.get(res.z_pk)
                if cat is None:
                    problems.append(
                        f"{spec.name} / {section}: {res.resolved_name!r} is not in the app's "
                        "exercise catalog."
                    )
                    continue
                if res.fuzzy:
                    warnings.append(
                        f"Fuzzy match: {res.input!r} → {res.resolved_name!r} "
                        f"(confidence {res.confidence})."
                    )
                if not ex.sets:
                    warnings.append(
                        f"{res.resolved_name!r}: no sets given — defaulting to 1 set of "
                        f"{DEFAULT_SET.reps:g} reps (bodyweight)."
                    )
                resolutions.append(res)
                cats.append(cat)
            plans.append(
                CreatePlan(
                    name=spec.name,
                    resolutions=resolutions,
                    exercise_count=len(entries),
                    set_count=sum(len(ex.sets or [DEFAULT_SET]) for _, ex in entries),
                    warnings=warnings,
                )
            )
            resolved.append(cats)
        if problems:
            raise DiffError("Rejected — nothing was created:\n- " + "\n- ".join(problems))
        if dry_run:
            return CreateResult(dry_run=True, plan=plans, created=[], notice=_DRY)

        now = self._now()
        top = max((r.number for r in data.routines), default=0)
        payloads = [
            new_routine_payload(spec, cats, number=top + i + 1, now=now, mint=self._mint)
            for i, (spec, cats) in enumerate(zip(specs, resolved, strict=True))
        ]
        self._client.post("routine/add/", add_routines_form(payloads, timezone=self._tz))
        self._store.invalidate()
        by_hash = {r.unique_hashid: r for r in self._store.data().routines}
        created: list[RoutineId] = []
        mismatches: list[str] = []
        for spec, payload in zip(specs, payloads, strict=True):
            routine = by_hash.get(int(payload["uniqueHashID"]))
            if routine is None:
                mismatches.append(f"{spec.name!r} is missing after the create")
                continue
            created.append(RoutineId(identifier=routine.identifier, name=routine.name))
            mismatches += [
                f"{spec.name}: {p}" for p in compare_views(_payload_view(payload), view_of(routine))
            ]
        if mismatches:
            raise WriteVerifyError("SmartGym accepted the create, but: " + "; ".join(mismatches))
        return CreateResult(dry_run=False, plan=plans, created=created, notice=_APPLIED)

    # ---------------------------------------------------------------- archive
    def set_archived(
        self, refs: Sequence[str | int], *, archived: bool, dry_run: bool
    ) -> ArchiveResult:
        targets = [self._store.resolve(r) for r in refs]
        todo = [t for t in targets if t.archived != archived]
        handles = [RoutineId(identifier=t.identifier, name=t.name) for t in todo]
        skipped = [
            RoutineId(identifier=t.identifier, name=t.name) for t in targets if t.archived == archived
        ]
        if dry_run or not todo:
            return ArchiveResult(
                dry_run=dry_run, archived=archived, routines=handles, skipped=skipped,
                notice=_DRY if dry_run else "Nothing to change.",
            )
        for t in todo:
            if archived:
                self._client.post("routine/archive/", archive_form(t.identifier))
            else:
                self._client.post("routine/unarchive/", unarchive_form(t.identifier))
        self._store.invalidate()
        state = {r.identifier: r.archived for r in self._store.data().routines}
        wrong = [t.name for t in todo if state.get(t.identifier) != archived]
        if wrong:
            raise WriteVerifyError(
                f"SmartGym accepted the request, but these routines are not "
                f"{'archived' if archived else 'active'}: {', '.join(wrong)}."
            )
        return ArchiveResult(
            dry_run=False, archived=archived, routines=handles, skipped=skipped, notice=_APPLIED
        )


__all__ = [
    "ArchiveResult",
    "CreatePlan",
    "CreateResult",
    "EditResult",
    "RoutineId",
    "RoutineService",
    "WriteVerifyError",
    "compare_views",
]
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_builders.py tests/test_service.py -v && uv run pytest -q && uv run mypy src && uv run ruff check src tests && uv run ruff format src tests`
Expected: 10 + 11 passed; full suite passes; mypy/ruff clean.

- [ ] **Step 7: Commit**

```bash
git add src/smartgym_mcp/builders.py src/smartgym_mcp/service.py tests/test_builders.py tests/test_service.py
git commit -m "Add verified edit, create and archive service over the API"
```

---

### Task 9: Read views over the account (`reads.py`) + bundle equipment

**Files:**
- Create: `src/smartgym_mcp/reads.py`
- Modify: `src/smartgym_mcp/catalog.py` (`CatalogEquipment`, `load_bundle_equipment`)
- Test: `tests/test_reads.py`, `tests/test_bundle_catalog.py` (extend)

**Interfaces:**
- Consumes: `model.AccountData`, `Routine`, `RoutineExercise`, `SECTIONS` (Task 3); `catalog.read_catalog` (existing).
- Produces (output models of the read tools in Task 10):
  - `catalog.CatalogEquipment` (frozen dataclass): `id: int`, `name: str` (English), `category: int`; `catalog.load_bundle_equipment(cfg: Config) -> list[CatalogEquipment]`
  - `reads.RoutineSummary`: `identifier`, `name`, `days`, `archived`, `warmup: int`, `main: int`, `cooldown: int` (exercise counts); `reads.RoutineListResult`: `count`, `routines`
  - `reads.SetEntry`: `set_no: int`, `reps: float`, `weight_kg: float`; `reads.SessionEntry`: `date: str` (local `YYYY-MM-DD`), `sets: list[SetEntry]`
  - `reads.ExerciseEntry`: `exercise_id: int` (server identifier — what write tools take), `name`, `rest_seconds`, `note`, `template_sets: list[SetEntry]`, `sessions: list[SessionEntry]` (newest first), `top_set: SetEntry | None`, `total_volume: float`
  - `reads.RoutineDetail`: `identifier`, `name`, `days`, `goal`, `note`, `archived`, `warmup / main / cooldown: list[ExerciseEntry]`
  - `reads.WorkoutSession`: `identifier`, `date` (local `YYYY-MM-DD HH:MM`), `routine: str | None`, `duration_min`, `calories`, `avg_hr`, `max_hr`; `reads.WorkoutHistoryResult`: `total`, `count`, `offset`, `has_more`, `next_offset`, `sessions`
  - `reads.EquipmentItem`: `id`, `name`, `owned`; `reads.EquipmentListResult`: `count`, `equipment`, `dumbbell_weights`, `kettlebell_weights`
  - `reads.list_routines(data, *, include_archived=False)`, `reads.routine_detail(routine, data, *, history_depth=5, tz=None)`, `reads.workout_history(data, *, days=7, date_from=None, date_to=None, routine=None, limit=20, offset=0, today=None, tz=None)`, `reads.equipment(data, catalog, *, owned_only=True)`; `ALLOWED_RANGE_DAYS = (7, 14, 30)`. Server times are UTC; `tz=None` means the Mac's local zone.

- [ ] **Step 1: Write the failing tests**

`tests/test_reads.py`:

```python
"""reads.py: AccountData → read-tool views. Pure; UTC + fixed 'today' for determinism."""

from __future__ import annotations

import json
from datetime import UTC, date
from pathlib import Path

import pytest

from smartgym_mcp.catalog import CatalogEquipment
from smartgym_mcp.model import parse_history_all
from smartgym_mcp.reads import equipment, list_routines, routine_detail, workout_history

DATA = parse_history_all(
    json.loads(
        (Path(__file__).parent / "fixtures" / "api" / "history_all_synthetic.json").read_text(
            encoding="utf-8"
        )
    )
)
TODAY = date(2026, 9, 30)


def test_list_routines_hides_archived_and_removed_and_counts_sections() -> None:
    (only,) = list_routines(DATA).routines
    assert (only.name, only.warmup, only.main, only.cooldown) == ("ZZ-FB — Test", 1, 1, 1)
    names = [r.name for r in list_routines(DATA, include_archived=True).routines]
    assert names == ["ZZ-FB — Test", "ZZ-Old"]


def test_routine_detail_groups_sections_and_sessions() -> None:
    detail = routine_detail(DATA.routines[0], DATA, history_depth=5, tz=UTC)
    assert [e.name for e in detail.warmup] == ["Shoulder Circling"]
    assert [e.name for e in detail.cooldown] == ["Cross Arm Stretch"]
    (chest,) = detail.main
    assert (chest.exercise_id, chest.rest_seconds, chest.note) == (40000012, 90, "Slow")
    assert [(s.set_no, s.reps, s.weight_kg) for s in chest.template_sets] == [(1, 10.0, 42.5)]
    assert [s.date for s in chest.sessions] == ["2026-09-21", "2026-09-14"]
    assert [(s.reps, s.weight_kg) for s in chest.sessions[1].sets] == [(10.0, 40.0), (8.0, 42.5)]
    assert chest.top_set is not None and chest.top_set.weight_kg == 42.5
    assert chest.total_volume == 425.0


def test_routine_detail_history_depth_limits_sessions() -> None:
    detail = routine_detail(DATA.routines[0], DATA, history_depth=1, tz=UTC)
    assert [s.date for s in detail.main[0].sessions] == ["2026-09-21"]


def test_workout_history_newest_first_with_routine_names() -> None:
    result = workout_history(DATA, days=30, today=TODAY, tz=UTC)
    assert [s.identifier for s in result.sessions] == [7000002, 7000001]
    newest, older = result.sessions
    assert (newest.date, newest.routine, newest.duration_min, newest.calories) == (
        "2026-09-21 08:00", "ZZ-FB — Test", 45, None
    )
    assert (older.duration_min, older.calories, older.avg_hr, older.max_hr) == (60, 350, 120, 160)


def test_workout_history_date_range_routine_filter_and_paging() -> None:
    ranged = workout_history(DATA, date_from="2026-09-20", date_to="2026-09-30", tz=UTC)
    assert [s.identifier for s in ranged.sessions] == [7000002]
    other = DATA.routines[1]
    assert workout_history(DATA, days=30, routine=other, today=TODAY, tz=UTC).total == 0
    page = workout_history(DATA, days=30, limit=1, today=TODAY, tz=UTC)
    assert (page.total, page.count, page.has_more, page.next_offset) == (2, 1, True, 1)


def test_workout_history_rejects_unsupported_preset() -> None:
    with pytest.raises(ValueError, match="days must be one of"):
        workout_history(DATA, days=9, today=TODAY, tz=UTC)


def test_equipment_owned_flags_and_weights() -> None:
    catalog = [
        CatalogEquipment(id=1, name="Barbell", category=1),
        CatalogEquipment(id=2, name="Dumbbell", category=1),
        CatalogEquipment(id=5, name="Kettlebell", category=2),
        CatalogEquipment(id=38, name="Resistance Band", category=3),
    ]
    owned = equipment(DATA, catalog)
    assert [i.id for i in owned.equipment] == [1, 2, 38]
    everything = equipment(DATA, catalog, owned_only=False)
    assert [(i.id, i.owned) for i in everything.equipment] == [
        (1, True), (2, True), (5, False), (38, True)
    ]
    assert (owned.dumbbell_weights, owned.kettlebell_weights) == (None, "8,12")
```

Append to `tests/test_bundle_catalog.py`:

```python
from smartgym_mcp.catalog import load_bundle_equipment


def test_load_bundle_equipment_uses_english_names(bundle_cfg: Config) -> None:
    resources = bundle_cfg.app_bundle / "Contents" / "Resources"
    (resources / "Equipments.json").write_text(
        json.dumps({"equipments": [
            {"identifier": "1", "category": "1", "name": {"en": "Barbell", "de": "Langhantel"}},
            {"identifier": "38", "category": "3", "name": "Resistance Band"},
        ]}),
        encoding="utf-8",
    )
    assert [(e.id, e.name, e.category) for e in load_bundle_equipment(bundle_cfg)] == [
        (1, "Barbell", 1), (38, "Resistance Band", 3)
    ]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_reads.py tests/test_bundle_catalog.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'smartgym_mcp.reads'`, `ImportError: cannot import name 'CatalogEquipment'`.

- [ ] **Step 3: Implement**

Append to `src/smartgym_mcp/catalog.py`:

```python
@dataclass(frozen=True)
class CatalogEquipment:
    """One bundle equipment entry; `id` matches the ids in equipment lists."""

    id: int
    name: str
    category: int


def load_bundle_equipment(cfg: Config) -> list[CatalogEquipment]:
    raw = json.loads(read_catalog(cfg, "equipment"))["equipments"]
    out: list[CatalogEquipment] = []
    for e in raw:
        name = e.get("name")
        label = name.get("en") if isinstance(name, dict) else name
        out.append(
            CatalogEquipment(
                id=int(e["identifier"]),
                name=str(label or e["identifier"]),
                category=int(e.get("category") or 0),
            )
        )
    return out
```

`src/smartgym_mcp/reads.py`:

```python
"""Read-tool views over AccountData (API-client spec §6, §10). Pure — no I/O.

Server times are UTC strings; views show the Mac's local time. A routine
exercise's "sessions" are its logged sets grouped by the workout they belong
to (history → set ids), newest first.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta, tzinfo

from pydantic import BaseModel

from .catalog import CatalogEquipment
from .model import SECTIONS, AccountData, LoggedSet, Routine, RoutineExercise

ALLOWED_RANGE_DAYS = (7, 14, 30)


class RoutineSummary(BaseModel):
    identifier: int
    name: str
    days: str | None
    archived: bool
    warmup: int
    main: int
    cooldown: int


class RoutineListResult(BaseModel):
    count: int
    routines: list[RoutineSummary]


class SetEntry(BaseModel):
    set_no: int
    reps: float
    weight_kg: float  # 0.0 = bodyweight / untracked


class SessionEntry(BaseModel):
    date: str
    sets: list[SetEntry]


class ExerciseEntry(BaseModel):
    exercise_id: int
    name: str
    rest_seconds: int
    note: str | None
    template_sets: list[SetEntry]
    sessions: list[SessionEntry]
    top_set: SetEntry | None
    total_volume: float


class RoutineDetail(BaseModel):
    identifier: int
    name: str
    days: str | None
    goal: str | None
    note: str | None
    archived: bool
    warmup: list[ExerciseEntry]
    main: list[ExerciseEntry]
    cooldown: list[ExerciseEntry]


class WorkoutSession(BaseModel):
    identifier: int
    date: str
    routine: str | None
    duration_min: int
    calories: int | None
    avg_hr: int | None
    max_hr: int | None


class WorkoutHistoryResult(BaseModel):
    total: int
    count: int
    offset: int
    has_more: bool
    next_offset: int | None
    sessions: list[WorkoutSession]


class EquipmentItem(BaseModel):
    id: int
    name: str
    owned: bool


class EquipmentListResult(BaseModel):
    count: int
    equipment: list[EquipmentItem]
    dumbbell_weights: str | None
    kettlebell_weights: str | None


def _local_tz() -> tzinfo:
    tz = datetime.now().astimezone().tzinfo
    return tz if tz is not None else UTC


def _local(server_time: str, tz: tzinfo) -> datetime:
    parsed = datetime.strptime(server_time[:19], "%Y-%m-%d %H:%M:%S")
    return parsed.replace(tzinfo=UTC).astimezone(tz)


def list_routines(data: AccountData, *, include_archived: bool = False) -> RoutineListResult:
    routines = sorted(
        (r for r in data.routines if not r.removed and (include_archived or not r.archived)),
        key=lambda r: r.name.lower(),
    )
    items = [
        RoutineSummary(
            identifier=r.identifier,
            name=r.name,
            days=r.days,
            archived=r.archived,
            **{s: len(r.section(s)) for s in SECTIONS},
        )
        for r in routines
    ]
    return RoutineListResult(count=len(items), routines=items)


def _sessions(
    ex: RoutineExercise, workout_day: dict[int, str], depth: int, tz: tzinfo
) -> list[SessionEntry]:
    groups: dict[str, list[LoggedSet]] = defaultdict(list)
    for s in ex.logged_sets:
        day = workout_day.get(s.identifier) or _local(s.logged_at, tz).date().isoformat()
        groups[day].append(s)
    out: list[SessionEntry] = []
    for day in sorted(groups, reverse=True)[: max(depth, 0)]:
        ordered = sorted(groups[day], key=lambda s: (s.index, s.logged_at))
        out.append(
            SessionEntry(
                date=day,
                sets=[
                    SetEntry(set_no=i + 1, reps=s.reps, weight_kg=s.weight_kg)
                    for i, s in enumerate(ordered)
                ],
            )
        )
    return out


def routine_detail(
    routine: Routine, data: AccountData, *, history_depth: int = 5, tz: tzinfo | None = None
) -> RoutineDetail:
    zone = tz or _local_tz()
    workout_day = {
        sid: _local(w.start, zone).date().isoformat() for w in data.workouts for sid in w.set_ids
    }

    def entry(e: RoutineExercise) -> ExerciseEntry:
        sessions = _sessions(e, workout_day, history_depth, zone)
        latest = sessions[0].sets if sessions else []
        top = max(latest, key=lambda s: (s.weight_kg, s.reps)) if latest else None
        return ExerciseEntry(
            exercise_id=e.identifier,
            name=e.name,
            rest_seconds=e.rest_seconds,
            note=e.note,
            template_sets=[
                SetEntry(set_no=i + 1, reps=s.reps, weight_kg=s.weight_kg)
                for i, s in enumerate(e.template_sets)
            ],
            sessions=sessions,
            top_set=top,
            total_volume=round(sum(s.reps * s.weight_kg for s in latest), 3),
        )

    return RoutineDetail(
        identifier=routine.identifier,
        name=routine.name,
        days=routine.days,
        goal=routine.goal,
        note=routine.note,
        archived=routine.archived,
        warmup=[entry(e) for e in routine.section("warmup")],
        main=[entry(e) for e in routine.section("main")],
        cooldown=[entry(e) for e in routine.section("cooldown")],
    )


def workout_history(
    data: AccountData,
    *,
    days: int = 7,
    date_from: str | None = None,
    date_to: str | None = None,
    routine: Routine | None = None,
    limit: int = 20,
    offset: int = 0,
    today: date | None = None,
    tz: tzinfo | None = None,
) -> WorkoutHistoryResult:
    zone = tz or _local_tz()
    if date_from or date_to:
        start = date.fromisoformat(date_from) if date_from else date.min
        end = date.fromisoformat(date_to) if date_to else date.max
    else:
        if days not in ALLOWED_RANGE_DAYS:
            raise ValueError(f"days must be one of {ALLOWED_RANGE_DAYS}, got {days}")
        end = today or datetime.now(zone).date()
        start = end - timedelta(days=days)
    names = {r.identifier: r.name for r in data.routines}
    picked: list[tuple[datetime, int]] = []
    for i, w in enumerate(data.workouts):
        local = _local(w.start, zone)
        if not start <= local.date() <= end:
            continue
        if routine is not None and w.routine_identifier != routine.identifier:
            continue
        picked.append((local, i))
    picked.sort(key=lambda p: p[0], reverse=True)
    page = picked[offset : offset + limit]
    sessions = []
    for local, i in page:
        w = data.workouts[i]
        sessions.append(
            WorkoutSession(
                identifier=w.identifier,
                date=local.strftime("%Y-%m-%d %H:%M"),
                routine=names.get(w.routine_identifier) if w.routine_identifier else None,
                duration_min=w.duration_s // 60,
                calories=w.calories,
                avg_hr=w.avg_hr,
                max_hr=w.max_hr,
            )
        )
    has_more = offset + len(sessions) < len(picked)
    return WorkoutHistoryResult(
        total=len(picked),
        count=len(sessions),
        offset=offset,
        has_more=has_more,
        next_offset=offset + len(sessions) if has_more else None,
        sessions=sessions,
    )


def equipment(
    data: AccountData, catalog: Sequence[CatalogEquipment], *, owned_only: bool = True
) -> EquipmentListResult:
    lists = [e for e in data.equipment_lists if e.selected] or data.equipment_lists[:1]
    owned = {i for e in lists for i in e.equipment_ids}
    items = [
        EquipmentItem(id=c.id, name=c.name, owned=c.id in owned)
        for c in sorted(catalog, key=lambda c: (c.category, c.name))
    ]
    if owned_only:
        items = [i for i in items if i.owned]
    first = lists[0] if lists else None
    return EquipmentListResult(
        count=len(items),
        equipment=items,
        dumbbell_weights=first.dumbbell_weights if first else None,
        kettlebell_weights=first.kettlebell_weights if first else None,
    )


__all__ = [
    "ALLOWED_RANGE_DAYS",
    "EquipmentItem",
    "EquipmentListResult",
    "ExerciseEntry",
    "RoutineDetail",
    "RoutineListResult",
    "RoutineSummary",
    "SessionEntry",
    "SetEntry",
    "WorkoutHistoryResult",
    "WorkoutSession",
    "equipment",
    "list_routines",
    "routine_detail",
    "workout_history",
]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_reads.py tests/test_bundle_catalog.py -v && uv run pytest -q && uv run mypy src && uv run ruff check src tests && uv run ruff format src tests`
Expected: 7 + 1 new tests pass; full suite passes; mypy/ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/smartgym_mcp/reads.py src/smartgym_mcp/catalog.py tests/test_reads.py tests/test_bundle_catalog.py
git commit -m "Add read views with sections, sessions, history and equipment"
```

---

### Task 10: Server on the API, retire the DB path, docs

**Files:**
- Modify: `src/smartgym_mcp/server.py` (full replacement below)
- Modify: `src/smartgym_mcp/config.py` (drop DB fields), `src/smartgym_mcp/models.py` (keep input specs only), `src/smartgym_mcp/matching.py` (drop the DB loader; `z_pk` → `catalog_id`), `src/smartgym_mcp/diff.py` + `src/smartgym_mcp/service.py` (`res.z_pk` → `res.catalog_id`)
- Delete: `src/smartgym_mcp/db.py`, `lifecycle.py`, `writes.py`, `queries.py`; `tests/test_create_program.py`, `test_db_readonly.py`, `test_pk_and_hashid.py`, `test_read_tools.py`, `test_write_safety.py`, `test_write_tools.py`
- Modify: `tests/conftest.py` (drop live-DB fixtures), `tests/test_bundle_catalog.py` (`.z_pk` → `.catalog_id`)
- Create: `tests/test_matching.py`, `tests/test_server.py`
- Modify: `smartgym-mcp.spec` (bundle certifi CA data), `README.md`, `DESIGN.md`, `FEATURES.md`, `CLAUDE.md`

**Interfaces:**
- Consumes: everything from Tasks 2–9.
- Produces:
  - `config.Config(app_bundle: Path, backup_dir: Path, credentials_path: Path)`; `load_config()` reads `SMARTGYM_APP_BUNDLE`, `SMARTGYM_BACKUP_DIR`, `SMARTGYM_CREDENTIALS`
  - `models.ExerciseResolution.catalog_id: int` (was `z_pk`); `models` keeps only `SetSpec`, `ExerciseSpec`, `RoutineSpec`, `ExerciseResolution`, `FieldChange`
  - `server.Services` (dataclass: `client`, `store`, `service`, `equipment`), `server.build_services(cfg: Config) -> Services`, `server.AppContext(cfg, factory=build_services)` with `services() -> Services` (built on first use, retried after failure) and `close()`
  - MCP tools (15): `smartgym_health`, `smartgym_list_routines`, `smartgym_get_routine`, `smartgym_get_workout_history`, `smartgym_get_equipment`, `smartgym_create_program`, `smartgym_update_routine`, `smartgym_add_exercise`, `smartgym_move_exercise`, `smartgym_remove_exercise`, `smartgym_reorder_routine`, `smartgym_update_exercise`, `smartgym_apply_routine`, `smartgym_archive_routines`, `smartgym_unarchive_routine`; catalog resources unchanged.

- [ ] **Step 1: Write the failing server and matching tests**

`tests/test_matching.py` (replaces the DB-backed matching tests of `test_create_program.py`):

```python
"""matching.py: deterministic name → catalog id resolution (no DB)."""

from __future__ import annotations

import pytest

from smartgym_mcp.matching import ExerciseCatalog, UnresolvedExercise

CATALOG = ExerciseCatalog(
    [(1, "Dumbbell Bench Press"), (2, "Barbell Squat"), (194, "Push Up"), (20, "Plank")]
)


def test_exact_match_is_case_insensitive() -> None:
    res = CATALOG.resolve("push up")
    assert (res.catalog_id, res.resolved_name, res.fuzzy) == (194, "Push Up", False)


def test_numeric_ref_resolves_by_catalog_id() -> None:
    assert CATALOG.resolve("20").resolved_name == "Plank"
    with pytest.raises(UnresolvedExercise, match="999"):
        CATALOG.resolve("999")


def test_alias_expansion_gives_a_fuzzy_match() -> None:
    res = CATALOG.resolve("db bench press")
    assert (res.catalog_id, res.fuzzy) == (1, True)


def test_unresolvable_lists_candidates() -> None:
    with pytest.raises(UnresolvedExercise, match="Closest"):
        CATALOG.resolve("Zercher carry")
```

`tests/test_server.py`:

```python
"""server.py: tool wiring with injected services — no credentials file, no network."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fakes import FakeClient

from smartgym_mcp import server
from smartgym_mcp.api.store import AccountStore
from smartgym_mcp.config import load_config
from smartgym_mcp.matching import ExerciseCatalog
from smartgym_mcp.service import RoutineService

FIX = Path(__file__).parent / "fixtures" / "api"
ACCOUNT = json.loads((FIX / "history_all_synthetic.json").read_text(encoding="utf-8"))
EXPECTED_TOOLS = {
    "smartgym_health", "smartgym_list_routines", "smartgym_get_routine",
    "smartgym_get_workout_history", "smartgym_get_equipment", "smartgym_create_program",
    "smartgym_update_routine", "smartgym_add_exercise", "smartgym_move_exercise",
    "smartgym_remove_exercise", "smartgym_reorder_routine", "smartgym_update_exercise",
    "smartgym_apply_routine", "smartgym_archive_routines", "smartgym_unarchive_routine",
}


def _ctx(app: server.AppContext) -> Any:
    return SimpleNamespace(request_context=SimpleNamespace(lifespan_context=app))


def _fake_app(tmp_path: Path, client: FakeClient) -> server.AppContext:
    cfg = replace(load_config(), backup_dir=tmp_path)

    def factory(_cfg: Any) -> server.Services:
        store = AccountStore(client)
        catalog = ExerciseCatalog([(207, "Cable Chest Press")])
        service = RoutineService(client, store, catalog, {}, backup_dir=tmp_path,
                                 timezone="Europe/Madrid")
        return server.Services(client=client, store=store, service=service, equipment=[])  # type: ignore[arg-type]

    return server.AppContext(cfg=cfg, factory=factory)


def test_all_tools_registered() -> None:
    tools = asyncio.run(server.mcp.list_tools())
    assert {t.name for t in tools} == EXPECTED_TOOLS


def test_health_reports_missing_credentials(tmp_path: Path) -> None:
    cfg = replace(load_config(), credentials_path=tmp_path / "none.json")
    status = server.smartgym_health(_ctx(server.AppContext(cfg=cfg)))
    assert not status.ok
    assert status.problem is not None and "capture_credentials.py" in status.problem


def test_list_routines_through_injected_services(tmp_path: Path) -> None:
    app = _fake_app(tmp_path, FakeClient({"history/all/1/": ACCOUNT}))
    result = server.smartgym_list_routines(_ctx(app))
    assert [r.name for r in result.routines] == ["ZZ-FB — Test"]


def test_update_exercise_dry_run_sends_nothing(tmp_path: Path) -> None:
    single = {"code": "SUCCESS", "routines": [ACCOUNT["routines"][0]]}
    client = FakeClient({"history/all/1/": ACCOUNT, "routine/single/3000001/": single})
    result = server.smartgym_update_exercise(
        _ctx(_fake_app(tmp_path, client)), exercise_id=40000012, rest_seconds=120
    )
    assert result.dry_run and result.requests == ["routine/update/"]
    assert client.sent == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_matching.py tests/test_server.py -v`
Expected: FAIL — `AttributeError: 'ExerciseResolution' object has no attribute 'catalog_id'` and `AttributeError: module 'smartgym_mcp.server' has no attribute 'Services'`.

- [ ] **Step 3: Rename `z_pk` → `catalog_id` and drop the DB catalog loader**

Run `grep -rn "z_pk" src tests` and change every hit that belongs to `ExerciseResolution` / `ExerciseCatalog` (the DB-bound files are deleted in Step 5, so ignore hits in them):
- `models.py`: `class ExerciseResolution` field `z_pk: int` → `catalog_id: int`.
- `matching.py`: module docstring → "Deterministic exercise-name resolution against the app-bundle catalog. Numeric → catalog id; else exact case-insensitive name; else normalized token-set fuzzy match with threshold 0.85. Anything below threshold fails with top candidates — nothing silently wrong is ever resolved. No LLM."; delete `import sqlite3` and the `load` classmethod; `_Entry.z_pk` → `catalog_id`; `self._by_pk` → `self._by_id`; every `z_pk=` keyword → `catalog_id=`; messages `z_pk={s}` → `id={s}` and `(z_pk={e.z_pk}, …)` → `(id={e.catalog_id}, …)`; `from_bundle` docstring → "Catalog from the app bundle."; `resolve` docstring "(name or numeric catalog id)".
- `diff.py`: `catalog_id=slot.resolution.z_pk` → `catalog_id=slot.resolution.catalog_id`.
- `service.py`: `self._bundle.get(res.z_pk)` → `self._bundle.get(res.catalog_id)`.
- `tests/test_bundle_catalog.py`: `.z_pk == 194` → `.catalog_id == 194`.

- [ ] **Step 4: Trim `models.py` and `config.py`**

`models.py`: delete every class except `SetSpec`, `ExerciseSpec`, `RoutineSpec`, `ExerciseResolution`, `FieldChange`; module docstring → `"""Tool input specs shared by the diff and the service (API-client spec §6)."""`.

`config.py` (full replacement):

```python
"""Configuration: environment overrides with defaults. No filesystem side effects."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_APP_BUNDLE = "/Applications/SmartGym.app"
DEFAULT_BACKUP_DIR = "~/.smartgym-mcp/backups"
DEFAULT_CREDENTIALS = "~/.smartgym-mcp/credentials.json"
# SmartGym version whose wire format the API client was verified against (spec §4).
VERIFIED_APP_VERSION = "8.0.3"


@dataclass(frozen=True)
class Config:
    app_bundle: Path
    backup_dir: Path
    credentials_path: Path


def _resolve(value: str) -> Path:
    return Path(value).expanduser().resolve()


def load_config() -> Config:
    return Config(
        app_bundle=_resolve(os.environ.get("SMARTGYM_APP_BUNDLE", DEFAULT_APP_BUNDLE)),
        backup_dir=_resolve(os.environ.get("SMARTGYM_BACKUP_DIR", DEFAULT_BACKUP_DIR)),
        credentials_path=_resolve(os.environ.get("SMARTGYM_CREDENTIALS", DEFAULT_CREDENTIALS)),
    )
```

- [ ] **Step 5: Delete the DB path**

```bash
git rm src/smartgym_mcp/db.py src/smartgym_mcp/lifecycle.py src/smartgym_mcp/writes.py src/smartgym_mcp/queries.py
git rm tests/test_create_program.py tests/test_db_readonly.py tests/test_pk_and_hashid.py tests/test_read_tools.py tests/test_write_safety.py tests/test_write_tools.py
```

`tests/conftest.py` (full replacement):

```python
"""Test setup: make `src` importable. Tests never touch the network, the account, or the DB."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
```

- [ ] **Step 6: Replace `src/smartgym_mcp/server.py`**

```python
"""FastMCP app over the SmartGym API (API-client spec §5, §6).

Tools are thin: parse input → store / service / reads. Credentials and the API
client are built on first use, so a missing credentials file becomes a clear
tool error (and a smartgym_health report) instead of a server that won't start.
"""

from __future__ import annotations

import logging
import sys
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Literal

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel

from . import __version__, builders, catalog, reads
from .api.auth import CredentialsError, FileCredentials
from .api.client import ApiClient, ApiError
from .api.store import AccountStore
from .config import VERIFIED_APP_VERSION, Config, load_config
from .diff import DesiredExercise, DesiredRoutine
from .matching import ExerciseCatalog
from .model import Section
from .models import RoutineSpec, SetSpec
from .payloads import local_timezone_name
from .service import ArchiveResult, CreateResult, EditResult, RoutineService

logger = logging.getLogger("smartgym_mcp")


def _read_only(title: str) -> ToolAnnotations:
    return ToolAnnotations(title=title, readOnlyHint=True, openWorldHint=True)


def _destructive(title: str) -> ToolAnnotations:
    return ToolAnnotations(
        title=title,
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    )


@dataclass
class Services:
    client: ApiClient
    store: AccountStore
    service: RoutineService
    equipment: list[catalog.CatalogEquipment]


def build_services(cfg: Config) -> Services:
    credentials = FileCredentials(cfg.credentials_path)
    client = ApiClient(
        credentials, app_version=catalog.installed_app_version(cfg) or VERIFIED_APP_VERSION
    )
    store = AccountStore(client)
    exercises = catalog.load_bundle_exercises(cfg)
    service = RoutineService(
        client,
        store,
        ExerciseCatalog.from_bundle(exercises),
        {e.id: e for e in exercises},
        backup_dir=cfg.backup_dir,
        timezone=local_timezone_name(),
    )
    return Services(
        client=client, store=store, service=service, equipment=catalog.load_bundle_equipment(cfg)
    )


@dataclass
class AppContext:
    cfg: Config
    factory: Callable[[Config], Services] = build_services
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _services: Services | None = None

    def services(self) -> Services:
        with self._lock:
            if self._services is None:
                self._services = self.factory(self.cfg)
            return self._services

    def close(self) -> None:
        if self._services is not None:
            self._services.client.close()


@asynccontextmanager
async def lifespan(_server: FastMCP) -> AsyncIterator[AppContext]:
    app = AppContext(cfg=load_config())
    logger.info("smartgym_mcp up; credentials=%s", app.cfg.credentials_path)
    try:
        yield app
    finally:
        app.close()


mcp = FastMCP("smartgym_mcp", lifespan=lifespan)


def _app(ctx: Context) -> AppContext:
    app: AppContext = ctx.request_context.lifespan_context
    return app


class HealthStatus(BaseModel):
    ok: bool
    routines: int | None
    app_version: str | None
    verified_app_version: str
    warning: str | None
    problem: str | None
    version: str


@mcp.tool(annotations=_read_only("Health check"))
def smartgym_health(ctx: Context) -> HealthStatus:
    """Check the credentials file, the SmartGym server, and the installed app version.

    ok=true means the server answered with your account data. `problem` explains
    what to fix otherwise (e.g. create the credentials file). `warning` appears when
    the installed SmartGym version differs from the one the MCP was verified against.
    """
    app = _app(ctx)
    installed = catalog.installed_app_version(app.cfg)
    warning = None
    if installed and installed != VERIFIED_APP_VERSION:
        warning = (
            f"SmartGym {installed} is installed; the MCP was verified against "
            f"{VERIFIED_APP_VERSION}. Re-run the live check on a ZZ- routine before "
            "trusting edits."
        )
    try:
        data = app.services().store.data()
    except (CredentialsError, ApiError, ValueError) as exc:
        return HealthStatus(
            ok=False, routines=None, app_version=installed,
            verified_app_version=VERIFIED_APP_VERSION, warning=warning, problem=str(exc),
            version=__version__,
        )
    return HealthStatus(
        ok=True,
        routines=sum(1 for r in data.routines if not r.removed and not r.archived),
        app_version=installed,
        verified_app_version=VERIFIED_APP_VERSION,
        warning=warning,
        problem=None,
        version=__version__,
    )


@mcp.tool(annotations=_read_only("List routines"))
def smartgym_list_routines(ctx: Context, include_archived: bool = False) -> reads.RoutineListResult:
    """List routines: id, name, days, and exercise counts per section (warm-up/main/cool-down).

    Archived routines are listed only with include_archived=true. Use the id (or the
    name) in the other tools.
    """
    return reads.list_routines(_app(ctx).services().store.data(), include_archived=include_archived)


@mcp.tool(annotations=_read_only("Get routine detail"))
def smartgym_get_routine(ctx: Context, routine: str, history_depth: int = 5) -> reads.RoutineDetail:
    """Get a routine split into warmup, main and cooldown sections.

    `routine` is a name (case-insensitive, partial allowed) or id. Each exercise shows
    its exercise_id (what the edit tools take), rest, note, planned template sets, and
    up to `history_depth` recent sessions (reps + weight_kg; 0.0 = bodyweight) with the
    latest session's top set and total volume.
    """
    store = _app(ctx).services().store
    return reads.routine_detail(store.resolve(routine), store.data(), history_depth=history_depth)


@mcp.tool(annotations=_read_only("Get workout history"))
def smartgym_get_workout_history(
    ctx: Context,
    days: Literal[7, 14, 30] = 7,
    date_from: str | None = None,
    date_to: str | None = None,
    routine: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> reads.WorkoutHistoryResult:
    """List past workouts (newest first) with duration, calories and heart rate.

    Defaults to the last 7 days (or 14/30 via `days`); explicit `date_from` / `date_to`
    (YYYY-MM-DD, inclusive, local time) override it. Optional `routine` filter (name or
    id). Paginated: total, has_more, next_offset.
    """
    store = _app(ctx).services().store
    return reads.workout_history(
        store.data(),
        days=days,
        date_from=date_from,
        date_to=date_to,
        routine=store.resolve(routine) if routine else None,
        limit=limit,
        offset=offset,
    )


@mcp.tool(annotations=_read_only("Get equipment"))
def smartgym_get_equipment(ctx: Context, owned_only: bool = True) -> reads.EquipmentListResult:
    """List equipment (owned_only=true: only what you selected) plus dumbbell/kettlebell weights."""
    services = _app(ctx).services()
    return reads.equipment(services.store.data(), services.equipment, owned_only=owned_only)


@mcp.tool(annotations=_destructive("Create program"))
def smartgym_create_program(
    ctx: Context, routines: list[RoutineSpec], dry_run: bool = True
) -> CreateResult:
    """Create one or more routines (a program) on the SmartGym server — all devices get them.

    Each routine: name (must not match an existing routine), optional days/goal/note, and
    three ordered sections — `warmup` (optional), `exercises` (= main, required),
    `cooldown` (optional). Exercises are catalog names (fuzzy-matched, deterministic) or
    catalog ids, with optional rest_seconds, note and template sets (reps + weight_kg;
    omitted = one 1x10 set, flagged). Validation is all-or-nothing.
    dry_run=true (default) returns the plan and sends NOTHING; dry_run=false creates the
    routines and verifies them on the server.
    """
    return _app(ctx).services().service.create(routines, dry_run=dry_run)


@mcp.tool(annotations=_destructive("Update routine"))
def smartgym_update_routine(
    ctx: Context,
    routine: str,
    name: str | None = None,
    days: str | None = None,
    goal: str | None = None,
    note: str | None = None,
    dry_run: bool = True,
) -> EditResult:
    """Edit a routine's name, days, goal or note (only the fields you pass; "" clears).

    dry_run=true (default) shows old → new and sends NOTHING; dry_run=false snapshots the
    routine, sends the edit, and verifies it on the server.
    """
    desired = builders.update_routine(name=name, days=days, goal=goal, note=note)
    return _app(ctx).services().service.edit(routine, lambda _r: desired, dry_run=dry_run)


@mcp.tool(annotations=_destructive("Add exercise"))
def smartgym_add_exercise(
    ctx: Context,
    routine: str,
    exercise: str,
    section: Section = "main",
    position: int | None = None,
    rest_seconds: int | None = None,
    note: str | None = None,
    sets: list[SetSpec] | None = None,
    dry_run: bool = True,
) -> EditResult:
    """Add a catalog exercise to a routine section (warmup / main / cooldown).

    `position` counts within the section (0 = first; omitted = last). Optional rest,
    note and template sets (omitted = one 1x10 set, flagged). dry_run=true (default)
    sends NOTHING.
    """
    return _app(ctx).services().service.edit(
        routine,
        lambda r: builders.add_exercise(
            r, exercise, section=section, position=position, rest_seconds=rest_seconds,
            note=note, sets=sets,
        ),
        dry_run=dry_run,
    )


@mcp.tool(annotations=_destructive("Move exercise"))
def smartgym_move_exercise(
    ctx: Context,
    exercise_id: int,
    section: Section,
    position: int | None = None,
    dry_run: bool = True,
) -> EditResult:
    """Move an exercise to another section (or to another position in its section).

    `exercise_id` comes from smartgym_get_routine; `position` counts within the target
    section (omitted = last). dry_run=true (default) sends NOTHING.
    """
    return _app(ctx).services().service.edit_exercise(
        exercise_id,
        lambda r: builders.move_exercise(r, exercise_id, section=section, position=position),
        dry_run=dry_run,
    )


@mcp.tool(annotations=_destructive("Remove exercise"))
def smartgym_remove_exercise(ctx: Context, exercise_id: int, dry_run: bool = True) -> EditResult:
    """Remove an exercise from its routine (logged history is kept).

    `exercise_id` comes from smartgym_get_routine. dry_run=true (default) sends NOTHING.
    """
    return _app(ctx).services().service.edit_exercise(
        exercise_id, lambda r: builders.remove_exercise(r, exercise_id), dry_run=dry_run
    )


@mcp.tool(annotations=_destructive("Reorder routine"))
def smartgym_reorder_routine(
    ctx: Context,
    routine: str,
    warmup: list[int] | None = None,
    main: list[int] | None = None,
    cooldown: list[int] | None = None,
    dry_run: bool = True,
) -> EditResult:
    """Reorder exercises inside sections. Each list you pass must hold exactly that
    section's current exercise_ids, in the new order; omitted sections stay as they are.
    To change an exercise's section use smartgym_move_exercise. dry_run=true sends NOTHING.
    """
    return _app(ctx).services().service.edit(
        routine,
        lambda r: builders.reorder(r, warmup=warmup, main=main, cooldown=cooldown),
        dry_run=dry_run,
    )


@mcp.tool(annotations=_destructive("Update exercise"))
def smartgym_update_exercise(
    ctx: Context,
    exercise_id: int,
    rest_seconds: int | None = None,
    note: str | None = None,
    sets: list[SetSpec] | None = None,
    dry_run: bool = True,
) -> EditResult:
    """Change an exercise's rest time, note ("" clears) or template sets.

    `sets` is the FULL planned list [{reps, weight_kg}] — sets beyond it are removed,
    extra ones added. Logged history is never touched. dry_run=true (default) sends
    NOTHING.
    """
    return _app(ctx).services().service.edit_exercise(
        exercise_id,
        lambda r: builders.update_exercise(
            r, exercise_id, rest_seconds=rest_seconds, note=note, sets=sets
        ),
        dry_run=dry_run,
    )


@mcp.tool(annotations=_destructive("Apply routine"))
def smartgym_apply_routine(
    ctx: Context,
    routine: str,
    name: str | None = None,
    days: str | None = None,
    goal: str | None = None,
    note: str | None = None,
    warmup: list[DesiredExercise] | None = None,
    main: list[DesiredExercise] | None = None,
    cooldown: list[DesiredExercise] | None = None,
    dry_run: bool = True,
) -> EditResult:
    """Rewrite a routine in one go (e.g. "week 2 of FB-A").

    Each section you pass is the complete ordered list for that section: existing
    exercises by exercise_id (optionally with new rest/note/sets), new ones by catalog
    name. An existing exercise listed under another section moves there; one you leave
    out of a passed section is removed. Omitted sections stay as they are.
    dry_run=true (default) shows the full plan and sends NOTHING.
    """
    desired = DesiredRoutine(
        name=name, days=days, goal=goal, note=note, warmup=warmup, main=main, cooldown=cooldown
    )
    return _app(ctx).services().service.edit(routine, lambda _r: desired, dry_run=dry_run)


@mcp.tool(annotations=_destructive("Archive routines"))
def smartgym_archive_routines(
    ctx: Context, routines: list[str], dry_run: bool = True
) -> ArchiveResult:
    """Archive routines (names or ids) on every device. Reversible with
    smartgym_unarchive_routine. dry_run=true (default) sends NOTHING."""
    return _app(ctx).services().service.set_archived(routines, archived=True, dry_run=dry_run)


@mcp.tool(annotations=_destructive("Unarchive routine"))
def smartgym_unarchive_routine(ctx: Context, routine: str, dry_run: bool = True) -> ArchiveResult:
    """Bring an archived routine back to the active list. dry_run=true sends NOTHING."""
    return _app(ctx).services().service.set_archived([routine], archived=False, dry_run=dry_run)


@mcp.resource("smartgym://catalog/exercises", mime_type="application/json")
def catalog_exercises() -> str:
    """Read-only exercise catalog from the SmartGym app bundle (Exercises.json)."""
    return catalog.read_catalog(load_config(), "exercises")


@mcp.resource("smartgym://catalog/equipment", mime_type="application/json")
def catalog_equipment() -> str:
    """Read-only equipment catalog from the SmartGym app bundle (Equipments.json)."""
    return catalog.read_catalog(load_config(), "equipment")


@mcp.resource("smartgym://catalog/categories", mime_type="application/json")
def catalog_categories() -> str:
    """Read-only category catalog from the SmartGym app bundle (Categories.json)."""
    return catalog.read_catalog(load_config(), "categories")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,  # stdout is the stdio protocol channel — never log there
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    mcp.run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 7: Run tests and checks**

Run: `uv run pytest -q && uv run mypy src && uv run ruff check src tests scripts && uv run ruff format src tests scripts && grep -rn "z_pk\|lifecycle\|writes\.\|queries\.\|open_ro_connection" src tests`
Expected: all tests pass (`test_matching.py` 4, `test_server.py` 4 new); mypy/ruff clean; the grep prints nothing.

- [ ] **Step 8: Build spec — ship the CA bundle httpx uses**

In `smartgym-mcp.spec` add `from PyInstaller.utils.hooks import collect_data_files` to the imports and, after the pydantic_core block, `datas += collect_data_files('certifi')`.

- [ ] **Step 9: Docs**

`README.md`:
- "What it can do" → **Read**: health, list routines, routine detail split into warm-up / main / cool-down with recent sessions, workout history, equipment. **Write**: create programs with warm-up / main / cool-down, add / move / remove / reorder exercises, change sets / reps / weights / rest / notes, rewrite a whole routine in one call, archive / unarchive. **Safely**: every write is a dry run first; applying snapshots the routine to `~/.smartgym-mcp/backups/`, sends the change straight to SmartGym's server, re-reads it and verifies it; the app no longer needs to be quit or relaunched.
- Replace "Syncing edits (SmartGym 8+)" with **"Connecting to your account"**: the MCP talks to SmartGym's own server with your account's session. One-time setup: install mitmproxy (`brew install --cask mitmproxy`), trust its certificate, run `mitmdump --mode local:SmartGym -s scripts/capture_credentials.py --set confdir=<CA dir>`, open SmartGym, stop mitmdump when it prints "credentials saved", then remove the certificate trust. The file `~/.smartgym-mcp/credentials.json` (mode 600) holds the session; never share it. Re-run the capture if tools report an authentication error.
- Configuration table: `SMARTGYM_CREDENTIALS` (default `~/.smartgym-mcp/credentials.json`), `SMARTGYM_BACKUP_DIR` (default `~/.smartgym-mcp/backups`), `SMARTGYM_APP_BUNDLE` (default `/Applications/SmartGym.app`, used for the exercise catalog and version check). Remove `SMARTGYM_DB_PATH`.
- Disclaimer → "It uses SmartGym's private server API with your own account session; use at your own risk."

`DESIGN.md`:
- Intro line → "Python + FastMCP · stdio · single-user local server that edits SmartGym routines through SmartGym's own server API."
- Spec table: add a row `docs/superpowers/specs/2026-10-05-api-client-design.md | API client: reads + writes over SmartGym's server, sections | high — Phase 0 + S7 verified | ✅ implemented (Plan 2)`; mark specs 02/03 "superseded by the API client (DB write path removed)".
- Replace "One-paragraph summary" with: "The MCP is a client of SmartGym's backend, like any SmartGym device. Reads come from one `history/all` call (routines, workouts, equipment). Every write is a dry run first; applying fetches the routine fresh, snapshots it, sends the app's own edit requests (`routine/update/`, `routine/updateExercise/`, `routine/add/`, archive/unarchive), re-reads and verifies. Routines have warm-up / main / cool-down sections (`listGroup` 1/0/2). The app is never quit, relaunched, or written to; the local DB is no longer used."
- Replace "Architecture invariants" with: 1. Tools in `server.py` are thin — no HTTP or payload code in tool bodies. 2. Every write = fetch fresh → plan (`diff.py`) → dry run returns the plan → snapshot → send → re-fetch → verify (`service.py`). 3. Every edit of an existing routine goes through `diff_routine`; single-field tools are `builders.py` wrappers. 4. Only template sets are changed; logged history is never touched. 5. Credentials only via `api/auth.py`; never logged or returned. 6. Before trusting a new kind of server write, capture the app doing it and add a golden fixture (`tests/fixtures/api/`).
- Add "## Decision log (2026-10-06, API client)": DB write path + publish/tombstone retired; credentials = user-captured 0600 file (`phrase` unchecked by the server, app headers required); sections modelled as three lists (user choice A); routine delete stays out (archive only).

`FEATURES.md` F7: replace the body with "Decision 2026-10-05: archive only (`smartgym_archive_routines`). The server's `routine/delete/` endpoint is captured (2026-10-05) but deliberately not exposed."

`CLAUDE.md` working rules: replace the DB-specific rules with — follow DESIGN.md invariants (thin tools, every write through `service.RoutineService`, every edit through `diff_routine`); live checks only on `ZZ-` routines with the user confirming on the iPhone; snapshots land in `~/.smartgym-mcp/backups/<ts>/`; before trusting a new kind of server write, capture the app doing it first; tests never touch the network or the account (fixtures + `tests/fakes.FakeClient`). Commands: drop the DB-only notes.

- [ ] **Step 10: Commit**

```bash
git add -A src tests smartgym-mcp.spec README.md DESIGN.md FEATURES.md CLAUDE.md
git commit -m "Serve all tools from the SmartGym API and retire the DB write path"
```

---

### Task 11: Build, install, live end-to-end pass on `ZZ-` routines (user in the loop)

No new product code. Output: the installed MCP verified against the real server, results recorded in spec §8.

**Files:**
- Modify: `docs/superpowers/specs/2026-10-05-api-client-design.md` (§8: live pass result, date, app version)

**Interfaces:**
- Consumes: the installed binary from `scripts/build.sh`; the credentials file (the Phase 0 spike file already has the needed keys — `authorization`, `authID`, `app_headers`; otherwise the user runs `scripts/capture_credentials.py`).
- Produces: a verified build; the live-pass record that Task 12 depends on.

- [ ] **Step 1: Build and check the binary serves the 15 tools**

```bash
uv run pytest -q && scripts/build.sh
uv run python - <<'EOF'
import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main() -> None:
    params = StdioServerParameters(command=str(__import__("pathlib").Path.home() / ".local/share/smartgym-mcp/smartgym-mcp"))
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        print(sorted(t.name for t in (await s.list_tools()).tools))

asyncio.run(main())
EOF
```

Expected: tests pass; build prints `Installed to …`; the list shows exactly the 15 tool names from Task 10.

- [ ] **Step 2: Reconnect the MCP (user)**

User restarts Claude Desktop (⌘Q, reopen) and starts a fresh Claude Code session so both load the new binary. In that session call `smartgym_health`.
Expected: `ok: true`, `routines` = the active count, `warning: null` (installed 8.0.3). `ok: false` with a credentials problem → user runs the capture (README "Connecting to your account"), then retry.

- [ ] **Step 3: Scripted live pass (each write: dry run → read the plan → apply)**

Run through the MCP tools, in order, on throwaway routines only:

1. `smartgym_create_program` → `ZZ-E2E` with warmup [Shoulder Circling 1×10], exercises [Push Up 2×10, Plank 1×30, Squat 3×8], cooldown [Cross Arm Stretch 1×30]. **User: iPhone shows three sections with those exercises and sets.**
2. `smartgym_get_routine ZZ-E2E` → note the exercise_ids.
3. `smartgym_update_routine` days "2,4", note "E2E".
4. `smartgym_add_exercise` "Bridge" into warmup at position 1 (mid-routine add → order request).
5. `smartgym_move_exercise` Plank → cooldown.
6. `smartgym_reorder_routine` main = [Squat, Push Up].
7. `smartgym_update_exercise` Squat: rest 120, note "slow down", sets [8×20, 8×20, 6×22.5].
8. `smartgym_update_exercise` Squat: note "" (clear).
9. `smartgym_remove_exercise` Bridge.
**User: iPhone shows warm-up [Shoulder Circling], main [Squat 8×20, 8×20, 6×22.5 · 120 s · no note, Push Up], cool-down [Cross Arm Stretch, Plank]; days and note updated.**
10. `smartgym_apply_routine ZZ-E2E` with warmup [Shoulder Circling, Push Up (existing id)], main [Squat (existing id, sets [5×25]), Lunge (new)], cooldown [Cross Arm Stretch] — Plank omitted (removed). **User: iPhone matches.**
11. `smartgym_archive_routines ["ZZ-E2E"]` → **user: gone from the iPhone list**; `smartgym_unarchive_routine ZZ-E2E` → back; `smartgym_archive_routines ["ZZ-E2E"]` again (cleanup).

Expected: every apply returns `notice` "Applied … verified"; no `WriteVerifyError`. On a verify error: stop, read the reported differing fields, compare with the snapshot, and fix via a new golden fixture (spec invariant 6) before continuing.

- [ ] **Step 4: Compare server reads with the local DB (spec §9 step 2)**

Read-only, no MCP involved:

```bash
DB="$HOME/Library/Containers/com.smartgymapp.smartgym/Data/Documents/GymModel.sqlite"
sqlite3 -readonly "file:$DB?mode=ro" "SELECT ZNAME FROM ZROUTINE WHERE ZDATEREMOVED IS NULL AND ZHIDDEN = 0 ORDER BY ZNAME"
sqlite3 -readonly "file:$DB?mode=ro" "SELECT COUNT(*) FROM ZWORKOUT"
```

Expected: every local routine name appears in `smartgym_list_routines`; `smartgym_get_workout_history` (wide `date_from`) returns at least as many workouts as the local count (the server keeps more history than the Mac). Spot-check one routine's sections and sets in `smartgym_get_routine` against the Mac app. Any gap → record it in spec §4 S4 and stop.

- [ ] **Step 5: Record + commit**

Add to spec §8: "Live pass <date> on SmartGym 8.0.3: create / fields / add mid-routine / move / reorder / sets / note clear / remove / apply_routine / archive / unarchive — all verified on server and iPhone."

```bash
git add docs/superpowers/specs/2026-10-05-api-client-design.md
git commit -m "Record live end-to-end pass of the API-backed tools"
```

---

### Task 12: Fix the six "Return" routines' sections (user approves each)

The MCP-created routines FB-A/B/C — Return W1/W2 hold every exercise in main (spec §10.1). Each is rewritten in place with `smartgym_apply_routine` — no copies, no tombstones. Only after Task 11 passed.

**Files:** none (account data only; snapshots land in `~/.smartgym-mcp/backups/`).

**Interfaces:**
- Consumes: `smartgym_get_routine`, `smartgym_apply_routine` (Task 10), the Task 11 live-pass record.

- [ ] **Step 1: Build the reference split**

`smartgym_get_routine` on the hand-made `FB-A — Compound focus.`, `FB-B — Leg emphasis.`, `FB-C — Variation + arms.` → note which exercises each uses in warmup and cooldown. These are the reference: the Return routines use the same drills and stretches.

- [ ] **Step 2: One routine at a time — propose**

For `FB-A — Return W1` (then W2, then FB-B and FB-C): `smartgym_get_routine`, then propose three lists by exercise_id:
- warmup = the exercises that are warm-up drills in the matching hand-made routine (e.g. Shoulder Circling, Resistance Band Pull Apart, Bridge, Cable External Rotation, the light Cable Chest Press set) in their current order;
- cooldown = the stretches (the matching hand-made cool-down, plus any catalog exercise whose name ends in "Stretch");
- main = everything else, current order kept.

Show the user the three lists and the `smartgym_apply_routine … dry_run=true` plan (it must contain ONLY section moves — no added/removed exercises, no set or rest changes).

- [ ] **Step 3: Apply after explicit approval**

On the user's "yes" for that routine: `smartgym_apply_routine` with the same lists and `dry_run=false`. Expected: "Applied … verified". **User checks the routine on the iPhone** (three sections, nothing lost, sets unchanged). Repeat Steps 2–3 for the next routine.

- [ ] **Step 4: Close out**

Update `DESIGN.md` decision log: "<date>: Return W1/W2 routines restructured into warm-up / main / cool-down via apply_routine (snapshots in ~/.smartgym-mcp/backups/)."

```bash
git add DESIGN.md
git commit -m "Record the section fix of the Return routines"
```

---

## After this plan

- `smartgym_get_routine` per-exercise sessions follow history set ids; exercises re-added by the §10.4 fallback start a fresh per-routine history (only if S7 forced the fallback).
- Deferred from Plan 1's final review (still open): repeated read 5xx reported as "unreachable (network error)"; `ChangeSet.final_order` string keys (`"id:N"`/`"new:k"`) instead of a tagged model.
- FEATURES F2 (progression analytics) can now use the full server history (61+ workouts vs 30 in the local DB).
