from __future__ import annotations

import json
import time

import requests

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def extract_remix_context(text: str) -> dict:
    marker = "window.__remixContext = "
    start = text.find(marker)
    if start < 0:
        raise ValueError("missing remix context")
    start += len(marker)
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : i + 1])
    raise ValueError("unclosed remix json")


def fetch_page_text(
    session: requests.Session,
    url: str,
    *,
    timeout: float = 90,
    max_retries: int = 6,
) -> str:
    last_response: requests.Response | None = None
    for attempt in range(max_retries):
        response = session.get(url, timeout=timeout)
        last_response = response
        if response.status_code in {429, 500, 502, 503, 504}:
            time.sleep(min(2.0 * (attempt + 1), 20.0))
            continue
        response.raise_for_status()
        return response.text
    if last_response is not None:
        last_response.raise_for_status()
    raise requests.RequestException(f"request failed: {url}")
