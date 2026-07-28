"""Lọc trùng / lấy proxy unique từ nhiều danh sách."""

from __future__ import annotations

import re
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
    keys: list[str] = field(default_factory=list)
    compare_keys: list[str] = field(default_factory=list)


@dataclass
class ProxyListExclusive:
    """Proxy chỉ có trong list này (không có ở list khác)."""

    label: str
    lines: list[str] = field(default_factory=list)


@dataclass
class ProxyDedupeResult:
    mode: str
    output_lines: list[str]
    total_unique_keys: int
    duplicate_keys: int
    unique_only_keys: int
    list_stats: list[ProxyListParse]
    # Mỗi list → proxy chỉ thuộc list đó
    exclusive_by_list: list[ProxyListExclusive] = field(default_factory=list)
    # Proxy trùng giữa ≥2 list (chung)
    shared_duplicates: list[str] = field(default_factory=list)
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
      - unique: kết quả chính = gộp mọi proxy chỉ có đúng 1 list
        + exclusive_by_list tách riêng từng list
      - duplicates: kết quả chính = proxy có ≥ 2 list
    """
    parsed: list[ProxyListParse] = []
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

    # compare_key chỉ thuộc đúng 1 label
    exclusive_cmp: dict[str, list[tuple[str, str]]] = {p.label: [] for p in parsed}
    shared: list[str] = []
    key_sources: dict[str, list[str]] = {}
    duplicate_keys = 0
    unique_only = 0

    for cmp, (display, labels) in sources.items():
        label_list = sorted(labels)
        key_sources[display] = label_list
        if len(labels) >= 2:
            duplicate_keys += 1
            shared.append(display)
        else:
            unique_only += 1
            only_label = next(iter(labels))
            if only_label in exclusive_cmp:
                exclusive_cmp[only_label].append((cmp, display))

    exclusive_by_list: list[ProxyListExclusive] = []
    for item in parsed:
        # Giữ thứ tự như trong list gốc
        lines: list[str] = []
        exclusive_set = {c for c, _ in exclusive_cmp.get(item.label, [])}
        for display, cmp in zip(item.keys, item.compare_keys):
            if cmp in exclusive_set:
                lines.append(display)
        exclusive_by_list.append(ProxyListExclusive(label=item.label, lines=lines))

    if mode == "duplicates":
        output = list(shared)
    else:
        # Gộp theo thứ tự list 1 → 2 → …
        output = [line for block in exclusive_by_list for line in block.lines]

    return ProxyDedupeResult(
        mode=mode,
        output_lines=output,
        total_unique_keys=len(sources),
        duplicate_keys=duplicate_keys,
        unique_only_keys=unique_only,
        list_stats=parsed,
        exclusive_by_list=exclusive_by_list,
        shared_duplicates=shared,
        key_sources=key_sources,
    )
