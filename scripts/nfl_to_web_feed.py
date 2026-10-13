"""Convert output/nfl.txt -> CSV feed web thuần (Trendsi-style, màu tách cột riêng)."""
from __future__ import annotations

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.nfl_title_color import split_nfl_title_color  # noqa: E402

IN_FILE = ROOT / "output" / "nfl.txt"
OUT_FILE = ROOT / "output" / "nfl_web_feed.csv"
BRAND = "NFL"


def main() -> None:
    if not IN_FILE.is_file() or IN_FILE.stat().st_size == 0:
        raise SystemExit(
            f"File {IN_FILE} trong hoac chua luu. Hay save nfl.txt trong editor roi chay lai."
        )

    lines = IN_FILE.read_text(encoding="utf-8").splitlines()
    if not lines:
        raise SystemExit("nfl.txt trong")

    header = lines[0].split("\t")
    col_id = header.index("id") if "id" in header else 0
    col_ten = header.index("ten") if "ten" in header else 1
    col_img = header.index("link_anh_dau") if "link_anh_dau" in header else 2
    col_gia = header.index("gia") if "gia" in header else 3
    col_des = header.index("des") if "des" in header else -1

    rows_out: list[dict[str, str]] = []
    with_color = 0

    for raw in lines[1:]:
        if not raw.strip():
            continue
        parts = raw.split("\t")
        if len(parts) <= col_ten:
            continue

        raw_title = parts[col_ten].strip()
        _clean, color = split_nfl_title_color(raw_title)
        if color:
            with_color += 1

        price = parts[col_gia].strip() if col_gia < len(parts) else ""
        try:
            price_fmt = f"{float(price):.2f}" if price else ""
        except ValueError:
            price_fmt = price

        image = parts[col_img].strip() if col_img < len(parts) else ""
        des = parts[col_des].strip() if col_des >= 0 and col_des < len(parts) else ""
        pid = parts[col_id].strip() if col_id < len(parts) else ""

        rows_out.append(
            {
                "title": raw_title,
                "description": des,
                "images": image,
                "category": "NFL Jerseys",
                "price": price_fmt,
                "color": color,
                "brand": BRAND,
                "tag": pid,
                "vendor": BRAND,
            }
        )

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "title",
        "description",
        "images",
        "category",
        "price",
        "color",
        "brand",
        "tag",
        "vendor",
    ]
    with OUT_FILE.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows_out)

    total = len(rows_out)
    print(f"[done] rows={total} co_mau={with_color} ({with_color/total*100:.1f}%)")
    print(f"file={OUT_FILE}")


if __name__ == "__main__":
    main()
