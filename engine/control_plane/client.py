"""Authenticated client for the Laravel internal API (/api/internal/v1).

Every request carries: bearer token, bot instance identity, request/correlation
ID (the outbox idempotency key for writes) and a timestamp. Any failure raises
ControlPlaneUnavailable; callers must FAIL CLOSED.
"""
from __future__ import annotations

import json
import time
import uuid

import requests

from engine.infrastructure.store import DecimalEncoder


class ControlPlaneUnavailable(Exception):
    pass


class ControlPlaneRejected(ControlPlaneUnavailable):
    """Permanent rejection (HTTP 422/409/400): retrying the same payload can never succeed."""


class ControlPlaneClient:
    def __init__(self, base_url: str, token: str, bot_instance_id: str, timeout: int = 10, session=None):
        self.base = base_url.rstrip("/") + f"/api/internal/v1/bots/{bot_instance_id}"
        self.token = token
        self.bot = bot_instance_id
        self.timeout = timeout
        self.http = session or requests.Session()

    def _headers(self, request_id: str) -> dict:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Bot-Instance": self.bot,
            "X-Request-Id": request_id,
            "X-Request-Timestamp": str(int(time.time())),
        }

    def _send(self, method: str, path: str, payload: dict | None, request_id: str | None) -> dict:
        rid = request_id or uuid.uuid4().hex
        body = None if payload is None else json.dumps({"bot_instance": self.bot, **payload}, cls=DecimalEncoder)
        try:
            r = self.http.request(method, f"{self.base}/{path}", data=body, headers=self._headers(rid), timeout=self.timeout)
        except requests.RequestException as exc:
            raise ControlPlaneUnavailable(f"{method} {path}: {type(exc).__name__}") from exc
        if r.status_code in (400, 409, 422):
            raise ControlPlaneRejected(f"{method} {path}: HTTP {r.status_code}: {r.text[:300]}")
        if r.status_code >= 400:
            raise ControlPlaneUnavailable(f"{method} {path}: HTTP {r.status_code}")
        try:
            return r.json()
        except ValueError as exc:
            raise ControlPlaneUnavailable(f"{method} {path}: invalid JSON") from exc

    def get_control(self) -> dict:
        data = self._send("GET", "control", None, None)
        if not isinstance(data.get("enabled"), bool) or not isinstance(data.get("emergency_stop"), bool):
            raise ControlPlaneUnavailable("control response missing enabled/emergency_stop booleans")
        return data

    def heartbeat(self, payload: dict) -> dict:
        return self._send("POST", "heartbeat", payload, None)

    def post(self, kind: str, payload: dict, request_id: str) -> dict:
        return self._send("POST", kind, payload, request_id)
