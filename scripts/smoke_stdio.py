"""Smoke-test an installed smartgym-mcp over stdio, the way an MCP client runs it.

  uv run --isolated --no-project --with dist/<wheel> python scripts/smoke_stdio.py <version>

Starts the `smartgym-mcp` entry point with a credentials path that does not exist (so
nothing reaches the network or the account), lists the tools and checks that
smartgym_health reports <version>.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def smoke(version: str) -> None:
    command = shutil.which("smartgym-mcp")
    if command is None:
        raise SystemExit("smartgym-mcp is not on PATH")
    with tempfile.TemporaryDirectory() as tmp:
        env = {
            **os.environ,
            "SMARTGYM_CREDENTIALS": str(Path(tmp) / "missing.json"),
            "SMARTGYM_BACKUP_DIR": str(Path(tmp) / "backups"),
        }
        params = StdioServerParameters(command=command, env=env)
        async with (
            stdio_client(params) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            tools = {tool.name for tool in (await session.list_tools()).tools}
            if "smartgym_health" not in tools:
                raise SystemExit(f"smartgym_health missing from {sorted(tools)}")
            result = await session.call_tool("smartgym_health", {})
            if result.is_error:
                raise SystemExit(f"smartgym_health failed: {result.content}")
            health = result.structured_content or json.loads(result.content[0].text)  # type: ignore[union-attr]
    if health.get("ok") is not False or health.get("version") != version:
        raise SystemExit(f"unexpected health {health}, expected version {version}")
    print(f"ok: {len(tools)} tools, version {version}")


if __name__ == "__main__":
    asyncio.run(smoke(sys.argv[1]))
