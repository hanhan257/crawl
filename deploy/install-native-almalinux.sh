#!/bin/bash
# Cài app KHÔNG Docker trên AlmaLinux 9 — tiết kiệm RAM
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$APP_DIR"

if [ "$(id -u)" -ne 0 ]; then
  echo "Chạy: sudo bash deploy/install-native-almalinux.sh"
  exit 1
fi

# AlmaLinux 9 mặc định Python 3.9 — app cần 3.10+ nên dùng 3.11
dnf -y install python3.11 python3.11-pip python3.11-devel git curl firewalld gcc

systemctl enable --now firewalld
firewall-cmd --permanent --add-service=ssh
firewall-cmd --permanent --add-port=8501/tcp
firewall-cmd --reload

if [ ! -f config.json ]; then
  cp config.example.json config.json
  echo "Đã tạo config.json — hãy nano config.json để thêm proxy."
fi

python3.11 -m venv .venv
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
echo "Log: journalctl -u crawl-feed-gmc -f"
echo "Restart: systemctl restart crawl-feed-gmc"
