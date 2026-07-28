from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TypeVar

T = TypeVar("T")

ProgressCallback = Callable[[int, int, int], None]
API_LIST_MAX_WORKERS = 500
# Woo/Shopify list API dễ 429 nếu bắn hàng trăm page cùng lúc.
API_LIST_WAVE_SIZE = 48
EMPTY_PAGE_RETRY_ROUNDS = 3


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


def _merge_page_batches(
    page_batches: dict[int, list[T]],
    *,
    limit: int | None = None,
) -> list[T]:
    items: list[T] = []
    for page_num in sorted(page_batches.keys()):
        batch = page_batches.get(page_num) or []
        if not batch:
            continue
        items.extend(batch)
        if limit is not None and len(items) >= limit:
            return items[:limit]
    return items


def _fetch_page_map(
    fetch_page: Callable[[int], list[T]],
    pages: list[int],
    *,
    max_workers: int,
) -> dict[int, list[T]]:
    """Fetch pages; failed requests become []."""
    if not pages:
        return {}
    workers = max(1, min(max_workers, len(pages)))
    out: dict[int, list[T]] = {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(fetch_page, page_num): page_num for page_num in pages}
        for future in as_completed(futures):
            page_num = futures[future]
            try:
                out[page_num] = future.result()
            except Exception:
                out[page_num] = []
    return out


def _empty_pages_to_retry(
    page_batches: dict[int, list[T]],
    *,
    start_page: int,
    end_page: int,
    per_page: int,
) -> list[int]:
    """Trang trống giữa khoảng đã biết total pages — thường do 429/timeout."""
    holes: list[int] = []
    for page_num in range(start_page, end_page + 1):
        batch = page_batches.get(page_num)
        if batch is None or len(batch) == 0:
            # Trang cuối có thể thật sự ngắn / rỗng sau khi hết SP — vẫn thử lại nếu trống hẳn.
            holes.append(page_num)
            continue
        # Trang giữa mà < per_page → nghi lỗi cắt sớm, thử lại (trừ trang cuối).
        if page_num < end_page and len(batch) < per_page:
            holes.append(page_num)
    return holes


def fetch_pages_in_waves(
    fetch_page: Callable[[int], list[T]],
    *,
    per_page: int,
    first_batch: list[T],
    max_pages: int | None = None,
    max_workers: int = 1,
    limit: int | None = None,
    progress_callback: ProgressCallback | None = None,
    estimated_pages: int | None = None,
) -> tuple[list[T], int]:
    """Fetch paginated API results with optional parallel page waves."""
    page_batches: dict[int, list[T]] = {1: list(first_batch)}
    pages_fetched = 1

    def progress_total() -> int:
        if estimated_pages and estimated_pages > 0:
            return estimated_pages
        if max_pages and max_pages > 0:
            return max_pages
        return max(pages_fetched, pages_fetched + 1)

    def report_progress(items: list[T] | None = None) -> None:
        nonlocal pages_fetched
        pages_fetched = len(page_batches)
        if progress_callback:
            progress_callback(
                pages_fetched,
                progress_total(),
                _matched_count(items if items is not None else _merge_page_batches(page_batches, limit=limit), limit),
            )

    report_progress()

    if limit is not None and len(first_batch) >= limit:
        return first_batch[:limit], pages_fetched

    if len(first_batch) < per_page:
        return list(first_batch), pages_fetched

    workers = max(1, min(max_workers, API_LIST_MAX_WORKERS))
    end_page = estimated_pages or max_pages
    if end_page and end_page > 1 and workers > 1:
        if max_pages:
            end_page = min(end_page, max_pages)
        pages_to_fetch = list(range(2, end_page + 1))
        if limit is not None:
            max_item_pages = max(1, (limit + per_page - 1) // per_page)
            pages_to_fetch = [page for page in pages_to_fetch if page <= max_item_pages]

        if pages_to_fetch:
            # Fetch theo đợt — tránh 500 request cùng lúc (Woo joyoce mất ~2/3 SP).
            wave = max(1, min(workers, API_LIST_WAVE_SIZE))
            for idx in range(0, len(pages_to_fetch), wave):
                chunk = pages_to_fetch[idx : idx + wave]
                fetched = _fetch_page_map(fetch_page, chunk, max_workers=wave)
                page_batches.update(fetched)
                report_progress()

            # Retry trang trống / lỗi (429) vài vòng với ít luồng hơn.
            retry_workers = max(1, min(24, wave))
            for _round in range(EMPTY_PAGE_RETRY_ROUNDS):
                holes = _empty_pages_to_retry(
                    page_batches,
                    start_page=2,
                    end_page=pages_to_fetch[-1],
                    per_page=per_page,
                )
                if not holes:
                    break
                # Trang cuối thật sự hết SP → bỏ nếu các trang trước đã đủ/đầy.
                if len(holes) == 1 and holes[0] == pages_to_fetch[-1]:
                    prev = page_batches.get(holes[0] - 1) or []
                    if prev and len(prev) < per_page:
                        break
                fetched = _fetch_page_map(fetch_page, holes, max_workers=retry_workers)
                page_batches.update(fetched)
                report_progress()

            items = _merge_page_batches(page_batches, limit=limit)
            last_batch = page_batches.get(end_page) or []
            if (
                (limit is None or len(items) < limit)
                and len(last_batch) >= per_page
                and estimated_pages
                and end_page >= estimated_pages
            ):
                next_page = end_page + 1
            else:
                if progress_callback:
                    progress_callback(pages_fetched, pages_fetched, _matched_count(items, limit))
                return items, pages_fetched
        else:
            items = _merge_page_batches(page_batches, limit=limit)
            if progress_callback:
                progress_callback(pages_fetched, pages_fetched, _matched_count(items, limit))
            return items, pages_fetched
    else:
        next_page = 2
        items = list(first_batch)

    while True:
        if max_pages and next_page > max_pages:
            break
        if limit is not None and len(_merge_page_batches(page_batches, limit=limit)) >= limit:
            break

        if workers <= 1:
            try:
                batch = fetch_page(next_page)
            except Exception:
                break
            pages_fetched += 1
            page_batches[next_page] = batch
            if not batch:
                break
            report_progress()
            items = _merge_page_batches(page_batches, limit=limit)
            if limit is not None and len(items) >= limit:
                break
            if len(batch) < per_page:
                break
            next_page += 1
            continue

        wave_size = workers
        pages_left = _remaining_pages(limit, per_page, len(_merge_page_batches(page_batches)))
        if pages_left is not None:
            wave_size = min(wave_size, pages_left)

        wave_pages = list(range(next_page, next_page + wave_size))
        if max_pages:
            wave_pages = [page for page in wave_pages if page <= max_pages]
        if not wave_pages:
            break

        failed_pages: list[int] = []

        with ThreadPoolExecutor(max_workers=len(wave_pages)) as executor:
            futures = {
                executor.submit(fetch_page, page_num): page_num for page_num in wave_pages
            }
            for future in as_completed(futures):
                page_num = futures[future]
                try:
                    page_batches[page_num] = future.result()
                except Exception:
                    failed_pages.append(page_num)

        for page_num in sorted(failed_pages):
            try:
                page_batches[page_num] = fetch_page(page_num)
            except Exception:
                page_batches[page_num] = []

        stop = False
        for page_num in sorted(wave_pages):
            batch = page_batches.get(page_num) or []
            pages_fetched = len(page_batches)
            if not batch:
                stop = True
                break
            report_progress()
            items = _merge_page_batches(page_batches, limit=limit)
            if limit is not None and len(items) >= limit:
                stop = True
                break
            if len(batch) < per_page:
                stop = True
                break

        if stop:
            break
        next_page += len(wave_pages)

    items = _merge_page_batches(page_batches, limit=limit)
    if progress_callback:
        progress_callback(len(page_batches), len(page_batches), _matched_count(items, limit))

    return items, len(page_batches)
