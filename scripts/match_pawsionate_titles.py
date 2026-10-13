"""Match titles from output/pawsionate against pawsionate_products.csv."""
from __future__ import annotations

import csv
import re
import unicodedata
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TITLE_FILE = ROOT / "output" / "pawsionate"
CSV_FILE = ROOT / "output" / "pawsionate_products.csv"
OUT_MATCHED = ROOT / "output" / "pawsionate_from_titles.csv"
OUT_CLEAN = ROOT / "output" / "pawsionate_from_titles_out.csv"
OUT_CLEAN_ALT = ROOT / "output" / "pawsionate_from_titles_fixed.csv"
OUT_REPORT = ROOT / "output" / "pawsionate_title_report.txt"

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
        "pawsionate",
        "custom",
        "personalized",
        "ver",
        "photo",
        "gift",
        "gifts",
        "lovers",
        "birthday",
        "christmas",
        "mother",
        "mothers",
        "father",
        "fathers",
        "day",
    }
)

FOREIGN_MARKERS = (
    "cremation urn",
    "urns for ashes",
    "mainely urns",
    "for an adult urn",
    "grave marker",
    "gravestone",
    "nameplate",
    "safeguard cremation",
    "stonewood cremation",
    "sheesham cremation",
    "military sheesham",
    "river rock",
    "snap top cremation",
    "diplomat wood cremation",
)

PRODUCT_TYPES: dict[str, tuple[str, ...]] = {
    "beanie": ("beanie", "winter hat", "baby beanie", "bobble"),
    "leather bag": ("leather bag",),
    "beach bag": ("beach bag",),
    "doormat": ("doormat", "door mat"),
    "mug": ("mug", "color changing mug"),
    "poster": ("poster", "print"),
    "suncatcher": ("suncatcher", "window hanging"),
    "wind chime": ("wind chime",),
    "t-shirt": ("t-shirt", "tshirt", "shirt", "long sleeve", "sweatshirt", "hoodie"),
    "cap": ("cap", "baseball cap", "denim cap"),
    "onesie": ("onesie",),
    "doormat": ("doormat",),
    "ornament": ("ornament",),
    "sunshade": ("sunshade",),
    "seat cover": ("seat cover",),
    "wood sign": ("wood sign", "sign"),
    "memorial stone": ("memorial stone", "pet stone"),
    "wallet": ("wallet",),
    "tumbler": ("tumbler",),
    "watch": ("watch",),
}


def normalize_key(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("…", "...").replace("–", "-").replace("—", "-")
    text = re.sub(r"\.{2,}$", "", text.strip())
    text = re.sub(r"\s+", " ", text)
    return text.casefold()


def is_foreign_feed(text: str) -> bool:
    t = normalize_key(text)
    return any(marker in t for marker in FOREIGN_MARKERS)


def product_types(text: str) -> list[str]:
    t = normalize_key(text)
    found: list[str] = []
    for name, keywords in PRODUCT_TYPES.items():
        if any(kw in t for kw in keywords):
            found.append(name)
    return found


def type_compatible(query: str, candidate: str) -> bool:
    q_types = product_types(query)
    if not q_types:
        return True
    c = normalize_key(candidate)
    for name in q_types:
        if any(kw in c for kw in PRODUCT_TYPES[name]):
            return True
    return False


def base_title(text: str) -> str:
    t = normalize_key(text)
    t = re.sub(r"^pawsionate:\s*", "", t, flags=re.I)
    t = re.sub(r"\s+by mainely urns\s*$", "", t, flags=re.I)
    t = re.sub(r"\s*-\s*urns for ashes adults\s*$", "", t, flags=re.I)
    t = re.sub(r"\s*-\s*for an adult urn\s*$", "", t, flags=re.I)
    t = re.sub(
        r",?\s*(?:xx-small|x-small|xs|small|medium|large|x-large|xx-large|xxx-large|"
        r"2x-large|3x-large|4x-large|5x-large|2xl|3xl|4xl|5xl|s|m|l|xl|xxl)\s*/\s*[^,]+$",
        "",
        t,
    )
    t = re.sub(r",?\s*[a-z0-9]+(?:/[a-z0-9]+)+\s*$", "", t, flags=re.I)
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
    main = base_title(text).split(" - ")[0]
    words = [
        w
        for w in re.findall(r"[a-z0-9]+", main)
        if w not in STOPWORDS and len(w) > 1
    ]
    return " ".join(words[:n])


def slug_tokens(text: str) -> frozenset[str]:
    words = re.findall(r"[a-z0-9]+", normalize_key(text))
    return frozenset(w for w in words if w not in STOPWORDS and len(w) > 1)


def link_slug(link: str) -> str:
    m = re.search(r"/products/([^/?#]+)", link or "", re.I)
    return m.group(1) if m else ""


def slug_score(query: str, link: str) -> float:
    slug = link_slug(link)
    if not slug:
        return 0.0
    q = slug_tokens(query)
    s = slug_tokens(slug.replace("-", " "))
    if not q:
        return 0.0
    overlap = len(q & s)
    if overlap < 2:
        return 0.0
    return overlap / len(q)


def catalog_main_phrase(title: str) -> str:
    """Use segment after bullet when present (pawsionate catalog format)."""
    t = normalize_key(title)
    if "•" in t:
        t = t.split("•", 1)[1]
    return base_title(t)


def build_csv_index(rows: list[dict[str, str]]) -> tuple[list[dict], dict[str, list[int]], dict[str, list[int]], dict[str, list[int]]]:
    indexed: list[dict] = []
    by_base: dict[str, list[int]] = {}
    by_token3: dict[str, list[int]] = {}
    by_slug_token: dict[str, list[int]] = {}
    for i, row in enumerate(rows):
        title = row.get("title", "").strip()
        link = (row.get("link") or "").strip()
        key = base_title(title)
        main_phrase = catalog_main_phrase(title)
        indexed.append(
            {
                "title": title,
                "base": key,
                "main_phrase": main_phrase,
                "cate": (row.get("cate") or "").strip(),
                "link": link,
                "images": format_images(row.get("images") or ""),
            }
        )
        by_base.setdefault(key, []).append(i)
        by_token3.setdefault(token_prefix(title, 3), []).append(i)
        for tok in slug_tokens(link_slug(link)):
            by_slug_token.setdefault(tok, []).append(i)
    return indexed, by_base, by_token3, by_slug_token


def candidate_indices(query: str, indexed: list[dict], by_token3: dict[str, list[int]]) -> list[int]:
    seen: set[int] = set()
    candidates: list[int] = []
    for n in (4, 3, 2, 1):
        for idx in by_token3.get(token_prefix(query, n), []):
            if idx not in seen:
                seen.add(idx)
                candidates.append(idx)
    if candidates:
        return candidates
    q_words = re.findall(r"[a-z0-9]+", normalize_key(query))
    if not q_words:
        return []
    first = q_words[0]
    for i, item in enumerate(indexed):
        if first in item["base"]:
            candidates.append(i)
    return candidates[:800]


def match_title(
    query: str,
    indexed: list[dict],
    by_base: dict[str, list[int]],
    by_token3: dict[str, list[int]],
    by_slug_token: dict[str, list[int]],
) -> tuple[dict | None, float, str]:
    if is_foreign_feed(query):
        return None, 0.0, "foreign_feed"

    q_base = base_title(query)
    q_main = q_base.split(" - ")[0].strip()
    q_tokens = token_prefix(query, 12)
    q_core = core_tokens(query, 12)

    if q_base in by_base:
        idx = by_base[q_base][0]
        return indexed[idx], 1.0, "base_exact"

    for key, idxs in by_base.items():
        key_main = key.split(" - ")[0].strip()
        if len(q_main) >= 12 and (key_main == q_main or key.startswith(q_main) or q_main.startswith(key_main)):
            item = indexed[idxs[0]]
            if type_compatible(query, item["title"]):
                return item, 0.96, "main_prefix"

    q_slug_words = slug_tokens(query)
    if len(q_slug_words) >= 2:
        slug_hits: Counter[int] = Counter()
        for tok in q_slug_words:
            for idx in by_slug_token.get(tok, []):
                slug_hits[idx] += 1
        slug_best: tuple[dict | None, float, str] = (None, 0.0, "none")
        for idx, hits in slug_hits.most_common(40):
            item = indexed[idx]
            if not type_compatible(query, item["title"]):
                continue
            score = slug_score(query, item["link"])
            if hits >= 3 and score >= 0.55 and score > slug_best[1]:
                slug_best = (item, score, "slug")
            elif score > slug_best[1]:
                slug_best = (item, score, "slug")
        if slug_best[0] is not None and slug_best[1] >= 0.55:
            return slug_best

    best: tuple[dict | None, float, str] = (None, 0.0, "none")
    for idx in candidate_indices(query, indexed, by_token3):
        item = indexed[idx]
        if not type_compatible(query, item["title"]):
            continue
        full = item["base"]
        full_main = full.split(" - ")[0].strip()
        phrase = item.get("main_phrase") or full
        full_tokens = token_prefix(item["title"], 12)
        full_core = core_tokens(item["title"], 12)

        if phrase.startswith(q_base) or q_base.startswith(phrase[: len(q_base)]):
            score = len(q_base) / max(len(phrase), 1)
            if score > best[1]:
                best = (item, max(score, 0.9), "phrase")

        if full.startswith(q_base) or q_base.startswith(full[: len(q_base)]):
            score = len(q_base) / max(len(full), 1)
            if score > best[1]:
                best = (item, max(score, 0.9), "prefix")

        if len(q_main) >= 10 and (full_main.startswith(q_main) or q_main.startswith(full_main)):
            score = len(q_main) / max(len(full_main), 1)
            if score > best[1]:
                best = (item, max(score, 0.88), "main")

        if len(q_main) >= 8 and (phrase.startswith(q_main) or q_main.startswith(phrase)):
            score = len(q_main) / max(len(phrase), 1)
            if score > best[1]:
                best = (item, max(score, 0.87), "phrase_main")

        core_score = SequenceMatcher(None, q_core, full_core).ratio()
        if core_score > best[1]:
            best = (item, core_score, "core")

        token_score = SequenceMatcher(None, q_tokens, full_tokens).ratio()
        if token_score > best[1]:
            best = (item, token_score, "token")

    method = best[2]
    score = best[1]
    if best[0] is None:
        return None, 0.0, "unmatched"
    if method in {"prefix", "main", "main_prefix", "base_exact", "phrase", "phrase_main", "slug"}:
        return best
    if method == "core" and score >= 0.72:
        return best
    if method == "token" and score >= 0.74:
        return best
    return None, 0.0, "unmatched"


def main() -> None:
    input_titles = load_input_titles(TITLE_FILE)
    csv_rows = list(csv.DictReader(CSV_FILE.open(encoding="utf-8-sig")))
    indexed, by_base, by_token3, by_slug_token = build_csv_index(csv_rows)

    total = len(input_titles)
    base_keys = [base_title(t) for t in input_titles]
    counter = Counter(base_keys)
    unique = len(counter)
    dup_rows = total - unique
    dup_pct = (dup_rows / total * 100) if total else 0.0
    foreign_count = sum(1 for t in input_titles if is_foreign_feed(t))

    matched_rows: list[dict[str, str]] = []
    unmatched: list[str] = []
    match_cache: dict[str, tuple[dict | None, float, str]] = {}

    for line_no, raw_title in enumerate(input_titles, start=1):
        cache_key = base_title(raw_title)
        if cache_key not in match_cache:
            match_cache[cache_key] = match_title(raw_title, indexed, by_base, by_token3, by_slug_token)
        item, score, method = match_cache[cache_key]

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
                    "match_method": method,
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
                "match_method": method,
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
                "match_method",
                "source_title",
            ],
        )
        writer.writeheader()
        writer.writerows(matched_rows)

    for out_path in (OUT_CLEAN, OUT_CLEAN_ALT):
        try:
            with out_path.open("w", encoding="utf-8-sig", newline="") as fh:
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
            clean_out = out_path.name
            break
        except PermissionError:
            continue
    else:
        clean_out = OUT_CLEAN.name

    pawsionate_lines = [t for t in input_titles if not is_foreign_feed(t)]
    match_hits = sum(1 for r in matched_rows if r.get("link"))
    paws_hits = sum(
        1
        for r, t in zip(matched_rows, input_titles)
        if r.get("link") and not is_foreign_feed(t)
    )

    top_dups = counter.most_common(10)
    report_lines = [
        "=== PAWSIONATE TITLE REPORT ===",
        f"File title input : {TITLE_FILE.name} ({total} dong)",
        f"CSV crawl        : {CSV_FILE.name} ({len(csv_rows)} SP)",
        "",
        "--- TRUNG TITLE (trong file pawsionate) ---",
        f"Tong dong        : {total}",
        f"Title unique     : {unique}",
        f"Dong trung       : {dup_rows}",
        f"Ty le trung      : {dup_pct:.1f}%",
        f"Dong mainelyurns  : {foreign_count} (khong co tren pawsionate.com)",
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
            f"Match duoc (all)   : {match_hits}/{total} ({match_hits/total*100:.1f}%)",
            f"Match pawsionate   : {paws_hits}/{len(pawsionate_lines)} ({paws_hits/len(pawsionate_lines)*100:.1f}%)",
            f"Khong match        : {len(unmatched)} dong",
            f"Xuat file          : {clean_out}",
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
