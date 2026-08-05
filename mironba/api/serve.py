"""The single launcher: one command starts the API and serves the UI.

    python -m mironba.api.serve            # http://127.0.0.1:8300, Ctrl+C stops

Uvicorn hosts the FastAPI app in-process, so there is nothing else to
start and Ctrl+C tears everything down.

**It refuses to start behind a stale server.** A previous instance still
holding the port serves OLD code at the same URL - a route added since
then 404s, and the page you are reading is not the page you just wrote.
That is the wrong-surface failure this project keeps catching elsewhere,
so the launcher checks the port first and says exactly what to do.
"""

from __future__ import annotations

import socket
import sys

HOST = "127.0.0.1"
PORT = 8300


def port_is_held(host: str = HOST, port: int = PORT) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.4)
        return probe.connect_ex((host, port)) == 0


def main() -> int:
    import uvicorn

    from mironba.api.ui import app

    if port_is_held():
        print(f"REFUSING TO START: {HOST}:{PORT} is already served by another "
              "process.")
        print("  That process is running the code it started with, so any "
              "route added since")
        print("  then will 404 and you will be reading a stale page. Stop it "
              "first:")
        print(f'    powershell "Get-NetTCPConnection -LocalPort {PORT} '
              '-State Listen | ForEach-Object { Stop-Process -Id '
              '$_.OwningProcess -Force }"')
        return 1

    print("MiroNBA UI - presentation only; reads committed artifacts")
    print(f"  http://{HOST}:{PORT}   (Ctrl+C stops everything)")
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
