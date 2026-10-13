"""Match titles from output/geckocustom.txt against geckocustom_products.csv."""
from __future__ import annotations

import csv
import re
import unicodedata
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TITLE_FILE = ROOT / "output" / "geckocustom.txt"
CSV_FILE = ROOT / "output" / "geckocustom_products.csv"
OUT_MATCHED = ROOT / "output" / "geckocustom_from_titles.csv"
OUT_CLEAN = ROOT / "output" / "geckocustom_from_titles_out.csv"
OUT_REPORT = ROOT / "output" / "geckocustom_title_report.txt"

STOPWORDS = frozenset(
    {
        "for",
        "the",
        "a",
        "an",
        "and",
        "or",
        "with",
        "by",
        "geckocustom",
        "custom",
        "personalized",
        "gift",
        "gifts",
    }
)


def normalize_key(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("…", "...").replace("–", "-").replace("—", "-")
    text = re.sub(r"\.{2,}$", "", text.strip())
    text = re.sub(r"\s+", " ", text)
    return text.casefold()


def base_title(text: str) -> str:
    t = normalize_key(text)
    t = re.sub(r"^geckocustom,?\s*", "", t, flags=re.I)
    t = re.sub(r"\s+\d{5,8}\s*$", "", t)
    t = re.sub(r"\s+[a-z]\d{2,4}\s*$", "", t, flags=re.I)
    t = re.sub(r"\s+ta\d+\s*$", "", t, flags=re.I)
    t = re.sub(r"\s+h\d{3}\s*$", "", t, flags=re.I)
    t = re.sub(r"\s+n\d{3}\s*$", "", t, flags=re.I)
    t = re.sub(r"\s+k\d+\s*$", "", t, flags=re.I)
    t = re.sub(
        r",?\s*(?:xx-small|x-small|xs|small|medium|large|x-large|xx-large|xxx-large|"
        r"2x-large|3x-large|4x-large|5x-large|2xl|3xl|4xl|5xl|s|m|l|xl|xxl)\s*/\s*[^,]+$",
        "",
        t,
    )
    t = re.sub(
        r",?\s*\d+(?:\.\d+)?\s*[x×]\s*\d+(?:\.\d+)?(?:\s*(?:inch|in|cm|ft|feet))?\s*"
        r"(?:\s*/\s*[^,]+)?$",
        "",
        t,
    )
    t = re.sub(r",?\s*/\s*[^,]+$", "", t)
    return t.strip(" ,/-")


def load_input_titles(path: Path) -> list[str]:
    lines: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line:
            lines.append(line)
    return lines


def format_images(raw: str) -> str:
    parts = [p.strip() for p in re.split(r"\s*\|\s*", raw or "") if p.strip()]
    return ",".join(parts)


def token_prefix(text: str, n: int = 10) -> str:
    words = re.findall(r"[a-z0-9]+", normalize_key(text))
    return " ".join(words[:n])


def core_tokens(text: str, n: int = 12) -> str:
    words = [
        w
        for w in re.findall(r"[a-z0-9]+", normalize_key(text))
        if w not in STOPWORDS and not re.fullmatch(r"\d{5,8}", w)
    ]
    return " ".join(words[:n])


def extract_codes(text: str) -> list[str]:
    t = normalize_key(text)
    codes: list[str] = []
    for pat in (
        r"\b\d{6}\b",
        r"\bn\d{3}\b",
        r"\bh\d{3}\b",
        r"\bta\d+\b",
        r"\bho\d+\b",
        r"\bch\d+\b",
        r"\bk\d+\b",
    ):
        codes.extend(re.findall(pat, t))
    return list(dict.fromkeys(codes))


def build_csv_index(rows: list[dict[str, str]]) -> tuple[list[dict], dict[str, list[int]], dict[str, list[int]], dict[str, list[int]]]:
    indexed: list[dict] = []
    by_base: dict[str, list[int]] = {}
    by_token3: dict[str, list[int]] = {}
    by_code: dict[str, list[int]] = {}
    for i, row in enumerate(rows):
        title = row.get("title", "").strip()
        key = base_title(title)
        indexed.append(
            {
                "title": title,
                "base": key,
                "cate": (row.get("cate") or "").strip(),
                "link": (row.get("link") or "").strip(),
                "images": format_images(row.get("images") or ""),
            }
        )
        by_base.setdefault(key, []).append(i)
        by_token3.setdefault(token_prefix(title, 3), []).append(i)
        for code in extract_codes(title):
            by_code.setdefault(code, []).append(i)
    return indexed, by_base, by_token3, by_code


def candidate_indices(query: str, indexed: list[dict], by_token3: dict[str, list[int]]) -> list[int]:
    seen: set[int] = set()
    candidates: list[int] = []
    for n in (3, 2, 1):
        for idx in by_token3.get(token_prefix(query, n), []):
            if idx not in seen:
                seen.add(idx)
                candidates.append(idx)
    if candidates:
        return candidates
    q_words = re.findall(r"[a-z0-9]+", normalize_key(query))
    if not q_words:
        return list(range(len(indexed)))
    first = q_words[0]
    for i, item in enumerate(indexed):
        if first in item["base"]:
            candidates.append(i)
    return candidates or list(range(min(len(indexed), 500)))


def match_title(
    query: str,
    indexed: list[dict],
    by_base: dict[str, list[int]],
    by_token3: dict[str, list[int]],
    by_code: dict[str, list[int]],
) -> tuple[dict | None, float, str]:
    q_base = base_title(query)
    q_tokens = token_prefix(query, 12)
    q_core = core_tokens(query, 12)

    if q_base in by_base:
        idx = by_base[q_base][0]
        return indexed[idx], 1.0, "base_exact"

    q_codes = extract_codes(query)
    for code in q_codes:
        if len(code) >= 6 and code in by_code:
            idx = by_code[code][0]
            return indexed[idx], 0.98, "code_exact"

    for code in q_codes:
        if code in by_code and len(by_code[code]) == 1:
            idx = by_code[code][0]
            return indexed[idx], 0.9, "code_unique"

    code_hits: list[int] = []
    for code in q_codes:
        code_hits.extend(by_code.get(code, []))
    if code_hits:
        best_code: tuple[dict | None, float, str] = (None, 0.0, "code_fuzzy")
        for idx in dict.fromkeys(code_hits):
            item = indexed[idx]
            core_score = SequenceMatcher(None, q_core, core_tokens(item["title"], 12)).ratio()
            if core_score > best_code[1]:
                best_code = (item, core_score, "code_fuzzy")
        if best_code[0] is not None and best_code[1] >= 0.45:
            return best_code

    best: tuple[dict | None, float, str] = (None, 0.0, "none")
    for idx in candidate_indices(query, indexed, by_token3):
        item = indexed[idx]
        full = item["base"]
        full_tokens = token_prefix(item["title"], 12)
        full_core = core_tokens(item["title"], 12)

        if full.startswith(q_base) or q_base.startswith(full[: len(q_base)]):
            score = len(q_base) / max(len(full), 1)
            if score > best[1]:
                best = (item, max(score, 0.9), "prefix")

        token_score = SequenceMatcher(None, q_tokens, full_tokens).ratio()
        if token_score > best[1]:
            best = (item, token_score, "token")

        core_score = SequenceMatcher(None, q_core, full_core).ratio()
        if core_score > best[1]:
            best = (item, core_score, "core")

        fuzzy = SequenceMatcher(None, q_base[:100], full[:100]).ratio()
        if fuzzy > best[1]:
            best = (item, fuzzy, "fuzzy")

    if best[0] is not None and best[1] >= 0.52:
        return best
    return None, 0.0, "unmatched"


def main() -> None:
    input_titles = load_input_titles(TITLE_FILE)
    csv_rows = list(csv.DictReader(CSV_FILE.open(encoding="utf-8-sig")))
    indexed, by_base, by_token3, by_code = build_csv_index(csv_rows)

    total = len(input_titles)
    base_keys = [base_title(t) for t in input_titles]
    counter = Counter(base_keys)
    unique = len(counter)
    dup_rows = total - unique
    dup_pct = (dup_rows / total * 100) if total else 0.0

    matched_rows: list[dict[str, str]] = []
    unmatched: list[str] = []
    match_cache: dict[str, tuple[dict | None, float, str]] = {}

    for line_no, raw_title in enumerate(input_titles, start=1):
        cache_key = base_title(raw_title)
        if cache_key not in match_cache:
            match_cache[cache_key] = match_title(raw_title, indexed, by_base, by_token3, by_code)
        item, score, _method = match_cache[cache_key]

        if item is None:
            unmatched.append(raw_title)
            matched_rows.append(
                {
                    "title": raw_title,
                    "cate": "",
                    "link": "",
                    "images": "",
                    "match_score": "",
                    "source_title": raw_title,
                    "line_no": str(line_no),
                }
            )
            continue

        cate = item["cate"]
        if cate == item["title"]:
            cate = ""
        matched_rows.append(
            {
                "title": item["title"],
                "cate": cate,
                "link": item["link"],
                "images": item["images"],
                "match_score": f"{score:.0%}",
                "source_title": raw_title,
                "line_no": str(line_no),
            }
        )

    OUT_MATCHED.parent.mkdir(parents=True, exist_ok=True)
    with OUT_MATCHED.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "line_no",
                "title",
                "cate",
                "link",
                "images",
                "match_score",
                "source_title",
            ],
        )
        writer.writeheader()
        writer.writerows(matched_rows)

    with OUT_CLEAN.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["title", "cate", "link", "images"])
        writer.writeheader()
        for row in matched_rows:
            writer.writerow(
                {
                    "title": row["title"],
                    "cate": row["cate"],
                    "link": row["link"],
                    "images": row["images"],
                }
            )

    top_dups = counter.most_common(10)
    match_hits = sum(1 for r in matched_rows if r.get("link"))
    report_lines = [
        "=== GECKOCUSTOM TITLE REPORT ===",
        f"File title input : {TITLE_FILE.name} ({total} dong)",
        f"CSV crawl        : {CSV_FILE.name} ({len(csv_rows)} SP)",
        "",
        "--- TRUNG TITLE (trong file geckocustom) ---",
        f"Tong dong        : {total}",
        f"Title unique     : {unique}",
        f"Dong trung       : {dup_rows}",
        f"Ty le trung      : {dup_pct:.1f}%",
        "",
        "Top title trung nhieu nhat:",
    ]
    for title, count in top_dups:
        if count > 1:
            report_lines.append(f"  x{count} | {title[:90]}")

    report_lines.extend(
        [
            "",
            "--- MATCH VOI CSV CRAWL ---",
            f"Dong xuat ra       : {len(matched_rows)}/{total}",
            f"Match duoc         : {match_hits}/{total} ({match_hits/total*100:.1f}%)",
            f"Khong match        : {len(unmatched)} dong",
            f"Title gan trung    : {dup_pct:.1f}% ({dup_rows} dong / {unique} title unique)",
            f"Xuat file          : {OUT_CLEAN.name}",
        ]
    )
    if unmatched[:5]:
        report_lines.append("")
        report_lines.append("Mot so title khong match:")
        for line in unmatched[:8]:
            report_lines.append(f"  - {line[:100]}")

    OUT_REPORT.write_text("\n".join(report_lines), encoding="utf-8")
    for line in report_lines:
        try:
            print(line)
        except UnicodeEncodeError:
            print(line.encode("ascii", errors="replace").decode("ascii"))


if __name__ == "__main__":
    main()
