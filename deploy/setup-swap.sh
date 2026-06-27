#!/bin/bash
# Thêm swap 2 GB — nên chạy trên VPS 1 GB RAM trước khi deploy
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Chạy: sudo bash deploy/setup-swap.sh"
  exit 1
fi

if swapon --show | grep -q '/swapfile'; then
  echo "Swap đã có sẵn:"
  swapon --show
  exit 0
fi

fallocate -l 2G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048
chmod 600 /swapfile
mkswap /swapfile
swapon /swapfile
grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab

# Giảm swap aggressiveness — ưu tiên RAM trước
sysctl vm.swappiness=10
grep -q 'vm.swappiness' /etc/sysctl.conf || echo 'vm.swappiness=10' >> /etc/sysctl.conf

echo "Đã bật swap 2 GB:"
free -h
