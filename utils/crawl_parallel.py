from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TypeVar

T = TypeVar("T")

ProgressCallback = Callable[[int, int, int], None]


def _matched_count(items: list, limit: int | None) -> int:
    if limit is None:
        return len(items)
    return min(len(items), limit)


def _remaining_pages(limit: int | None, per_page: int, current_count: int) -> int | None:
    if limit is None or per_page <= 0:
        return None
    remaining_items = max(limit - current_count, 0)
    if remaining_items <= 0:
        return 0
    return max(1, (remaining_items + per_page - 1) // per_page)


def fetch_pages_in_waves(
    fetch_page: Callable[[int], list[T]],
    *,
    per_page: int,
    first_batch: list[T],
    max_pages: int | None = None,
    max_workers: int = 1,
    limit: int | None = None,
    progress_callback: ProgressCallback | None = None,
) -> tuple[list[T], int]:
    """Fetch paginated API results with optional parallel page waves."""
    items: list[T] = list(first_batch)
    pages_fetched = 1

    if limit is not None and len(items) >= limit:
        if progress_callback:
            progress_callback(pages_fetched, pages_fetched, _matched_count(items, limit))
        return items[:limit], pages_fetched

    if len(first_batch) < per_page:
        if progress_callback:
            progress_callback(pages_fetched, pages_fetched, _matched_count(items, limit))
        return items, pages_fetched

    next_page = 2
    workers = max(1, min(max_workers, 32))

    while True:
        if max_pages and next_page > max_pages:
            break
        if limit is not None and len(items) >= limit:
            break

        if workers <= 1:
            try:
                batch = fetch_page(next_page)
            except Exception:
                break
            pages_fetched += 1
            if not batch:
                break
            items.extend(batch)
            if progress_callback:
                est = max_pages or max(pages_fetched + 1, pages_fetched)
                progress_callback(pages_fetched, est, _matched_count(items, limit))
            if limit is not None and len(items) >= limit:
                break
            if len(batch) < per_page:
                break
            next_page += 1
            continue

        wave_size = workers
        pages_left = _remaining_pages(limit, per_page, len(items))
        if pages_left is not None:
            wave_size = min(wave_size, pages_left)

        wave_pages = list(range(next_page, next_page + wave_size))
        if max_pages:
            wave_pages = [page for page in wave_pages if page <= max_pages]
        if not wave_pages:
            break

        batches: dict[int, list[T]] = {}
        failed_pages: list[int] = []

        with ThreadPoolExecutor(max_workers=len(wave_pages)) as executor:
            futures = {
                executor.submit(fetch_page, page_num): page_num for page_num in wave_pages
            }
            for future in as_completed(futures):
                page_num = futures[future]
                try:
                    batches[page_num] = future.result()
                except Exception:
                    failed_pages.append(page_num)

        for page_num in sorted(failed_pages):
            try:
                batches[page_num] = fetch_page(page_num)
            except Exception:
                batches[page_num] = []

        stop = False
        for page_num in sorted(batches.keys()):
            batch = batches[page_num]
            pages_fetched += 1
            if not batch:
                stop = True
                break
            items.extend(batch)
            if limit is not None and len(items) >= limit:
                stop = True
                break
            if len(batch) < per_page:
                stop = True
                break

        if progress_callback:
            est_total = max_pages or max(pages_fetched + wave_size, pages_fetched)
            progress_callback(
                min(pages_fetched, est_total),
                est_total,
                _matched_count(items, limit),
            )

        if stop:
            break
        next_page += len(wave_pages)

    if limit is not None and len(items) > limit:
        items = items[:limit]

    if progress_callback:
        progress_callback(pages_fetched, pages_fetched, _matched_count(items, limit))

    return items, pages_fetched
