"""The single launcher: one command starts the API and serves the UI.

    python -m mironba.api.serve            # http://127.0.0.1:8300, Ctrl+C stops

Uvicorn hosts the FastAPI app in-process, so there is nothing else to
start and Ctrl+C tears everything down.
"""

from __future__ import annotations


def main() -> int:
    import uvicorn

    from mironba.api.ui import app

    print("MiroNBA UI - presentation only; reads committed artifacts")
    print("  http://127.0.0.1:8300   (Ctrl+C stops everything)")
    uvicorn.run(app, host="127.0.0.1", port=8300, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
