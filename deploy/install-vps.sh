#!/bin/bash
# Cài Docker trên Ubuntu 22.04/24.04 (chạy với quyền root hoặc sudo)
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Chạy: sudo bash deploy/install-vps.sh"
  exit 1
fi

apt-get update
apt-get install -y ca-certificates curl git ufw

if ! command -v docker >/dev/null 2>&1; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo \
    "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
    $(. /etc/os-release && echo "${VERSION_CODENAME:-$VERSION_ID}") stable" \
    | tee /etc/apt/sources.list.d/docker.list >/dev/null
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
fi

systemctl enable docker
systemctl start docker

ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

echo ""
echo "Docker đã sẵn sàng. Tiếp theo:"
echo "  git clone https://github.com/hanhan257/crawl.git"
echo "  cd crawl"
echo "  cp config.example.json config.json   # chỉnh proxy trong file này"
echo "  docker compose up -d --build"
echo "  Mở http://IP-VPS:8501"
