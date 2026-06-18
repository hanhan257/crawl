#!/usr/bin/env python3
"""Entry point khi chạy bản build (.exe) hoặc dev."""
from __future__ import annotations

import os
import sys
import webbrowser
from pathlib import Path
from threading import Timer

from app_brand import APP_NAME, APP_VERSION

PORT = 8501


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def resource_dir() -> Path:
    if _is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent


def work_dir() -> Path:
    if _is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def open_browser() -> None:
    webbrowser.open(f"http://localhost:{PORT}")


def main() -> int:
    root = resource_dir()
    cwd = work_dir()
    os.chdir(cwd)

    app_path = root / "app.py"
    if not app_path.is_file():
        print(f"[LOI] Khong tim thay app.py tai: {app_path}")
        input("Nhan Enter de thoat...")
        return 1

    os.environ.setdefault("BROWSER", "default")
    os.environ["STREAMLIT_GLOBAL_DEVELOPMENT_MODE"] = "false"
    os.environ["STREAMLIT_SERVER_HEADLESS"] = "true"

    print("=" * 48)
    print(f"  {APP_NAME} v{APP_VERSION}")
    print("=" * 48)
    print(f"Thu muc lam viec: {cwd}")
    print(f"Mo trinh duyet: http://localhost:{PORT}")
    print("Dong cua so nay = tat app.")
    print()

    Timer(2.0, open_browser).start()

    sys.argv = [
        "streamlit",
        "run",
        str(app_path),
        "--server.headless",
        "true",
        "--browser.gatherUsageStats",
        "false",
        "--server.port",
        str(PORT),
        "--server.fileWatcherType",
        "none",
    ]

    from streamlit.web import cli as stcli

    return stcli.main() or 0


if __name__ == "__main__":
    raise SystemExit(main())
