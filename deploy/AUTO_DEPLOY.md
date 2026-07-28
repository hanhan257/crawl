# Tự deploy web khi git push (GitHub Actions → VPS)

Push lên nhánh **main** → Actions SSH vào VPS → `git pull` + restart Streamlit.

## 1. Trên VPS — git qua SSH (không cần dán PAT mỗi lần)

```bash
ssh root@206.189.82.178

# Tạo deploy key (chỉ đọc repo)
ssh-keygen -t ed25519 -C "crawl-vps" -f ~/.ssh/crawl_github -N ""
cat ~/.ssh/crawl_github.pub
```

GitHub → repo **crawl** → **Settings → Deploy keys → Add deploy key**  
- Title: `vps-crawl`  
- Key: dán nội dung `crawl_github.pub`  
- **Allow write access**: tắt  

Trên VPS:

```bash
cat >> ~/.ssh/config <<'EOF'
Host github.com
  HostName github.com
  User git
  IdentityFile ~/.ssh/crawl_github
  IdentitiesOnly yes
EOF
chmod 600 ~/.ssh/config ~/.ssh/crawl_github

cd ~/crawl
git remote set-url origin git@github.com:hanhan257/crawl.git
git fetch origin
bash deploy/update-web.sh
```

## 2. GitHub Secrets (Actions SSH vào VPS)

Tạo key riêng để Actions login VPS (khác deploy key ở trên):

**Trên máy Windows (PowerShell) hoặc VPS:**

```bash
ssh-keygen -t ed25519 -C "github-actions-crawl" -f crawl_actions -N ""
```

- File `crawl_actions.pub` → thêm vào VPS:

```bash
# trên VPS
mkdir -p ~/.ssh
echo 'NỘI_DUNG_crawl_actions.pub' >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
```

- File `crawl_actions` (private) → GitHub repo → **Settings → Secrets and variables → Actions → New repository secret**

| Secret | Giá trị |
|--------|---------|
| `VPS_HOST` | `206.189.82.178` |
| `VPS_USER` | `root` |
| `VPS_SSH_KEY` | toàn bộ nội dung file private `crawl_actions` (gồm dòng BEGIN/END) |
| `VPS_APP_DIR` | `/root/crawl` (tuỳ chọn, mặc định `/root/crawl`) |

## 3. Kiểm tra

1. Push bất kỳ commit lên `main` (hoặc Actions → **Deploy VPS** → Run workflow)
2. Tab **Actions** phải xanh
3. Mở https://crawl.egeadcompany.com (Ctrl+F5)

Cập nhật thủ công trên VPS:

```bash
cd ~/crawl && bash deploy/update-web.sh
```
