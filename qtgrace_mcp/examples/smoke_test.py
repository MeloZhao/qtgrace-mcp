#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""冒烟测试: 按全局配置方式启动 qtgrace-mcp 插件, 走 MCP 握手 + tools/list。"""
import asyncio
import json
import os
import sys

COMMAND = sys.executable
ARGS = [os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'server.py')]


async def main():
    proc = await asyncio.create_subprocess_exec(
        COMMAND, *ARGS,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=os.path.dirname(ARGS[0]),
    )
    init = {'jsonrpc': '2.0', 'id': 0, 'method': 'initialize',
            'params': {'protocolVersion': '2024-11-05', 'capabilities': {},
                       'clientInfo': {'name': 'smoke', 'version': '1'}}}
    proc.stdin.write((json.dumps(init) + '\n').encode())
    await proc.stdin.drain()
    resp = json.loads((await asyncio.wait_for(proc.stdout.readline(), timeout=15)).decode())
    print('initialize:', 'OK' if 'result' in resp else resp)

    proc.stdin.write(b'{"jsonrpc": "2.0", "method": "notifications/initialized"}\n')
    await proc.stdin.drain()

    proc.stdin.write((json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list', 'params': {}}) + '\n').encode())
    await proc.stdin.drain()
    resp = json.loads((await asyncio.wait_for(proc.stdout.readline(), timeout=15)).decode())
    tools = [t['name'] for t in resp['result']['tools']]
    print('tools:', tools)

    # ping
    proc.stdin.write((json.dumps({'jsonrpc': '2.0', 'id': 2, 'method': 'ping', 'params': {}}) + '\n').encode())
    await proc.stdin.drain()
    resp = json.loads((await asyncio.wait_for(proc.stdout.readline(), timeout=15)).decode())
    print('ping:', 'OK' if 'result' in resp else resp)

    proc.stdin.close()
    await proc.wait()
    print('\n✅ 插件冒烟测试通过 (按全局配置启动)')


if __name__ == '__main__':
    asyncio.run(main())
