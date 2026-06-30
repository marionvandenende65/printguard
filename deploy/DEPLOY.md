# PrintGuard — Deploy-runbook (Hetzner VPS)

Doel: PrintGuard/DesignGuard weer live krijgen op `www.printguardtool.com`,
weg van Railway, op een simpele eigen VPS. Vers starten (geen oude data).

## Wat Marion eenmalig doet
1. **Hetzner Cloud account** aanmaken op https://www.hetzner.com/cloud
   - Nieuw project → server toevoegen → **CX22** (2 vCPU / 4 GB / 40 GB), locatie Falkenstein of Nürnberg.
   - Image: **Ubuntu 24.04**.
   - SSH-key toevoegen (Claude kan een key genereren, of gebruik een bestaande uit de keynames-map).
   - Server aanmaken → noteer het **IP-adres**.
2. **Mailwachtwoord** van `noreply@printguardtool.com` uit SiteGround Site Tools paraat hebben.
3. **GitHub-token** (de oude `ghp_...` in `.git/config`) intrekken op GitHub.

## Wat Claude daarna doet (via SSH)
1. Code naar de server zetten (git clone of scp naar `/opt/printguard`).
2. `bash /opt/printguard/deploy/setup.sh` draaien — dit doet:
   - systeem updaten, Python venv, dependencies
   - Caddy installeren (automatische SSL)
   - `.env` genereren met willekeurige SECRET_KEY + ADMIN_TOKEN
   - systemd-service `printguard` starten (gunicorn op 127.0.0.1:5000)
   - firewall (22/80/443) + dagelijkse DB-backup
3. `MAIL_PASSWORD` invullen in `/opt/printguard/.env` en `systemctl restart printguard`.
4. Testen op het server-IP: home 200, login demo, /api/protect, contactmail.

## Cutover (samen, 1 klik — ONOMKEERBAAR maar laag risico)
De site ligt nu toch plat, dus er kan niets stuk.
1. **Cloudflare DNS**: A-record `www` en apex `@` → het Hetzner-IP (proxied = oranje wolk laten staan).
2. SSL-mode in Cloudflare op **Full (strict)**.
3. Caddy haalt automatisch het Let's Encrypt-certificaat zodra DNS klopt.
4. Verifiëren: `curl -I https://www.printguardtool.com` → 200.

## Na 1-2 weken stabiel draaien
- Railway-project opzeggen/verwijderen (bespaart kosten).

## Updates uitrollen (na go-live)
Op de server: `cd /opt/printguard && git pull && systemctl restart printguard`
(of een klein deploy-scriptje; Caddy/DB blijven staan).

## Belangrijke paden op de server
- Code: `/opt/printguard`
- Database: `/var/lib/printguard/printguard.db`
- Config: `/opt/printguard/.env` (rechten 600)
- Backups: `/var/backups/printguard/` (14 dagen)
- Service: `systemctl status printguard` · logs: `journalctl -u printguard -f`
- Caddy: `/etc/caddy/Caddyfile` · `systemctl reload caddy`

## SMTP-let op
Hetzner blokkeert uitgaande mailpoorten soms voor nieuwe accounts. Werkt mail niet:
test poort 587 i.p.v. 465, of vraag deblokkering aan via Hetzner-support.
