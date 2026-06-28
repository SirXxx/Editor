import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn

# Fix Windows console encoding
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def find_free_port(start=8010, end=8099, bind_host="0.0.0.0"):
    for port in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((bind_host, port))
                return port
            except OSError:
                continue
    return start


def get_local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


def open_browser_later(url: str, delay: float = 2.0):
    def _open():
        time.sleep(delay)
        webbrowser.open(url)
    threading.Thread(target=_open, daemon=True).start()


def main():
    os.chdir(Path(__file__).resolve().parent)
    bind_host = os.getenv("APP_BIND_HOST", "0.0.0.0")
    open_host = os.getenv("APP_OPEN_HOST", "localhost")
    port_start = env_int("APP_PORT_START", 8010)
    port_end = env_int("APP_PORT_END", 8099)
    if port_end < port_start:
        port_end = port_start

    port = find_free_port(start=port_start, end=port_end, bind_host=bind_host)
    local_url = f"http://{open_host}:{port}/"
    lan_ip = get_local_ip()
    lan_url = f"http://{lan_ip}:{port}/"

    print("=" * 55)
    print("  AI Editor Review System")
    print("=" * 55)
    print(f"  Local:   {local_url}")
    print(f"  LAN/Hotspot: {lan_url}  <-- use on other devices")
    print(f"  Bind Host: {bind_host}")
    print("=" * 55)

    open_browser_later(local_url)
    uvicorn.run("app.api.main:app", host=bind_host, port=port, reload=False)


if __name__ == "__main__":
    main()
