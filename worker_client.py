#!/usr/bin/env python3
import socket
import time
import json
import struct
import base64
import os
import platform
import subprocess
from pathlib import Path

HUB_HOST = os.getenv("HUB_HOST", "xxx.payit.gg")   # Đổi thành domain/IP payit.gg của Codespace
HUB_PORT = int(os.getenv("HUB_PORT", "4444"))      # Port public của payit.gg
AUTH_TOKEN = os.getenv("HUB_TOKEN", "CHANGE_ME")   # Phải khớp với hub

WORKER_NAME = os.getenv("WORKER_NAME", platform.node())
RECONNECT_DELAY = 5
HEARTBEAT_INTERVAL = 15
CHUNK_SIZE = 64 * 1024

def send_msg(sock, obj):
    data = json.dumps(obj).encode("utf-8")
    sock.sendall(struct.pack("!I", len(data)) + data)

def recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("disconnected")
        buf += chunk
    return buf

def recv_msg(sock):
    header = recv_exact(sock, 4)
    (length,) = struct.unpack("!I", header)
    payload = recv_exact(sock, length)
    return json.loads(payload.decode("utf-8"))

def run_cmd(cmd, cwd=None):
    try:
        completed = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=cwd)
        return True, completed.returncode, completed.stdout, completed.stderr
    except Exception as e:
        return False, -1, "", str(e)

def connect_and_run():
    cwd = str(Path.home())
    while True:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect((HUB_HOST, HUB_PORT))

            # hello
            send_msg(sock, {
                "type": "hello",
                "role": "worker",
                "token": AUTH_TOKEN,
                "meta": {
                    "name": WORKER_NAME,
                    "platform": f"{platform.system()} {platform.release()}",
                    "cwd": cwd
                }
            })
            ack = recv_msg(sock)
            if ack.get("type") != "hello_ack":
                sock.close(); time.sleep(RECONNECT_DELAY); continue

            last_hb = time.time()

            while True:
                # heartbeat
                if time.time() - last_hb > HEARTBEAT_INTERVAL:
                    send_msg(sock, {"type": "heartbeat"})
                    last_hb = time.time()

                sock.settimeout(1.0)
                try:
                    msg = recv_msg(sock)
                except socket.timeout:
                    continue

                mtype = msg.get("type")

                if mtype == "sysinfo":
                    data = {
                        "name": WORKER_NAME,
                        "platform": f"{platform.system()} {platform.release()}",
                        "python": platform.python_version(),
                        "cwd": cwd,
                        "env_count": len(os.environ),
                    }
                    send_msg(sock, {"type": "sysinfo", "data": data})

                elif mtype == "cd":
                    path = msg.get("path", "")
                    try:
                        os.chdir(path)
                        cwd = os.getcwd()
                        send_msg(sock, {"type": "exec_result", "ok": True, "rc": 0,
                                        "stdout": f"[cwd] {cwd}", "stderr": ""})
                    except Exception as e:
                        send_msg(sock, {"type": "exec_result", "ok": False, "rc": -1,
                                        "stdout": "", "stderr": str(e)})

                elif mtype == "exec":
                    ok, rc, out, err = run_cmd(msg.get("cmd", ""), cwd=cwd)
                    send_msg(sock, {
                        "type": "exec_result", "ok": ok, "rc": rc,
                        "stdout": out, "stderr": err
                    })

                elif mtype == "file_begin":
                    # chỉ thông báo, controller sẽ gửi 'file_chunk' tiếp
                    pass

                elif mtype == "file_chunk":
                    # nhận chunk để ghi file (upload: controller -> worker)
                    session_id = msg["session_id"]
                    remote_path = msg["remote_path"]
                    data_b64 = msg["data_b64"]
                    data = base64.b64decode(data_b64)
                    mode = msg.get("mode", "ab")
                    with open(remote_path, "ab" if mode == "ab" else "wb") as f:
                        f.write(data)

                elif mtype == "file_done":
                    # xác nhận xong 1 phiên upload
                    send_msg(sock, {"type": "file_done", "session_id": msg.get("session_id")})

                elif mtype == "download":
                    # gửi file từ worker -> controller qua hub bằng các chunk
                    session_id = msg["session_id"]
                    remote_path = msg["remote_path"]
                    with open(remote_path, "rb") as f:
                        while True:
                            chunk = f.read(CHUNK_SIZE)
                            if not chunk:
                                break
                            send_msg(sock, {
                                "type": "file_chunk",
                                "direction": "download",
                                "session_id": session_id,
                                "remote_path": remote_path,
                                "data_b64": base64.b64encode(chunk).decode("ascii")
                            })
                    send_msg(sock, {"type": "file_done", "session_id": session_id})

                elif mtype == "exit":
                    sock.close()
                    return

        except Exception:
            try:
                sock.close()
            except:
                pass
            time.sleep(RECONNECT_DELAY)

if __name__ == "__main__":
    connect_and_run()
