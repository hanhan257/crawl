#!/bin/bash
# Cập nhật app trên VPS sau git push (gọi từ GitHub Actions hoặc thủ công).
# Chạy từ thư mục repo: bash deploy/update-web.sh
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$APP_DIR"

BRANCH="${DEPLOY_BRANCH:-main}"
REMOTE="${DEPLOY_REMOTE:-origin}"

echo "[deploy] dir=$APP_DIR branch=$BRANCH"

if [ ! -d .git ]; then
  echo "[deploy] Không phải git repo: $APP_DIR" >&2
  exit 1
fi

git fetch "$REMOTE" "$BRANCH"
git checkout "$BRANCH"
git reset --hard "$REMOTE/$BRANCH"

if [ -x .venv/bin/pip ]; then
  .venv/bin/pip install -r requirements.txt -q
elif [ -x .venv/bin/pip3 ]; then
  .venv/bin/pip3 install -r requirements.txt -q
else
  echo "[deploy] Cảnh báo: chưa có .venv — bỏ qua pip install"
fi

if systemctl list-unit-files | grep -q '^crawl-feed-gmc\.service'; then
  systemctl restart crawl-feed-gmc
  systemctl is-active crawl-feed-gmc
else
  echo "[deploy] Không thấy service crawl-feed-gmc — bỏ qua restart"
fi

echo "[deploy] OK → $(git rev-parse --short HEAD)"
