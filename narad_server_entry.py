"""
narad-server console entry point.

Installed via pyproject [project.scripts]; also runnable directly:
    python3 narad_server_entry.py [--host 127.0.0.1] [--port 8000]
"""
from __future__ import annotations

import argparse

import narad_paths  # noqa: F401  — single-source sys.path bootstrap


def main() -> None:
    parser = argparse.ArgumentParser(prog="narad-server")
    parser.add_argument("--host", default="127.0.0.1")  # localhost by default — see C2
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    import uvicorn

    uvicorn.run("server:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
