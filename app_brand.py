"""Branding — dùng chung UI, launcher và build."""

APP_NAME = "Crawl Feed GMC"
APP_SHORT_NAME = "CrawlFeedGMC"
APP_VERSION = "1.0.10"
APP_TAGLINE = "Crawl sản phẩm · Tùy chỉnh · Xuất feed Google Merchant Center"
APP_PAGE_ICON = "📋"


def app_build_id() -> str:
    """Git short SHA trên VPS — đối chiếu đã deploy commit mới chưa."""
    try:
        from pathlib import Path
        import subprocess

        root = Path(__file__).resolve().parent
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=root,
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=3,
        ).strip()
    except Exception:
        return APP_VERSION
