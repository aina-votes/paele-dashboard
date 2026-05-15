#!/bin/bash
# One-shot installer for the Vote Paele dashboard data refresher.
# Run on the droplet (159.89.148.51) as root:
#   curl -sSL https://raw.githubusercontent.com/aina-votes/paele-dashboard/main/setup_droplet.sh | bash
set -euo pipefail

REPO_DIR=/root/paele-dashboard
LOG_DIR=/root/logs
ENV_SRC=/root/fireflys-path/.env

if [ ! -f "$ENV_SRC" ]; then
  echo "FATAL: $ENV_SRC not found. Aborting." >&2
  exit 1
fi

mkdir -p "$LOG_DIR"

if [ ! -d "$REPO_DIR/.git" ]; then
  rm -rf "$REPO_DIR"
  git clone https://github.com/aina-votes/paele-dashboard.git "$REPO_DIR"
fi

cd "$REPO_DIR"
ln -sf "$ENV_SRC" .env

python3 -m pip install --quiet requests python-dotenv

git config user.email "sampeck2550@gmail.com"
git config user.name  "Sam Peck"
TOKEN=$(grep '^GITHUB_TOKEN=' "$ENV_SRC" | cut -d= -f2-)
git remote set-url origin "https://aina-votes:${TOKEN}@github.com/aina-votes/paele-dashboard.git"

# Refresh script: fetch -> if progress.json changed, commit + push
cat > /root/paele-dashboard-refresh.sh <<'EOS'
#!/bin/bash
set -e
cd /root/paele-dashboard
python3 fetch_progress.py >> /root/logs/paele-dashboard.log 2>&1
if ! git diff --quiet progress.json 2>/dev/null; then
  git add progress.json
  git commit -m "auto: refresh progress.json" >> /root/logs/paele-dashboard.log 2>&1
  git push origin main >> /root/logs/paele-dashboard.log 2>&1
fi
EOS
chmod +x /root/paele-dashboard-refresh.sh

# Add cron entry (idempotent — strips any prior copy first)
(crontab -l 2>/dev/null | grep -v paele-dashboard-refresh; \
 echo "*/30 * * * * /root/paele-dashboard-refresh.sh") | crontab -

echo
echo "=== first run ==="
python3 fetch_progress.py

echo
echo "=== cron ==="
crontab -l | grep paele-dashboard || echo "  (no entry — bug!)"

echo
echo "DONE."
