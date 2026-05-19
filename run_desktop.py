import os
import socket
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn


def find_free_port(start=8010, end=8099):
    for port in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return 8010


def open_browser_later(url: str, delay: float = 2.0):
    def _open():
        time.sleep(delay)
        webbrowser.open(url)
    threading.Thread(target=_open, daemon=True).start()


def main():
    os.chdir(Path(__file__).resolve().parent)
    port = find_free_port()
    url = f"http://127.0.0.1:{port}/"
    print(f"启动 AI 审稿系统: {url}")
    open_browser_later(url)
    uvicorn.run("app.api.main:app", host="127.0.0.1", port=port, reload=False)


if __name__ == "__main__":
    main()
