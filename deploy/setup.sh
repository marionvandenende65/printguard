#!/usr/bin/env bash
# PrintGuard — server bootstrap voor een verse Ubuntu 24.04 VPS (Hetzner CX22).
# Draai als root:  bash setup.sh
# Idempotent: opnieuw draaien is veilig.
set -euo pipefail

APP_USER="printguard"
APP_DIR="/opt/printguard"
DATA_DIR="/var/lib/printguard"
REPO="https://github.com/marionvandenende65/printguard.git"   # of via scp/rsync vullen
DOMAIN="www.printguardtool.com"
APEX="printguardtool.com"

echo "==> 1. Systeem bijwerken + pakketten"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get upgrade -y
apt-get install -y python3-venv python3-pip git sqlite3 ufw curl debian-keyring debian-archive-keyring apt-transport-https

echo "==> 2. Caddy (officiele repo) installeren"
if ! command -v caddy >/dev/null 2>&1; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
  apt-get update -y
  apt-get install -y caddy
fi

echo "==> 3. App-gebruiker + mappen"
id -u "$APP_USER" >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin "$APP_USER"
mkdir -p "$APP_DIR" "$DATA_DIR"

echo "==> 4. Code ophalen ($APP_DIR)"
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" pull
else
  # Als je liever via scp/rsync uploadt: sla deze clone over en kopieer de bestanden zelf naar $APP_DIR
  git clone "$REPO" "$APP_DIR" || echo "!! git clone faalde — upload de bestanden handmatig naar $APP_DIR"
fi

echo "==> 5. Virtualenv + dependencies"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --upgrade pip
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"

echo "==> 6. .env aanmaken (alleen als die nog niet bestaat)"
if [ ! -f "$APP_DIR/.env" ]; then
  SECRET_KEY="$(python3 -c 'import secrets;print(secrets.token_hex(32))')"
  ADMIN_TOKEN="$(python3 -c 'import secrets;print(secrets.token_hex(24))')"
  cat > "$APP_DIR/.env" <<EOF
SECRET_KEY=${SECRET_KEY}
ADMIN_TOKEN=${ADMIN_TOKEN}
DATABASE_PATH=${DATA_DIR}/printguard.db
SITE_URL=https://${DOMAIN}
MOLLIE_API_KEY=
MAIL_HOST=mail.printguardtool.com
MAIL_PORT=465
MAIL_USER=noreply@printguardtool.com
MAIL_PASSWORD=
MAIL_FROM=PrintGuard <noreply@printguardtool.com>
EOF
  echo "    .env gegenereerd met willekeurige SECRET_KEY + ADMIN_TOKEN."
  echo "    !! Vul nog handmatig in: MAIL_PASSWORD (en later MOLLIE_API_KEY)."
else
  echo "    .env bestaat al — overgeslagen."
fi

echo "==> 7. Rechten"
chown -R "$APP_USER:$APP_USER" "$APP_DIR" "$DATA_DIR"
chmod 600 "$APP_DIR/.env"

echo "==> 8. systemd service"
cp "$APP_DIR/deploy/printguard.service" /etc/systemd/system/printguard.service
systemctl daemon-reload
systemctl enable printguard
systemctl restart printguard

echo "==> 9. Caddy reverse proxy + auto-SSL"
cp "$APP_DIR/deploy/Caddyfile" /etc/caddy/Caddyfile
systemctl reload caddy

echo "==> 10. Firewall"
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

echo "==> 11. Backup-cron (dagelijks 04:00)"
cp "$APP_DIR/deploy/backup.sh" /usr/local/bin/printguard-backup.sh
chmod +x /usr/local/bin/printguard-backup.sh
( crontab -l 2>/dev/null | grep -v printguard-backup ; echo "0 4 * * * /usr/local/bin/printguard-backup.sh" ) | crontab -

echo ""
echo "==================================================================="
echo " KLAAR. Controleer met:"
echo "   systemctl status printguard"
echo "   curl -I http://127.0.0.1:5000/         (verwacht 200)"
echo "   journalctl -u printguard -n 50         (logs)"
echo " Zet daarna de DNS (Cloudflare) van ${DOMAIN} + ${APEX} naar dit server-IP."
echo " Caddy regelt automatisch het SSL-certificaat zodra DNS klopt."
echo "==================================================================="
