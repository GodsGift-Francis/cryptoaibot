# Deployment (Ubuntu 24.04, single VPS)

Topology: nginx -> PHP-FPM (Laravel control plane) · systemd -> trading worker + reconciliation worker ·
SQLite (Laravel DB) + SQLite engine ledger (`/opt/crypto-bot/var/engine.sqlite3`). No Node.js.
MySQL/PostgreSQL can replace the Laravel SQLite DB via `DB_CONNECTION`; Redis is optional.

## 1. Base system
```bash
sudo bash deploy/install-ubuntu.sh          # nginx, php8.3, python3, cryptobot system user
# install Composer per getcomposer.org, verifying the installer checksum
```

## 2. Code
```bash
sudo mkdir -p /opt/crypto-bot && sudo chown $USER /opt/crypto-bot
# copy the project into /opt/crypto-bot (scp/rsync/unzip)
cd /opt/crypto-bot
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
mkdir -p var && sudo chown -R cryptobot:cryptobot var
```

## 3. Secrets (generate once, same token in both files)
```bash
python3 -c "import secrets;print(secrets.token_urlsafe(48))"   # -> CONTROL_PLANE_TOKEN / TRADING_BOT_INTERNAL_TOKEN
cp .env.example .env && chmod 600 .env && sudo chown cryptobot .env
cp laravel/.env.example laravel/.env && chmod 640 laravel/.env
```
Engine `.env`: `CONTROL_PLANE_URL=http://127.0.0.1:8080` (the localhost-only nginx server).

## 4. Laravel
```bash
cd /opt/crypto-bot/laravel
composer install --no-dev --optimize-autoloader
touch database/database.sqlite      # set DB_DATABASE to its absolute path in laravel/.env
php artisan key:generate
php artisan migrate --force
php artisan db:seed --force         # creates the bot instance, PAUSED
php artisan bot:create-user you@example.com --role=admin     # password prompted
php artisan config:cache && php artisan route:cache && php artisan view:cache
sudo chown -R www-data:www-data storage bootstrap/cache database
```
Scheduler (cron, as www-data):
```cron
* * * * * cd /opt/crypto-bot/laravel && php artisan schedule:run >> /dev/null 2>&1
```

## 5. nginx
Copy `deploy/nginx.conf.example` to `/etc/nginx/sites-available/crypto-bot`, set your domain and TLS
certificates, enable it, `sudo nginx -t && sudo systemctl reload nginx`.
The public server returns 404 for `/api/internal/*`; the engine API is served only on `127.0.0.1:8080`.

## 6. Workers
```bash
sudo cp deploy/crypto-bot-*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now crypto-bot-trading-worker
sudo systemctl enable --now crypto-bot-reconciliation-worker   # exits cleanly in PAPER mode
journalctl -u crypto-bot-trading-worker -f                        # JSON logs, secrets redacted
```

## 7. Telegram (optional)
Set `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_CHAT_IDS`, `TELEGRAM_WEBHOOK_SECRET` (>=16 chars) in
`laravel/.env`, then register `https://YOUR-DOMAIN/telegram/webhook/<secret>` with Telegram `setWebhook`.
Engine alerts use `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` in the engine `.env`.

## Mode progression
See `docs/RUNBOOK.md` -> "PAPER -> TESTNET" and "Before LIVE".
