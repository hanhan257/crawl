from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from utils.feed_history import format_file_size

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "output"
FEED_HISTORY_DIRNAME = "feed_history"


@dataclass(frozen=True)
class LegacyOutputScan:
    file_count: int = 0
    dir_count: int = 0
    total_bytes: int = 0

    @property
    def has_legacy(self) -> bool:
        return self.file_count > 0 or self.dir_count > 0


@dataclass(frozen=True)
class LegacyOutputCleanupResult:
    removed_files: int = 0
    removed_dirs: int = 0
    freed_bytes: int = 0
    errors: int = 0


def _dir_size(path: Path) -> int:
    total = 0
    for child in path.rglob("*"):
        if child.is_file():
            try:
                total += child.stat().st_size
            except OSError:
                pass
    return total


def scan_legacy_output() -> LegacyOutputScan:
    """Đếm file/thư mục cũ trong output/ (không tính feed_history)."""
    if not OUTPUT_DIR.is_dir():
        return LegacyOutputScan()

    file_count = 0
    dir_count = 0
    total_bytes = 0

    for item in OUTPUT_DIR.iterdir():
        if item.name == FEED_HISTORY_DIRNAME:
            continue
        if item.is_file():
            file_count += 1
            try:
                total_bytes += item.stat().st_size
            except OSError:
                pass
        elif item.is_dir():
            dir_count += 1
            total_bytes += _dir_size(item)

    return LegacyOutputScan(
        file_count=file_count,
        dir_count=dir_count,
        total_bytes=total_bytes,
    )


def cleanup_legacy_output() -> LegacyOutputCleanupResult:
    """Xóa mọi file/thư mục trong output/ trừ feed_history."""
    if not OUTPUT_DIR.is_dir():
        return LegacyOutputCleanupResult()

    removed_files = 0
    removed_dirs = 0
    freed_bytes = 0
    errors = 0

    for item in list(OUTPUT_DIR.iterdir()):
        if item.name == FEED_HISTORY_DIRNAME:
            continue
        try:
            if item.is_file():
                size = item.stat().st_size
                item.unlink()
                removed_files += 1
                freed_bytes += size
            elif item.is_dir():
                size = _dir_size(item)
                shutil.rmtree(item)
                removed_dirs += 1
                freed_bytes += size
        except OSError:
            errors += 1

    return LegacyOutputCleanupResult(
        removed_files=removed_files,
        removed_dirs=removed_dirs,
        freed_bytes=freed_bytes,
        errors=errors,
    )


def legacy_output_summary(scan: LegacyOutputScan) -> str:
    parts: list[str] = []
    if scan.file_count:
        parts.append(f"**{scan.file_count}** file")
    if scan.dir_count:
        parts.append(f"**{scan.dir_count}** thư mục")
    size = format_file_size(scan.total_bytes)
    if parts:
        return f"{' · '.join(parts)} (~{size})"
    return ""
