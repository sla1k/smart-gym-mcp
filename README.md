# smart-gym-mcp

**Talk to your workouts.** An [MCP](https://modelcontextprotocol.io) server that connects AI
assistants like Claude to [SmartGym](https://smartgym.app) on your Mac — so you can review your
training, tweak routines, and build whole programs in plain English, and watch them sync to
your iPhone and Apple Watch.

## Why

SmartGym is a great workout tracker, but building and evolving a training program is still a
lot of tapping. Meanwhile, AI assistants are genuinely good at programming workouts — they just
had no way to reach your data.

This server bridges that gap. Your workout history, routines, and equipment become something
you can have a conversation with:

- *"How has my bench press progressed over the last 3 months?"*
- *"Add face pulls to my pull day, 3×12, after the rows."*
- *"Build me a 3-day full-body comeback program based on what I was lifting in May,
  using only equipment I own."*

Changes land in SmartGym itself — not a parallel app — so your data stays in one place and
syncs everywhere via SmartGym's own engine.

## What it can do

**Read** — health check, list routines, routine detail split into warm-up / main / cool-down
with recent sessions, workout history, your equipment.

**Write** — create programs with warm-up / main / cool-down, add / move / remove / reorder
exercises, change sets / reps / weights / rest / notes, rewrite a whole routine in one call,
archive / unarchive.

**Safely** — every write is a dry run first; applying snapshots the routine to
`~/.smartgym-mcp/backups/`, sends the change straight to SmartGym's server, re-reads it and
verifies it. The app no longer needs to be quit or relaunched.

## Connecting to your account

The MCP talks to SmartGym's own server with your account's session. One-time setup:

1. Install mitmproxy: `brew install --cask mitmproxy`, and trust its certificate.
2. Run `mitmdump --mode local:SmartGym -s scripts/capture_credentials.py --set confdir=<CA dir>`.
3. Open SmartGym; stop mitmdump when it prints "credentials saved".
4. Remove the mitmproxy certificate trust again.

The file `~/.smartgym-mcp/credentials.json` (mode 600) holds the session — never share it.
Re-run the capture if tools report an authentication error.

## Quick start

Requirements: macOS with SmartGym, Python ≥ 3.11, [`uv`](https://docs.astral.sh/uv/).

```sh
git clone https://github.com/sla1k/smart-gym-mcp
cd smart-gym-mcp
uv sync
```

Add it to your MCP client — e.g. Claude Code:

```sh
claude mcp add smartgym -- uv run --directory /path/to/smart-gym-mcp smartgym-mcp
```

or Claude Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "smartgym": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/smart-gym-mcp", "smartgym-mcp"]
    }
  }
}
```

Then ask your assistant to call `smartgym_health` — `{ ok: true, ... }` means you're connected.

## Configuration

Works out of the box with a standard SmartGym install. Env vars if you need them:

| Var | Default | Purpose |
|---|---|---|
| `SMARTGYM_CREDENTIALS` | `~/.smartgym-mcp/credentials.json` | account session (see above) |
| `SMARTGYM_BACKUP_DIR` | `~/.smartgym-mcp/backups` | routine snapshots taken before each write |
| `SMARTGYM_APP_BUNDLE` | `/Applications/SmartGym.app` | exercise catalog and version check |

## Contributing

Issues and PRs welcome. Design notes live in [`DESIGN.md`](DESIGN.md), ideas in
[`FEATURES.md`](FEATURES.md).

```sh
uv run pytest                                  # tests never touch your real data
uv run ruff check src tests && uv run mypy src
```

## Disclaimer

This is an unofficial, personal project — not affiliated with or endorsed by the makers of
SmartGym. It uses SmartGym's private server API with your own account session; use at your own
risk.

## License

[MIT](LICENSE)
