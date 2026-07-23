from __future__ import annotations

import io
import re
from typing import Any

import pandas as pd

from models.product import Product

COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "title": ("title", "name", "product name", "product_name", "ten", "tên", "tieu de", "tiêu đề"),
    "description": (
        "description",
        "descriptio",
        "body (html)",
        "body",
        "mo ta",
        "mô tả",
        "short description",
        "content",
    ),
    "price": ("price", "regular price", "regular_price", "variant price", "gia", "giá", "sale price"),
    "sku": ("sku", "variant sku", "product sku", "ma sp", "mã sp"),
    "vendor": ("vendor", "brand", "thuong hieu", "thương hiệu", "manufacturer"),
    "categories": (
        "categories",
        "category",
        "product category",
        "danh muc",
        "danh mục",
        "cat",
        "cate",
    ),
    "tags": ("tags", "tag"),
    "url": ("url", "link", "product url", "permalink", "external url"),
    "product_type": ("product type", "product_type", "type", "loai sp"),
    "status": ("status", "published", "stock status"),
    "currency": ("currency",),
    "id": ("id", "product id", "product_id"),
    "images": ("images", "image", "image src", "image_src", "image urls", "image_urls", "anh", "ảnh"),
}


def _normalize_header(name: str) -> str:
    return re.sub(r"\s+", " ", str(name or "").strip().lower())


def _build_header_map(columns: list[str]) -> dict[str, str]:
    normalized = {_normalize_header(col): col for col in columns}
    mapping: dict[str, str] = {}

    for field, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in normalized:
                mapping[field] = normalized[alias]
                break

    for col in columns:
        norm = _normalize_header(col)
        if _is_indexed_image_column(norm):
            continue
        if norm in {"images", "image"} or norm.startswith("image "):
            if "images" not in mapping:
                mapping["images"] = col

    return mapping


def _split_image_urls(value: Any) -> list[str]:
    """Tách nhiều URL ảnh trong một ô (Woo: dấu phẩy, hoặc pipe cũ)."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return []
    text = str(value).strip()
    if not text:
        return []

    if "|" in text and re.search(r"https?://", text):
        parts = text.split("|")
    elif "," in text and re.search(r"https?://", text):
        parts = re.split(r",(?=https?://)", text)
    else:
        parts = [text]

    return [p.strip() for p in parts if p and str(p).strip()]


def _split_list(value: Any, prefer_pipe: bool = False) -> list[str]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return []
    text = str(value).strip()
    if not text:
        return []

    if prefer_pipe and "|" in text:
        parts = text.split("|")
    elif "," in text and "http" not in text.split(",")[0][:10]:
        parts = [p.strip() for p in text.split(",")]
    elif "|" in text:
        parts = text.split("|")
    else:
        parts = [text]

    return [p.strip() for p in parts if p and str(p).strip()]


def _parse_price(value: Any) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    if not text:
        return None
    cleaned = re.sub(r"[^\d.,-]", "", text)
    if not cleaned:
        return None
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(",", "")
    else:
        cleaned = cleaned.replace(",", ".")
    try:
        return round(float(cleaned), 2)
    except ValueError:
        return None


def _is_indexed_image_column(norm: str) -> bool:
    if norm in {"image 2", "image 3", "image 4", "image 5"}:
        return True
    patterns = (
        r"^images?\[\d+\]$",
        r"^image\s+\d+$",
        r"^images?_\d+$",
        r"^images?\d+$",
    )
    return any(re.match(pattern, norm) for pattern in patterns) and norm not in {"images", "image"}


def _image_column_sort_key(col: str) -> int:
    norm = _normalize_header(col)
    if norm in {"images", "image"}:
        return 0
    bracket = re.search(r"\[(\d+)\]", norm)
    if bracket:
        return int(bracket.group(1))
    spaced = re.match(r"^image\s+(\d+)$", norm)
    if spaced:
        return int(spaced.group(1))
    underscored = re.match(r"^images?_(\d+)$", norm)
    if underscored:
        return int(underscored.group(1))
    suffixed = re.match(r"^images?(\d+)$", norm)
    if suffixed:
        return int(suffixed.group(1))
    return 0


def _list_image_columns(columns: list[str]) -> tuple[list[str], str | None]:
    indexed: list[str] = []
    combined: str | None = None
    combined_names = {
        "images",
        "image",
        "image src",
        "image_src",
        "image urls",
        "image_urls",
        "anh",
        "ảnh",
    }

    for col in columns:
        norm = _normalize_header(col)
        if _is_indexed_image_column(norm):
            indexed.append(col)
        elif norm in combined_names:
            combined = col

    indexed.sort(key=_image_column_sort_key)
    return indexed, combined


def _append_image_url(urls: list[str], value: Any) -> None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return
    text = str(value).strip()
    if not text:
        return
    if text.startswith("http") and text not in urls:
        urls.append(text)


def _collect_images(row: dict[str, Any], columns: list[str], header_map: dict[str, str]) -> list[str]:
    urls: list[str] = []
    indexed_cols, combined_col = _list_image_columns(columns)

    for col in indexed_cols:
        _append_image_url(urls, row.get(col))

    images_col = header_map.get("images") or combined_col
    if images_col and images_col not in indexed_cols and images_col in row:
        for part in _split_image_urls(row.get(images_col)):
            _append_image_url(urls, part)

    return urls


def _row_to_product(row: dict[str, Any], columns: list[str], header_map: dict[str, str], index: int) -> Product | None:
    def cell(field: str) -> Any:
        col = header_map.get(field)
        if not col:
            return None
        return row.get(col)

    title = str(cell("title") or "").strip()
    if not title:
        return None

    categories = _split_list(cell("categories"))
    tags = _split_list(cell("tags"))
    images = _collect_images(row, columns, header_map)

    product_id = str(cell("id") or index + 1).strip()
    sku = str(cell("sku") or "").strip() or None

    return Product(
        source="csv-import",
        id=product_id,
        title=title,
        description=str(cell("description") or "").strip() or None,
        price=_parse_price(cell("price")),
        currency=str(cell("currency") or "").strip() or None,
        url=str(cell("url") or "").strip() or None,
        image_urls=images,
        vendor=str(cell("vendor") or "").strip() or None,
        sku=sku,
        tags=tags,
        categories=categories,
        product_type=str(cell("product_type") or "").strip() or None,
        status=str(cell("status") or "").strip() or None,
        variants=[],
    )


def read_csv_dataframe(file_bytes: bytes) -> pd.DataFrame:
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return pd.read_csv(io.BytesIO(file_bytes), encoding=encoding, dtype=str, keep_default_na=False)
        except UnicodeDecodeError:
            continue
    return pd.read_csv(io.BytesIO(file_bytes), dtype=str, keep_default_na=False)


def parse_csv_to_products(file_bytes: bytes) -> tuple[list[Product], pd.DataFrame]:
    df = read_csv_dataframe(file_bytes)
    if df.empty:
        return [], df

    columns = list(df.columns)
    header_map = _build_header_map(columns)
    products: list[Product] = []

    for index, row in df.iterrows():
        product = _row_to_product(row.to_dict(), columns, header_map, int(index))
        if product:
            products.append(product)

    return products, df


def list_categories(products: list[Product]) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    uncategorized = 0

    for product in products:
        if product.categories:
            for cat in product.categories:
                name = str(cat).strip()
                if name:
                    counts[name] = counts.get(name, 0) + 1
        else:
            uncategorized += 1

    items = sorted(counts.items(), key=lambda item: (-item[1], item[0].lower()))
    if uncategorized:
        items.append(("(Không có category)", uncategorized))
    return items


def filter_products_by_category(products: list[Product], category: str | None) -> list[Product]:
    if not category or category == "Tất cả":
        return list(products)

    if category == "(Không có category)":
        return [p for p in products if not p.categories]

    target = category.strip().lower()
    return [p for p in products if any(str(c).strip().lower() == target for c in p.categories)]


def slug_from_category(category: str) -> str:
    slug = re.sub(r"[^\w\-]+", "-", category.strip().lower())
    return slug.strip("-") or "products"
