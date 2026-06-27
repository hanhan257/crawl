from __future__ import annotations

import time
from typing import Any

import requests

DEFAULT_CONNECT_TIMEOUT = 15
DEFAULT_READ_TIMEOUT = 90
DEFAULT_REQUEST_TIMEOUT = (DEFAULT_CONNECT_TIMEOUT, DEFAULT_READ_TIMEOUT)


def get_with_retry(
    session: requests.Session,
    url: str,
    *,
    max_retries: int = 4,
    timeout: float | tuple[float, float] = DEFAULT_REQUEST_TIMEOUT,
    backoff: float = 2.0,
    **kwargs: Any,
) -> requests.Response:
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            response = session.get(url, timeout=timeout, **kwargs)
            if response.status_code in {429, 502, 503, 504} and attempt + 1 < max_retries:
                time.sleep(min(backoff * (attempt + 1), 12))
                continue
            response.raise_for_status()
            return response
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_exc = exc
            if attempt + 1 < max_retries:
                time.sleep(min(backoff * (attempt + 1), 12))
                continue
            raise
        except requests.HTTPError as exc:
            last_exc = exc
            status = exc.response.status_code if exc.response is not None else None
            if status in {429, 502, 503, 504} and attempt + 1 < max_retries:
                time.sleep(min(backoff * (attempt + 1), 12))
                continue
            raise
    if last_exc is not None:
        raise last_exc
    raise requests.RequestException(f"Không tải được {url}")
