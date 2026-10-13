"""Match titles from output/petfiestas against petfiestas_products.csv."""
from __future__ import annotations

import csv
import re
import unicodedata
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TITLE_FILE = ROOT / "output" / "petfiestas"
CSV_FILE = ROOT / "output" / "petfiestas_products.csv"
OUT_MATCHED = ROOT / "output" / "petfiestas_from_titles.csv"
OUT_CLEAN = ROOT / "output" / "petfiestas_from_titles_out.csv"
OUT_REPORT = ROOT / "output" / "petfiestas_title_report.txt"


def normalize_key(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("…", "...").replace("–", "-").replace("—", "-")
    text = re.sub(r"\.{2,}$", "", text.strip())
    text = re.sub(r"\s+", " ", text)
    return text.casefold()


def base_title(text: str) -> str:
    t = normalize_key(text)
    t = re.sub(
        r",?\s*(xs|s|m|l|xl|xxl|2xl|3xl|4xl|5xl)\s*/\s*[^,]+$",
        "",
        t,
    )
    t = re.sub(
        r",?\s*\d+(?:\.\d+)?\s*[x×]\s*\d+(?:\.\d+)?(?:\s*(?:inch|in|cm))?\s*$",
        "",
        t,
    )
    t = re.sub(r",?\s*\d+(?:\.\d+)?(?:\"|in)?\s*/\s*[^,]+$", "", t)
    t = re.sub(r",?\s*/\s*[^,]+$", "", t)
    return t.strip(" ,/")


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


def build_csv_index(rows: list[dict[str, str]]) -> tuple[list[dict], dict[str, list[int]]]:
    indexed: list[dict] = []
    by_base: dict[str, list[int]] = {}
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
    return indexed, by_base


def token_prefix(text: str, n: int = 10) -> str:
    words = re.findall(r"[a-z0-9]+", normalize_key(text))
    return " ".join(words[:n])


def match_title(
    query: str,
    indexed: list[dict],
    by_base: dict[str, list[int]],
) -> tuple[dict | None, float, str]:
    q_base = base_title(query)
    q_tokens = token_prefix(query, 12)

    if q_base in by_base:
        idx = by_base[q_base][0]
        return indexed[idx], 1.0, "base_exact"

    best: tuple[dict | None, float, str] = (None, 0.0, "none")
    for item in indexed:
        full = item["base"]
        full_tokens = token_prefix(item["title"], 12)

        if full.startswith(q_base) or q_base.startswith(full[: len(q_base)]):
            score = len(q_base) / max(len(full), 1)
            if score > best[1]:
                best = (item, max(score, 0.9), "prefix")

        token_score = SequenceMatcher(None, q_tokens, full_tokens).ratio()
        if token_score > best[1]:
            best = (item, token_score, "token")

        fuzzy = SequenceMatcher(None, q_base[:80], full[:80]).ratio()
        if fuzzy > best[1]:
            best = (item, fuzzy, "fuzzy")

    if best[0] is not None and best[1] >= 0.68:
        return best
    return None, 0.0, "unmatched"


def main() -> None:
    input_titles = load_input_titles(TITLE_FILE)
    csv_rows = list(csv.DictReader(CSV_FILE.open(encoding="utf-8-sig")))
    indexed, by_base = build_csv_index(csv_rows)

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
            match_cache[cache_key] = match_title(raw_title, indexed, by_base)
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
        "=== PETFIESTAS TITLE REPORT ===",
        f"File title input : {TITLE_FILE.name} ({total} dong)",
        f"CSV crawl        : {CSV_FILE.name} ({len(csv_rows)} SP)",
        "",
        "--- TRUNG TITLE (trong file petfiestas) ---",
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
