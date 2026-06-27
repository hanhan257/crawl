#!/bin/bash
# Cài app KHÔNG dùng Docker — tiết kiệm RAM (~300 MB), phù hợp VPS 1 GB
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$APP_DIR"

if [ "$(id -u)" -ne 0 ]; then
  echo "Chạy: sudo bash deploy/install-native.sh"
  exit 1
fi

apt-get update
apt-get install -y python3 python3-venv python3-pip git ufw curl

ufw allow OpenSSH
ufw allow 8501/tcp
ufw --force enable

if [ ! -f config.json ]; then
  cp config.example.json config.json
  echo "Đã tạo config.json — hãy nano config.json để thêm proxy."
fi

python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

mkdir -p output

SERVICE=/etc/systemd/system/crawl-feed-gmc.service
cat > "$SERVICE" <<EOF
[Unit]
Description=Crawl Feed GMC (Streamlit)
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$APP_DIR
Environment=STREAMLIT_SERVER_HEADLESS=true
ExecStart=$APP_DIR/.venv/bin/streamlit run app.py --server.port=8501 --server.address=0.0.0.0 --server.headless=true --browser.gatherUsageStats=false --server.fileWatcherType=none
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable crawl-feed-gmc
systemctl restart crawl-feed-gmc

echo ""
echo "App chạy tại http://IP-VPS:8501"
echo "Xem log: journalctl -u crawl-feed-gmc -f"
echo "Khởi động lại: systemctl restart crawl-feed-gmc"
