from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path


def temp_export_path(download_filename: str) -> Path:
    """File tạm trên hệ thống — không ghi vào thư mục output/ của app."""
    fd, raw = tempfile.mkstemp(prefix="crawl_export_", suffix=f"_{download_filename}")
    os.close(fd)
    return Path(raw)


def cleanup_export_artifacts(path: str | Path) -> None:
    """Xóa file CSV tạm và thư mục phụ WooCommerce (ảnh backup)."""
    file_path = Path(path)
    if file_path.is_file():
        file_path.unlink(missing_ok=True)
    extras = file_path.with_suffix("")
    if extras.is_dir():
        shutil.rmtree(extras, ignore_errors=True)
