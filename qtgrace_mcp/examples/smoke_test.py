#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""冒烟测试: 按全局配置方式启动 qtgrace-mcp 插件, 走 MCP 握手 + tools/list。

适配 mcp >= 2.0 协议 (JSON-RPC over stdio)。
"""
import asyncio
import json
import os
import sys

COMMAND = sys.executable
ARGS = [os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server.py")]


async def main():
    proc = await asyncio.create_subprocess_exec(
        COMMAND, *ARGS,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=os.path.dirname(ARGS[0]),
    )

    def send(msg: dict) -> None:
        proc.stdin.write((json.dumps(msg) + "\n").encode())

    async def recv() -> dict:
        line = await asyncio.wait_for(proc.stdout.readline(), timeout=15)
        return json.loads(line.decode())

    # 1. initialize
    init = {
        "jsonrpc": "2.0", "id": 0, "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "smoke", "version": "1"},
        },
    }
    send(init)
    await proc.stdin.drain()
    resp = await recv()
    print("initialize:", "OK" if "result" in resp else resp)

    # 2. initialized notification
    send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    await proc.stdin.drain()

    # 3. tools/list
    send({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    await proc.stdin.drain()
    resp = await recv()
    tools = [t["name"] for t in resp["result"]["tools"]]
    print("tools:", tools)

    # 4. ping
    send({"jsonrpc": "2.0", "id": 2, "method": "ping", "params": {}})
    await proc.stdin.drain()
    resp = await recv()
    print("ping:", "OK" if "result" in resp else resp)

    proc.stdin.close()
    await proc.wait()
    print("\n[OK] 插件冒烟测试通过 (mcp 2.0 stdio)")


if __name__ == "__main__":
    asyncio.run(main())
