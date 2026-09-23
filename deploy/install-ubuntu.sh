#!/usr/bin/env bash
# Base packages for a fresh Ubuntu 24.04 VPS. Review before running as root.
# This script does NOT create secrets, start trading, or enable LIVE mode.
set -euo pipefail
apt-get update
apt-get install -y nginx php8.3-fpm php8.3-cli php8.3-curl php8.3-mbstring php8.3-xml \
  php8.3-sqlite3 php8.3-bcmath php8.3-intl python3 python3-venv unzip git
systemctl enable --now nginx php8.3-fpm
id cryptobot >/dev/null 2>&1 || useradd --system --home /opt/crypto-bot --shell /usr/sbin/nologin cryptobot
# Composer: install per https://getcomposer.org/download/ and VERIFY the installer checksum.
echo "Next: follow deploy/README.md"
