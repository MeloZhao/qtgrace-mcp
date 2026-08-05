#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""qtgrace MCP 可行性验证 v4 — 严格按官方 qtclient 协议。

每个批次: 新建连接 -> initComm 握手 -> 一次性发送完整数据流
数据流: [PLOT_INFO '@FLUSH\\n'][PLOT_INFO '@ g0 on\\n']
        [WRITE_DATAVEC len x[] y[]][WRITE_DATAVEC_FINISHED][REDRAW][END_COMM]
"""
import socket
import struct
import subprocess
import time
import os

QTGRACE = r'D:\qtgrace_v026_Win7\bin\qtgrace.exe'
PORT_WRITE = 25031  # Server1 -> qtgrace writeServer (client 收响应)
PORT_READ = 25032   # Server2 -> qtgrace readServer (client 发命令)
WORKDIR = os.path.join(os.environ['TEMP'], 'qtgrace_mcp_probe4')
os.makedirs(WORKDIR, exist_ok=True)

PLOT_INFO = 1
WRITE_DATAVEC = 2
REDRAW = 4
PS_FILENAME = 5
REDRAW_AND_WRITEPS = 7
WRITE_DATAVEC_FINISHED = 12
END_COMM = 99
KILL_CHILD = 42

initComm, endComm, readDataComm = 0, 1, 2

env = dict(os.environ)
env['QTGRACEDEBUG'] = '1'

proc = subprocess.Popen(
    [QTGRACE, '-ServerMode', str(PORT_WRITE), str(PORT_READ)],
    cwd=WORKDIR, env=env,
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)
time.sleep(3.0)


def new_session():
    """建新连接并握手，返回 (sock_read_cmd, sock_write_resp)。"""
    sr = socket.create_connection(('127.0.0.1', PORT_READ), timeout=5)
    sw = socket.create_connection(('127.0.0.1', PORT_WRITE), timeout=5)
    sr.settimeout(8); sw.settimeout(8)
    sr.sendall(struct.pack('i', initComm))
    resp = struct.unpack('i', sw.recv(4))[0]
    assert resp == initComm, f'握手失败: {resp}'
    return sr, sw


def send_stream(sr, sw, payload: bytes):
    """发送 readDataComm + 数据流，收 endComm，关闭连接。"""
    sr.sendall(struct.pack('i', readDataComm))
    sr.sendall(struct.pack('i', len(payload)))
    sr.sendall(payload)
    resp = struct.unpack('i', sw.recv(4))[0]
    sr.close(); sw.close()
    return resp


def plot_info(s):
    b = s.encode('latin-1')
    return struct.pack('i', PLOT_INFO) + struct.pack('i', len(b)) + b


def datavec(xs, ys):
    n = len(xs)
    return (struct.pack('i', WRITE_DATAVEC) + struct.pack('i', n)
            + struct.pack('d' * n, *xs) + struct.pack('d' * n, *ys))


def cmd(c):
    return struct.pack('i', c)


print('[1] 批次1: 画图数据 (cleanPlot + datavec + finish + redraw + end)')
xs = [i * 0.5 for i in range(10)]
ys = [i * i for i in range(10)]
stream = b''.join([
    plot_info('@FLUSH\n'),
    plot_info('@ g0 on\n'),
    datavec(xs, ys),
    cmd(WRITE_DATAVEC_FINISHED),
    cmd(REDRAW),
    cmd(END_COMM),
])
sr, sw = new_session()
r = send_stream(sr, sw, stream)
print(f'    ✅ 响应 {r} (期望 endComm=1)')

print('[2] 批次2: 设置导出文件名 (相对名, cwd 已切到 WORKDIR)')
outbase = 'plot_out'  # 相对名 -> 写到 WORKDIR
fb = outbase.encode('latin-1')
stream = struct.pack('i', PS_FILENAME) + struct.pack('i', len(fb)) + fb
sr, sw = new_session()
r = send_stream(sr, sw, stream)
print(f'    ✅ 响应 {r}')

print('[3] 批次3: REDRAW_AND_WRITEPS (导出)')
sr, sw = new_session()
r = send_stream(sr, sw, cmd(REDRAW_AND_WRITEPS))
print(f'    ✅ 响应 {r}')

time.sleep(4)
print('\n[4] 检查输出文件:')
found = False
for f in sorted(os.listdir(WORKDIR)):
    p = os.path.join(WORKDIR, f)
    if os.path.isfile(p) and f != 'QTGRACEDEBUG.txt':
        found = True
        print(f'    📄 {f}  ({os.path.getsize(p)} bytes)')
if not found:
    print('    ❌ 无输出文件')
    import glob
    for g in glob.glob(os.path.join(WORKDIR, 'plot_out*')) + glob.glob(os.path.join(WORKDIR, '*.png')) + glob.glob(os.path.join(WORKDIR, '*.ps')):
        print(f'    glob: {g} ({os.path.getsize(g)} bytes)')

print('\n[5] KILL_CHILD')
try:
    sr, sw = new_session()
    send_stream(sr, sw, cmd(KILL_CHILD))
except Exception as e:
    print(f'    ({e})')
try:
    proc.wait(timeout=5)
except Exception:
    proc.kill()
print('\ndone')
