# Deploy trên VPS WPTangToc + OpenLiteSpeed

VPS dùng **OpenLiteSpeed** (WPTangToc) — **không** dùng Nginx / `certbot --nginx`.

Domain: **crawl.egeadcompany.com** · App Streamlit: **127.0.0.1:8501**

## Lỗi thường gặp (đúng như screenshot)

| Lệnh sai | Lý do |
|----------|--------|
| `cp deploy/nginx-...` trong thư mục `wptangtoc-ols` | File nằm trong repo **crawl**, không phải thư mục WPTangToc |
| `certbot --nginx` | Server là **LiteSpeed**, không cài plugin Nginx |

## 1. Chạy app Streamlit trên VPS

```bash
ssh root@64.225.56.106

cd ~
git clone https://github.com/hanhan257/crawl.git
cd crawl

# VPS 1 GB: native (nhẹ)
sudo bash deploy/setup-swap.sh
sudo bash deploy/install-native-almalinux.sh

# Hoặc Docker (≥ 2 GB):
# sudo bash deploy/install-vps-almalinux.sh
# cp config.example.json config.json
# docker compose up -d --build
```

Kiểm tra app local:

```bash
curl -I http://127.0.0.1:8501
```

## 2. Tạo domain trong WPTangToc

1. Vào menu WPTangToc → thêm website **crawl.egeadcompany.com**
2. DNS Cloudflare `A crawl` → `64.225.56.106`
3. Cài SSL Let's Encrypt **trong WPTangToc** (không dùng certbot nginx)

## 3. Reverse proxy LiteSpeed → :8501

Chi tiết: `deploy/openlitespeed-crawl.egeadcompany.com.txt`

Tóm tắt WebAdmin OLS (`https://IP:7080` hoặc theo WPTangToc):

1. **External App** (Proxy) → `http://127.0.0.1:8501`
2. **Context /** → Proxy tới External App đó
3. **WebSocket Proxy /** → `127.0.0.1:8501` (bắt buộc cho Streamlit)
4. Graceful Restart

## 4. Mở port (nếu cần)

```bash
firewall-cmd --permanent --add-service=http
firewall-cmd --permanent --add-service=https
firewall-cmd --reload
# Không cần mở 8501 ra ngoài nếu đã proxy qua OLS
```

## 5. Cập nhật code

```bash
cd ~/crawl
git pull
.venv/bin/pip install -r requirements.txt
systemctl restart crawl-feed-gmc
```

**Shop Cloudflare (vd. us.wdycraftlab.com):** cần `curl_cffi` trong requirements — nếu lỗi `pip: command not found` thì dùng `.venv/bin/pip`, không gõ `pip` trực tiếp:

```bash
cd ~/crawl
.venv/bin/pip install "curl_cffi>=0.7.0"
.venv/bin/python -c "import curl_cffi; print('OK')"
systemctl restart crawl-feed-gmc
```

Mở: **https://crawl.egeadcompany.com**
