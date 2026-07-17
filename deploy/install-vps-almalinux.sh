#!/bin/bash
# Cài Docker trên AlmaLinux 9 / RHEL 9 (chạy với sudo)
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Chạy: sudo bash deploy/install-vps-almalinux.sh"
  exit 1
fi

dnf -y update
dnf -y install dnf-plugins-core curl git firewalld

if ! command -v docker >/dev/null 2>&1; then
  dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
  dnf -y install docker-ce docker-ce-cli containerd.io docker-compose-plugin
fi

systemctl enable --now docker
systemctl enable --now firewalld

firewall-cmd --permanent --add-service=ssh
firewall-cmd --permanent --add-service=http
firewall-cmd --permanent --add-service=https
firewall-cmd --permanent --add-port=8501/tcp
firewall-cmd --reload

echo ""
echo "Docker (AlmaLinux) sẵn sàng. Tiếp theo:"
echo "  git clone https://github.com/hanhan257/crawl.git"
echo "  cd crawl"
echo "  cp config.example.json config.json"
echo "  docker compose up -d --build"
echo "  Rồi cấu hình Nginx + SSL cho crawl.egeadcompany.com"
