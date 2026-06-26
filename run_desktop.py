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


def find_free_port(start=8010, end=8099):
    for port in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("0.0.0.0", port))
                return port
            except OSError:
                continue
    return 8010


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
    port = find_free_port()
    local_url = f"http://127.0.0.1:{port}/"
    lan_ip = get_local_ip()
    lan_url = f"http://{lan_ip}:{port}/"

    print("=" * 55)
    print("  AI Editor Review System")
    print("=" * 55)
    print(f"  Local:   {local_url}")
    print(f"  LAN/Hotspot: {lan_url}  <-- use on other devices")
    print("=" * 55)

    open_browser_later(local_url)
    uvicorn.run("app.api.main:app", host="0.0.0.0", port=port, reload=False)


if __name__ == "__main__":
    main()
