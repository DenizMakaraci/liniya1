"""Minimal MCP (Model Context Protocol) stdio server exposing CallPilot tools to Hermes/OpenClaw.

Implements just `initialize`, `tools/list` and `tools/call` over newline-delimited JSON-RPC 2.0,
which is all a NemoClaw-managed MCP integration needs. Run: python -m callpilot.tools.mcp_server
"""

import asyncio
import json
import sys

from .hermes_tools import TOOLS, dispatch


def _mcp_tools():
    return [{"name": t["function"]["name"], "description": t["function"]["description"],
             "inputSchema": t["function"]["parameters"]} for t in TOOLS]


async def handle(req: dict) -> dict | None:
    rid, method, params = req.get("id"), req.get("method"), req.get("params") or {}
    if method == "initialize":
        result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                  "serverInfo": {"name": "callpilot", "version": "0.1.0"}}
    elif method == "tools/list":
        result = {"tools": _mcp_tools()}
    elif method == "tools/call":
        try:
            out = await dispatch(params["name"], dict(params.get("arguments") or {}))
            result = {"content": [{"type": "text", "text": json.dumps(out, default=str)}]}
        except Exception as e:
            result = {"content": [{"type": "text", "text": str(e)}], "isError": True}
    elif method == "notifications/initialized" or rid is None:
        return None
    else:
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"unknown method {method}"}}
    return {"jsonrpc": "2.0", "id": rid, "result": result}


async def main():
    reader = asyncio.StreamReader()
    await asyncio.get_running_loop().connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)
    while line := await reader.readline():
        try:
            resp = await handle(json.loads(line))
        except json.JSONDecodeError:
            continue
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    asyncio.run(main())
