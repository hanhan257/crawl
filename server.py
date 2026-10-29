"""Wrapper để Muse gọi tool Crawl Feed GMC.

Đặt file này ở THƯ MỤC GỐC của repo Crawl Feed GMC (cạnh app.py),
rồi chạy:
    CRAWL_API_KEY=<key-rieng-cua-tool> python3 server.py --port 8000

KHÔNG commit key vào git. Key này là key của CHÍNH tool crawl của bạn,
dùng để Muse xác thực khi gọi wrapper, không phải key của Meta/Muse,
không phải key của Meta Model API.

Luồng Muse -> tool:
    Muse (app) -> custom connector -> https://<domain-cua-ban>/api/jobs
    -> server.py -> utils.crawl_runner.run_auto_crawl() -> crawlers/
    -> trả về products, lưu CSV trên server.

Vì crawl có thể chạy rất lâu (hàng chục nghìn sản phẩm), wrapper chạy
theo job bất đồng bộ, KHÔNG trả toàn bộ sản phẩm trong 1 response:
    POST /api/jobs            {url, limit, max_workers} -> {job_id}
    GET  /api/jobs/<id>       -> trạng thái, platform, số sản phẩm, lỗi
    GET  /api/jobs/<id>/products?offset=0&limit=50
                              -> từng trang sản phẩm (title, cate, gia,
                                 link_hinh, link_san_pham)
    GET  /api/jobs/<id>/csv   -> tải file CSV đầy đủ
    GET  /health              -> kiểm tra server sống hay không
    GET  /api/tools           -> mô tả các endpoint cho connector

Chỉ dùng thư viện chuẩn Python, không cần cài thêm gì ngoài những gì
repo Crawl Feed GMC đã cần (requests, pandas...). Chạy được cùng máy
với Streamlit, cổng khác nhau (ví dụ Streamlit 8501, wrapper 8000),
rồi dùng nginx reverse proxy ra HTTPS public.
"""

from __future__ import annotations

import csv
import json
import os
import sys
import threading
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

# Repo root = thư mục chứa file này (cũng là nơi có app.py, crawlers/).
REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

JOBS_DIR = os.path.join(REPO_ROOT, "muse_jobs")
os.makedirs(JOBS_DIR, exist_ok=True)

JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()


def product_to_row(p) -> dict:
    """Product (models/product.py) -> đúng format CSV của tool."""
    d = p.to_dict() if hasattr(p, "to_dict") else dict(p)
    categories = d.get("categories") or []
    cate = categories[0] if categories else (d.get("product_type") or "")
    images = d.get("image_urls") or []
    return {
        "title": d.get("title") or "",
        "cate": cate,
        "gia": d.get("price"),
        "currency": d.get("currency") or "",
        "link_hinh": ",".join(images),
        "link_san_pham": d.get("url") or "",
        "vendor": d.get("vendor") or "",
        "sku": d.get("sku") or "",
    }


def write_csv(job_dir: str, rows: list[dict]) -> str:
    path = os.path.join(job_dir, "products.csv")
    fields = ["title", "cate", "gia", "currency", "link_hinh", "link_san_pham", "vendor", "sku"]
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})
    return path


def run_job(job_id: str, url: str, limit, max_workers: int) -> None:
    job_dir = os.path.join(JOBS_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)
    try:
        from utils.crawl_runner import run_auto_crawl

        products, target = run_auto_crawl(
            url, limit=limit, max_workers=max_workers,
        )
        rows = [product_to_row(p) for p in products]
        csv_path = write_csv(job_dir, rows)
        with open(os.path.join(job_dir, "products.json"), "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False)
        with JOBS_LOCK:
            JOBS[job_id].update({
                "status": "done",
                "platform": getattr(target, "platform", None),
                "crawl_type": getattr(target, "crawl_type", None),
                "count": len(rows),
                "csv_path": csv_path,
            })
    except Exception as e:  # noqa: BLE001 - trả lỗi về cho Muse, không crash server
        with JOBS_LOCK:
            JOBS[job_id].update({
                "status": "error",
                "error": str(e),
                "traceback": traceback.format_exc()[-2000:],
            })


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # giữ log gọn, không in key
        sys.stderr.write("%s %s\n" % (self.address_string(), args[0] % args[1:]))

    def _send(self, code: int, obj) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _auth_ok(self) -> bool:
        expected = os.environ.get("CRAWL_API_KEY", "").strip()
        if not expected:
            # Chưa đặt key = từ chối luôn, tránh vô tình mở tool ra public.
            return False
        got = (self.headers.get("X-API-Key") or "").strip()
        auth = (self.headers.get("Authorization") or "").strip()
        if auth.startswith("Bearer "):
            got = auth[7:].strip()
        return got == expected

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8") or "{}")

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        qs = parse_qs(parsed.query)

        if path == "/health":
            return self._send(200, {"ok": True, "service": "crawl-feed-gmc-muse-wrapper"})
        if path == "/api/tools":
            return self._send(200, {"tools": [
                {"name": "crawl_start", "method": "POST", "path": "/api/jobs",
                 "body": {"url": "link shop, ví dụ https://munakin.com/", "limit": "số sản phẩm tối đa, để trống = tất cả", "max_workers": "số luồng, mặc định 1"},
                 "returns": {"job_id": "..."}},
                {"name": "crawl_status", "method": "GET", "path": "/api/jobs/<job_id>",
                 "returns": {"status": "running|done|error", "platform": "...", "count": 0}},
                {"name": "crawl_products", "method": "GET", "path": "/api/jobs/<job_id>/products?offset=0&limit=50",
                 "returns": "từng trang sản phẩm, mỗi sản phẩm có title, cate, gia, link_hinh, link_san_pham"},
                {"name": "crawl_csv", "method": "GET", "path": "/api/jobs/<job_id>/csv",
                 "returns": "file CSV đầy đủ"},
            ]})
        if not self._auth_ok():
            return self._send(401, {"error": "Thiếu hoặc sai X-API-Key. Đặt CRAWL_API_KEY trên server và gửi lại."})

        parts = [p for p in path.split("/") if p]
        if len(parts) == 3 and parts[0] == "api" and parts[1] == "jobs":
            job_id = parts[2]
            with JOBS_LOCK:
                job = dict(JOBS.get(job_id) or {})
            if not job:
                # Job có thể đã restart server: đọc lại từ thư mục job.
                job_dir = os.path.join(JOBS_DIR, job_id)
                if os.path.isdir(job_dir):
                    job = {"job_id": job_id, "status": "unknown", "note": "Server đã restart, xem file CSV trong thư mục job nếu có."}
                else:
                    return self._send(404, {"error": "Không tìm thấy job_id"})
            job.pop("traceback", None)
            return self._send(200, job)
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "jobs" and parts[3] == "products":
            job_id = parts[2]
            job_dir = os.path.join(JOBS_DIR, job_id)
            data_path = os.path.join(job_dir, "products.json")
            if not os.path.exists(data_path):
                return self._send(409, {"error": "Job chưa xong hoặc không có kết quả. Kiểm tra /api/jobs/<id> trước."})
            with open(data_path, encoding="utf-8") as f:
                rows = json.load(f)
            offset = int((qs.get("offset") or ["0"])[0])
            limit = min(int((qs.get("limit") or ["50"])[0]), 200)
            return self._send(200, {
                "job_id": job_id, "total": len(rows), "offset": offset,
                "limit": limit, "products": rows[offset:offset + limit],
            })
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "jobs" and parts[3] == "csv":
            job_id = parts[2]
            csv_path = os.path.join(JOBS_DIR, job_id, "products.csv")
            if not os.path.exists(csv_path):
                return self._send(409, {"error": "Job chưa xong hoặc không có CSV."})
            with open(csv_path, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="crawl-{job_id}.csv"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return None
        return self._send(404, {"error": "Không tìm thấy endpoint"})

    def do_POST(self):  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path.rstrip("/") != "/api/jobs":
            return self._send(404, {"error": "Không tìm thấy endpoint"})
        if not self._auth_ok():
            return self._send(401, {"error": "Thiếu hoặc sai X-API-Key."})
        try:
            body = self._read_json()
        except Exception as e:  # noqa: BLE001
            return self._send(400, {"error": f"JSON không hợp lệ: {e}"})
        url = (body.get("url") or "").strip()
        if not url:
            return self._send(400, {"error": "Thiếu trường url."})
        limit = body.get("limit")
        if limit is not None:
            try:
                limit = int(limit)
            except (TypeError, ValueError):
                return self._send(400, {"error": "limit phải là số nguyên."})
        try:
            max_workers = int(body.get("max_workers") or 1)
        except (TypeError, ValueError):
            max_workers = 1
        max_workers = max(1, min(max_workers, 60))
        job_id = uuid.uuid4().hex[:12]
        with JOBS_LOCK:
            JOBS[job_id] = {"job_id": job_id, "status": "running", "url": url, "count": 0}
        t = threading.Thread(target=run_job, args=(job_id, url, limit, max_workers), daemon=True)
        t.start()
        return self._send(202, {"job_id": job_id, "status": "running", "url": url})


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    if not os.environ.get("CRAWL_API_KEY"):
        print("CẢNH BÁO: chưa đặt CRAWL_API_KEY, server sẽ từ chối mọi request có auth.")
        print("Chạy: CRAWL_API_KEY=<key-rieng> python3 server.py --port 8000")
    print(f"Crawl Muse wrapper chạy tại http://{args.host}:{args.port} (repo: {REPO_ROOT})")
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()
