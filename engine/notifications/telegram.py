"""Telegram operator notifications. Delivered through the outbox (idempotent keys).

Raises on failure so the outbox retries; a missing configuration is a no-op.
"""
import requests


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str, timeout: int = 10):
        self.token = token
        self.chat_id = chat_id
        self.timeout = timeout

    def send(self, text: str) -> None:
        if not self.token or not self.chat_id:
            return
        r = requests.post(f"https://api.telegram.org/bot{self.token}/sendMessage",
                          data={"chat_id": self.chat_id, "text": text[:4000]}, timeout=self.timeout)
        if not r.ok:
            raise RuntimeError(f"telegram HTTP {r.status_code}")
