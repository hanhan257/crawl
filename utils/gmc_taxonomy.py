from __future__ import annotations

import html
import re
from dataclasses import dataclass

from models.product import Product

# Giá trị Google Merchant Center — https://support.google.com/merchants/answer/6324463
GMC_GENDER_OPTIONS = ["", "male", "female", "unisex"]

GMC_AGE_GROUP_OPTIONS = ["", "adult", "kids", "toddler", "infant", "newborn"]

GMC_AGE_GROUP_DEFAULT = "adult"

GMC_DEFAULT_GENDER = "unisex"
GMC_DEFAULT_GOOGLE_CATEGORY_ID = "212"


@dataclass(frozen=True)
class GmcCategorySuggestion:
    id: str
    label_vi: str
    label_en: str
    keywords: tuple[str, ...]


# Thứ tự: category cụ thể trước, category rộng (212) sau.
# Tham khảo taxonomy: https://productcategory.net/
GMC_CATEGORY_SUGGESTIONS: tuple[GmcCategorySuggestion, ...] = (
    GmcCategorySuggestion("207", "Shorts", "Shorts", ("shorts", "short pant", "quần short", "bermuda")),
    GmcCategorySuggestion("173", "Mũ / Hats", "Hats", ("hat", "cap", "beanie", "snapback", "bucket hat", "mũ", "nón")),
    GmcCategorySuggestion("5001", "Đồ chơi mèo", "Cat Toys", ("cat toy", "kitten toy", "catnip", "đồ chơi mèo", "meo")),
    GmcCategorySuggestion("5010", "Đồ chơi chó", "Dog Toys", ("dog toy", "puppy toy", "dog chew", "đồ chơi chó", "cho cho")),
    GmcCategorySuggestion("2169", "Mug", "Mugs", ("mug", "coffee mug", "ceramic mug", " cốc ", "ly sứ")),
    GmcCategorySuggestion(
        "2951",
        "Tumblers",
        "Tumblers",
        ("tumbler", "travel mug", "insulated cup", "yeti", "stanley cup"),
    ),
    GmcCategorySuggestion(
        "6049",
        "Coffee & Tea Cups",
        "Coffee & Tea Cups",
        ("coffee cup", "tea cup", "teacup", "espresso cup", "cốc cà phê", "cốc trà"),
    ),
    GmcCategorySuggestion(
        "188",
        "Trang sức",
        "Jewelry",
        ("jewelry", "jewellery", "necklace", "ring", "bracelet", "earring", "pendant", "trang sức", "trang suc"),
    ),
    GmcCategorySuggestion(
        "5181",
        "Túi / Vali",
        "Luggage & Bags",
        (
            "backpack",
            "handbag",
            "tote bag",
            "duffel",
            "luggage",
            "suitcase",
            "crossbody",
            "shoulder bag",
            "vali",
            "túi xách",
            "tui xach",
        ),
    ),
    GmcCategorySuggestion(
        "212",
        "Thời trang",
        "Apparel & Accessories",
        (
            "apparel",
            "clothing",
            "fashion",
            "dress",
            "shirt",
            "blouse",
            "hoodie",
            "sweater",
            "jacket",
            "pants",
            "trousers",
            "legging",
            "thoi trang",
            "thời trang",
            "quần áo",
            "quan ao",
            "top",
            "tee",
            "t-shirt",
            "tshirt",
            "sneaker",
            "sneakers",
            "shoe",
            "shoes",
            "footwear",
        ),
    ),
)


def category_dropdown_label(category: GmcCategorySuggestion) -> str:
    return f"{category.id} — {category.label_vi} ({category.label_en})"


GMC_CATEGORY_DROPDOWN_OPTIONS: list[str] = [""] + [
    category_dropdown_label(c) for c in GMC_CATEGORY_SUGGESTIONS
]

_CATEGORY_ID_BY_LABEL = {
    category_dropdown_label(c): c.id for c in GMC_CATEGORY_SUGGESTIONS
}

GMC_DEFAULT_GOOGLE_CATEGORY = category_dropdown_label(
    next(c for c in GMC_CATEGORY_SUGGESTIONS if c.id == GMC_DEFAULT_GOOGLE_CATEGORY_ID)
)


def category_id_from_option(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text in _CATEGORY_ID_BY_LABEL:
        return _CATEGORY_ID_BY_LABEL[text]
    match = re.match(r"^(\d+)", text)
    return match.group(1) if match else text


def category_option_from_id(category_id: object) -> str:
    cid = str(category_id or "").strip()
    if not cid:
        return ""
    for category in GMC_CATEGORY_SUGGESTIONS:
        if category.id == cid:
            return category_dropdown_label(category)
    return cid


def _product_search_text(product: Product) -> str:
    parts: list[str] = [
        product.title or "",
        product.description or "",
        product.product_type or "",
        product.vendor or "",
    ]
    parts.extend(product.categories or [])
    parts.extend(product.tags or [])
    raw = html.unescape(" ".join(str(p) for p in parts if p)).lower()
    return re.sub(r"\s+", " ", raw)


def _keyword_in_text(keyword: str, text: str) -> bool:
    key = keyword.strip().lower()
    if not key:
        return False
    if " " in key:
        return key in text
    return re.search(rf"\b{re.escape(key)}\b", text) is not None


def suggest_google_product_category(product: Product) -> str:
    text = _product_search_text(product)
    if not text.strip():
        return ""

    for category in GMC_CATEGORY_SUGGESTIONS:
        for keyword in category.keywords:
            if _keyword_in_text(keyword, text):
                return category.id
    return ""


def suggest_gmc_gender(product: Product) -> str:
    text = _product_search_text(product)
    female_hits = sum(
        1
        for kw in ("women", "woman", "female", "ladies", "lady", "girl", "girls", "nữ", "nu")
        if _keyword_in_text(kw, text)
    )
    male_hits = sum(
        1
        for kw in ("men", "man", "male", "boy", "boys", "nam")
        if _keyword_in_text(kw, text)
    )
    if female_hits > male_hits and female_hits > 0:
        return "female"
    if male_hits > female_hits and male_hits > 0:
        return "male"
    if "unisex" in text:
        return "unisex"
    return ""


def resolve_gmc_category_display(product: Product, default_id: str = "") -> str:
    cid = (
        str(product.google_product_category or "").strip()
        or suggest_google_product_category(product)
        or str(default_id or "").strip()
    )
    return category_option_from_id(cid) if cid else ""


def resolve_gmc_gender_display(product: Product, default: str = "") -> str:
    value = str(product.gmc_gender or "").strip() or suggest_gmc_gender(product) or str(default or "").strip()
    return value if value in GMC_GENDER_OPTIONS else value


def resolve_gmc_age_group_display(product: Product, default: str = GMC_AGE_GROUP_DEFAULT) -> str:
    value = str(product.gmc_age_group or "").strip() or str(default or GMC_AGE_GROUP_DEFAULT).strip()
    lowered = value.lower()
    return lowered if lowered in GMC_AGE_GROUP_OPTIONS[1:] else GMC_AGE_GROUP_DEFAULT


def normalize_age_group_for_export(value: object) -> str:
    text = str(value or "").strip().lower()
    if text in GMC_AGE_GROUP_OPTIONS[1:]:
        return text
    return GMC_AGE_GROUP_DEFAULT
