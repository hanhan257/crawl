# Deploy lên VPS (Docker)

App chạy **Streamlit** trên port **8501**.

| OS | Script |
|----|--------|
| Ubuntu 22.04 / 24.04 | `deploy/install-vps.sh` · `deploy/install-native.sh` |
| **AlmaLinux 9** / RHEL 9 | `deploy/install-vps-almalinux.sh` · `deploy/install-native-almalinux.sh` |
| **WPTangToc + OpenLiteSpeed** | Xem `deploy/WPTANGTOC_OLS.md` (không dùng Nginx) |

## Domain mẫu: crawl.egeadcompany.com (AlmaLinux 9)

**DNS:** bản ghi `A` của `crawl.egeadcompany.com` → IP VPS (vd. `64.225.56.106`).

```bash
ssh root@64.225.56.106

# 1) Clone + Docker
git clone https://github.com/hanhan257/crawl.git
cd crawl
sudo bash deploy/install-vps-almalinux.sh
cp config.example.json config.json
nano config.json   # proxy nếu cần

docker compose up -d --build

# 2) Nginx + HTTPS
sudo dnf -y install nginx certbot python3-certbot-nginx
sudo cp deploy/nginx-crawl.egeadcompany.com.conf /etc/nginx/conf.d/
sudo nginx -t && sudo systemctl enable --now nginx
sudo certbot --nginx -d crawl.egeadcompany.com
```

Mở: **https://crawl.egeadcompany.com**

Cập nhật code sau này:

```bash
cd ~/crawl
git pull
docker compose up -d --build
```

## VPS 1 vCPU / 1 GB RAM (gói Basic)

**Chạy được**, nhưng RAM hơi chật. Khuyến nghị:

| Việc | Gợi ý |
|------|--------|
| **Swap** | Bắt buộc: `sudo bash deploy/setup-swap.sh` (thêm 2 GB swap) |
| **Cách cài** | Ưu tiên **không Docker** → `sudo bash deploy/install-native.sh` (Ubuntu) hoặc `install-native-almalinux.sh` |
| **Giới hạn SP** | Test **10–50 SP**; tránh crawl hàng nghìn SP một lúc |
| **Docker** | Vẫn dùng được nếu đã bật swap, nhưng dễ chậm khi crawl nặng |
| **Nâng cấp** | Nếu crawl Redbubble nhiều → nên lên **2 GB RAM** |

### Quy trình nhanh (VPS 1 GB, Ubuntu 24.04)

```bash
ssh root@IP-VPS

git clone https://github.com/hanhan257/crawl.git
cd crawl

sudo bash deploy/setup-swap.sh          # swap 2 GB
cp config.example.json config.json
nano config.json                        # proxy Redbubble

sudo bash deploy/install-native.sh      # cài + chạy nền (systemd)
```

Mở: `http://IP-VPS:8501`

Log: `journalctl -u crawl-feed-gmc -f`

---

## VPS ≥ 2 GB RAM

Khuyến nghị dùng **Docker** (cách dưới đây).

## 1. Chuẩn bị VPS

- Mở port **8501** (test trực tiếp) hoặc **80/443** (dùng Nginx + domain).
- SSH vào VPS: `ssh root@IP-VPS`

## 2. Cài Docker (một lần)

**Ubuntu:**

```bash
sudo bash deploy/install-vps.sh
```

**AlmaLinux 9:**

```bash
sudo bash deploy/install-vps-almalinux.sh
```

Hoặc cài Docker thủ công theo [docs.docker.com](https://docs.docker.com/engine/install/).

## 3. Clone code & cấu hình

```bash
git clone https://github.com/hanhan257/crawl.git
cd crawl
cp config.example.json config.json
nano config.json   # dán proxy Redbubble (mỗi dòng 1 proxy)
```

**Không** commit `config.json` lên GitHub (đã có trong `.gitignore`).

## 4. Chạy app

```bash
docker compose up -d --build
docker compose logs -f    # xem log
```

Truy cập: `http://IP-VPS:8501`

Dừng / khởi động lại:

```bash
docker compose down
docker compose up -d
```

## 5. Domain + HTTPS (khuyến nghị)

### AlmaLinux + crawl.egeadcompany.com

```bash
sudo dnf -y install nginx certbot python3-certbot-nginx
sudo cp deploy/nginx-crawl.egeadcompany.com.conf /etc/nginx/conf.d/
sudo nginx -t && sudo systemctl enable --now nginx
sudo certbot --nginx -d crawl.egeadcompany.com
```

### Ubuntu (domain tùy chỉnh)

```bash
sudo apt install -y nginx certbot python3-certbot-nginx
sudo cp deploy/nginx-crawl.conf.example /etc/nginx/sites-available/crawl
sudo nano /etc/nginx/sites-available/crawl   # đổi your-domain.com
sudo ln -sf /etc/nginx/sites-available/crawl /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d your-domain.com
```

Sau đó vào `https://your-domain.com` (Nginx proxy tới `127.0.0.1:8501`).

`proxy_read_timeout 3600s` giúp crawl lâu không bị ngắt giữa chừng.

## 6. Cập nhật code sau khi sửa

```bash
cd ~/crawl
git pull
docker compose up -d --build
```

## 7. Bảo mật

- App **không có đăng nhập** — nên:
  - Chỉ mở port 8501 cho IP tin cậy, **hoặc**
  - Dùng Nginx + HTTPS + [basic auth](https://nginx.org/en/docs/http/ngx_http_auth_basic_module.html), **hoặc**
  - VPN / Cloudflare Access
- Proxy và API key chỉ để trong `config.json` trên VPS.

## 8. Biến môi trường (tùy chọn)

Thay vì `config.json`, có thể set trên VPS:

```bash
export CRAWL_PROXY="host:port:user:pass"
```

Thêm vào `docker-compose.yml` phần `environment` nếu cần.

## 9. Lỗi thường gặp

| Triệu chứng | Cách xử lý |
|-------------|------------|
| Không vào được `:8501` | AlmaLinux: `firewall-cmd --add-port=8501/tcp --permanent && firewall-cmd --reload` |
| Container thoát ngay | `docker compose logs crawl` |
| Redbubble crawl lỗi | Kiểm tra proxy trong `config.json` |
| Crawl lâu bị ngắt | Tăng `proxy_read_timeout` trong Nginx |
| Certbot lỗi DNS | Kiểm tra `A crawl.egeadcompany.com` → đúng IP VPS |
