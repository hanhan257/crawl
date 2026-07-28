"""Lọc trùng / lấy proxy unique từ nhiều danh sách."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

# Bỏ ngày giờ phía sau: "host:port:user:pass 2026-05-08 02:01:35"
_DATETIME_SUFFIX = re.compile(
    r"\s+\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?\s*$"
)


def extract_proxy_key(line: str) -> str | None:
    """
    Lấy phần proxy để so trùng — bỏ timestamp / text thừa sau.
    VD:
      69.33.242.208:4444:b8c97e4cea:zBTeEa4M
      64.16.172.101:4444:b8c97e4cea:zBTeEa4M 2026-05-08 02:01:35
    → key = host:port:user:pass
    """
    raw = str(line or "").strip()
    if not raw or raw.startswith("#"):
        return None
    cleaned = _DATETIME_SUFFIX.sub("", raw).strip()
    if not cleaned:
        return None
    # Chỉ lấy token đầu (trước khoảng trắng còn lại)
    token = cleaned.split()[0].strip()
    if not token or ":" not in token:
        return None
    return token


def normalize_compare_key(proxy_key: str) -> str:
    """Khóa so sánh: host/port không phân biệt hoa thường; user/pass giữ nguyên."""
    parts = proxy_key.split(":")
    if len(parts) >= 2:
        host = parts[0].lower()
        port = parts[1]
        rest = ":".join(parts[2:])
        return f"{host}:{port}:{rest}" if rest else f"{host}:{port}"
    return proxy_key.lower()


@dataclass
class ProxyListParse:
    label: str
    raw_lines: int = 0
    valid: int = 0
    invalid: int = 0
    keys: list[str] = field(default_factory=list)  # display keys (first-seen form)
    compare_keys: list[str] = field(default_factory=list)


@dataclass
class ProxyDedupeResult:
    mode: str
    output_lines: list[str]
    total_unique_keys: int
    duplicate_keys: int
    unique_only_keys: int
    list_stats: list[ProxyListParse]
    # key → danh sách nhãn list chứa key đó
    key_sources: dict[str, list[str]] = field(default_factory=dict)


def parse_proxy_text(text: str, *, label: str) -> ProxyListParse:
    result = ProxyListParse(label=label)
    seen_in_list: set[str] = set()
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        result.raw_lines += 1
        key = extract_proxy_key(stripped)
        if not key:
            result.invalid += 1
            continue
        result.valid += 1
        cmp = normalize_compare_key(key)
        if cmp in seen_in_list:
            continue
        seen_in_list.add(cmp)
        result.keys.append(key)
        result.compare_keys.append(cmp)
    return result


def dedupe_proxy_lists(
    lists: list[tuple[str, str]],
    *,
    mode: str = "unique",
) -> ProxyDedupeResult:
    """
    lists: [(label, text), ...]
    mode:
      - unique: chỉ proxy xuất hiện đúng 1 list (không trùng giữa các list)
      - duplicates: proxy xuất hiện ≥ 2 list
    """
    parsed: list[ProxyListParse] = []
    # compare_key → (display_key, set of list labels)
    sources: dict[str, tuple[str, set[str]]] = {}

    for label, text in lists:
        item = parse_proxy_text(text, label=label)
        if item.raw_lines == 0 and not str(text or "").strip():
            continue
        parsed.append(item)
        for display, cmp in zip(item.keys, item.compare_keys):
            if cmp not in sources:
                sources[cmp] = (display, {label})
            else:
                sources[cmp][1].add(label)

    duplicate_keys = 0
    unique_only = 0
    output: list[str] = []
    key_sources: dict[str, list[str]] = {}

    for cmp, (display, labels) in sources.items():
        label_list = sorted(labels)
        key_sources[display] = label_list
        if len(labels) >= 2:
            duplicate_keys += 1
            if mode == "duplicates":
                output.append(display)
        else:
            unique_only += 1
            if mode == "unique":
                output.append(display)

    return ProxyDedupeResult(
        mode=mode,
        output_lines=output,
        total_unique_keys=len(sources),
        duplicate_keys=duplicate_keys,
        unique_only_keys=unique_only,
        list_stats=parsed,
        key_sources=key_sources,
    )
