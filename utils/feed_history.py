from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
HISTORY_DIR = ROOT / "output" / "feed_history"
INDEX_PATH = HISTORY_DIR / "index.json"
MAX_ENTRIES = 40


def _ensure_dir() -> Path:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    return HISTORY_DIR


def _load_index() -> list[dict[str, Any]]:
    if not INDEX_PATH.is_file():
        return []
    try:
        payload = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return payload if isinstance(payload, list) else []


def _save_index(entries: list[dict[str, Any]]) -> None:
    _ensure_dir()
    INDEX_PATH.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def format_file_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"


def _prune_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    for entry in entries[:MAX_ENTRIES]:
        path = Path(str(entry.get("path") or ""))
        if path.is_file():
            kept.append(entry)
    for entry in entries[MAX_ENTRIES:]:
        path = Path(str(entry.get("path") or ""))
        if path.is_file():
            try:
                path.unlink()
            except OSError:
                pass
    return kept


def register_feed_export(
    exported_path: Path | str,
    *,
    export_format: str,
    shop_label: str = "",
    row_count: int | None = None,
) -> dict[str, Any]:
    """Ghi nhận file feed vừa xuất vào lịch sử."""
    path = Path(exported_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)

    now = datetime.now(timezone.utc)
    entry = {
        "id": now.strftime("%Y%m%d_%H%M%S_%f"),
        "path": str(path),
        "filename": path.name,
        "export_format": export_format,
        "shop_label": (shop_label or path.stem).strip(),
        "row_count": row_count,
        "created_at": now.isoformat(),
        "size_bytes": path.stat().st_size,
    }

    entries = _load_index()
    entries = [item for item in entries if item.get("path") != entry["path"]]
    entries.insert(0, entry)
    entries = _prune_entries(entries)
    _save_index(entries)
    return entry


def list_feed_history(*, sync_disk: bool = True) -> list[dict[str, Any]]:
    """Danh sách file feed đã lưu — mới nhất trước."""
    entries = _load_index()
    if not sync_disk:
        return entries

    synced: list[dict[str, Any]] = []
    for entry in entries:
        path = Path(str(entry.get("path") or ""))
        if not path.is_file():
            continue
        updated = dict(entry)
        updated["size_bytes"] = path.stat().st_size
        synced.append(updated)

    if len(synced) != len(entries):
        _save_index(synced)
    return synced


def read_feed_file(entry_id: str) -> tuple[bytes, str] | None:
    entry = _find_entry(entry_id)
    if not entry:
        return None
    path = Path(str(entry["path"]))
    if not path.is_file():
        return None
    return path.read_bytes(), str(entry.get("filename") or path.name)


def delete_feed_history_entry(entry_id: str) -> bool:
    entries = _load_index()
    target = None
    remaining: list[dict[str, Any]] = []
    for entry in entries:
        if entry.get("id") == entry_id:
            target = entry
        else:
            remaining.append(entry)

    if target is None:
        return False

    path = Path(str(target.get("path") or ""))
    if path.is_file():
        try:
            path.unlink()
        except OSError:
            pass

    _save_index(remaining)
    return True


def delete_all_feed_history() -> int:
    entries = _load_index()
    removed = 0
    for entry in entries:
        path = Path(str(entry.get("path") or ""))
        if path.is_file():
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
    _save_index([])
    return removed


def total_feed_history_bytes() -> int:
    return sum(int(item.get("size_bytes") or 0) for item in list_feed_history())


def _find_entry(entry_id: str) -> dict[str, Any] | None:
    for entry in _load_index():
        if entry.get("id") == entry_id:
            return entry
    return None


def format_created_at(iso_text: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_text)
        if dt.tzinfo is not None:
            dt = dt.astimezone()
        return dt.strftime("%d/%m/%Y %H:%M")
    except (TypeError, ValueError):
        return str(iso_text or "")
