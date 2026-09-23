"""One-shot local setup: creates both .env files, wires them together, prepares the Laravel database.

    python scripts/setup_local.py

Idempotent and safe: it never overwrites an existing .env unless you pass --force, and it never
prints secrets. PAPER mode needs no exchange keys, so nothing here can place a real order.
"""
from __future__ import annotations

import argparse
import os
import re
import secrets
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE_ENV = os.path.join(ROOT, ".env")
LARAVEL_DIR = os.path.join(ROOT, "laravel")
LARAVEL_ENV = os.path.join(LARAVEL_DIR, ".env")
SQLITE = os.path.join(LARAVEL_DIR, "database", "database.sqlite")


def set_key(text: str, key: str, value: str) -> str:
    """Replace `KEY=...` (even if commented out), or append it."""
    pattern = re.compile(rf"^#?\s*{re.escape(key)}=.*$", re.MULTILINE)
    line = f"{key}={value}"
    return pattern.sub(line, text, count=1) if pattern.search(text) else text.rstrip("\n") + f"\n{line}\n"


def read_key(path: str, key: str) -> str | None:
    if not os.path.exists(path):
        return None
    m = re.search(rf"^{re.escape(key)}=(.*)$", open(path, encoding="utf-8").read(), re.MULTILINE)
    return m.group(1).strip() if m else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="overwrite existing .env files (new token)")
    args = ap.parse_args()

    for src in (ENGINE_ENV + ".example", LARAVEL_ENV + ".example"):
        if not os.path.exists(src):
            print(f"ERROR: {src} missing - run this from the project folder.")
            return 1

    existing = read_key(ENGINE_ENV, "CONTROL_PLANE_TOKEN")
    if existing and not args.force and "CHANGE_THIS" not in existing:
        token, reused = existing, True
    else:
        token, reused = secrets.token_urlsafe(48), False

    # --- engine .env ---
    if os.path.exists(ENGINE_ENV) and not args.force:
        print(f"kept    {ENGINE_ENV} (already exists; --force to regenerate)")
        text = open(ENGINE_ENV, encoding="utf-8").read()
    else:
        text = open(ENGINE_ENV + ".example", encoding="utf-8").read()
        print(f"created {ENGINE_ENV}")
    text = set_key(text, "CONTROL_PLANE_TOKEN", token)
    text = set_key(text, "TRADING_MODE", "PAPER")
    text = set_key(text, "CONTROL_PLANE_URL", "http://127.0.0.1:8000")
    open(ENGINE_ENV, "w", encoding="utf-8").write(text)

    # --- laravel .env ---
    if os.path.exists(LARAVEL_ENV) and not args.force:
        print(f"kept    {LARAVEL_ENV} (already exists; --force to regenerate)")
        ltext = open(LARAVEL_ENV, encoding="utf-8").read()
    else:
        ltext = open(LARAVEL_ENV + ".example", encoding="utf-8").read()
        print(f"created {LARAVEL_ENV}")
    for key, value in (("TRADING_BOT_INTERNAL_TOKEN", token), ("APP_ENV", "local"), ("APP_DEBUG", "true"),
                       ("SESSION_SECURE_COOKIE", "false"), ("APP_URL", "http://127.0.0.1:8000"),
                       ("DB_CONNECTION", "sqlite"), ("DB_DATABASE", SQLITE.replace("\\", "/"))):
        ltext = set_key(ltext, key, value)
    open(LARAVEL_ENV, "w", encoding="utf-8").write(ltext)

    os.makedirs(os.path.dirname(SQLITE), exist_ok=True)
    if not os.path.exists(SQLITE):
        open(SQLITE, "a").close()
        print(f"created {SQLITE}")

    same = read_key(ENGINE_ENV, "CONTROL_PLANE_TOKEN") == read_key(LARAVEL_ENV, "TRADING_BOT_INTERNAL_TOKEN")
    print(f"\nShared token: {'reused existing' if reused else 'generated'}, "
          f"engine and Laravel {'MATCH' if same else 'DO NOT MATCH - rerun with --force'}")
    if not same:
        return 1

    php = shutil.which("php")
    composer = shutil.which("composer") or shutil.which("composer.bat")
    print(f"php:      {php or 'NOT FOUND - install PHP 8.3+ and add it to PATH'}")
    print(f"composer: {composer or 'NOT FOUND - install from getcomposer.org'}")

    print("\nNext, in the laravel folder:")
    print("  composer install")
    print("  php artisan key:generate")
    print("  php artisan migrate")
    print("  php artisan db:seed")
    print("  php artisan bot:create-user you@example.com --role=admin")
    print("  php artisan serve")
    print("\nThen in a second terminal, from the project folder (venv active):")
    print("  python -m workers.trading_worker")
    print("\nOpen http://127.0.0.1:8000, sign in, press Resume (new bots start paused).")
    print("Mode is PAPER: no exchange keys, no real orders.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
