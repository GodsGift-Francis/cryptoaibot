"""Optional: sends a message to your phone via Telegram whenever the bot does something notable."""
import requests


def send_telegram_message(cfg, text: str) -> bool:
    if not cfg.TELEGRAM_BOT_TOKEN or not cfg.TELEGRAM_CHAT_ID:
        return False
    url = f"https://api.telegram.org/bot{cfg.TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, data={"chat_id": cfg.TELEGRAM_CHAT_ID, "text": text}, timeout=10)
        return resp.ok
    except Exception:
        return False
