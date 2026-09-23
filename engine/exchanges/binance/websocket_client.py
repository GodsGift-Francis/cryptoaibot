"""Binance Spot User Data Stream over the WebSocket API.

Current mechanism (checked against binance-spot-api-docs, 2026-09):
  * connect to wss://ws-api.binance.com:443/ws-api/v3 (testnet: ws-api.testnet.binance.vision)
  * send `userDataStream.subscribe.signature` with {apiKey, timestamp, recvWindow, signature}
    (HMAC over the alphabetically sorted params; works with any API key type, no session.logon)
  * events arrive as {"subscriptionId": n, "event": {...}}
  * connections live at most 24h; `serverShutdown` and `eventStreamTerminated` require resubscription
The legacy listenKey stream on stream.binance.com is deprecated and is NOT used.

Liveness: user streams are silent when the account is idle, so a receive
timeout alone cannot detect a dead connection. We send the WS API `ping`
method every WS_PING_INTERVAL_SECONDS and treat a missing response within
WS_RESPONSE_TIMEOUT_SECONDS as a stale connection.

This client knows nothing about orders or positions. It hands raw event dicts
to `on_event` and connection state changes to `on_state`; the reconciliation
worker decides what they mean.
"""
from __future__ import annotations

import asyncio
import json
import random
import uuid
from typing import Awaitable, Callable

from engine.exchanges.binance.signer import signed_ws_params


class SubscriptionFailed(Exception):
    pass


class StaleConnection(Exception):
    pass


class BinanceUserDataStream:
    def __init__(self, url: str, api_key: str, api_secret: str, clock,
                 on_event: Callable[[dict], Awaitable[None]],
                 on_state: Callable[[str, str], Awaitable[None]],
                 connect=None, ping_interval: float = 30, response_timeout: float = 10,
                 max_backoff: float = 60, recv_window_ms: int = 5000, logger=None, sleep=asyncio.sleep):
        self.url = url
        self._key = api_key
        self._secret = api_secret
        self.clock = clock
        self.on_event = on_event
        self.on_state = on_state
        self._connect = connect or _default_connect
        self.ping_interval = ping_interval
        self.response_timeout = response_timeout
        self.max_backoff = max_backoff
        self.recv_window_ms = recv_window_ms
        self.log = logger
        self._sleep = sleep
        self._stop = asyncio.Event()
        self._pending: dict[str, asyncio.Future] = {}
        self.attempt = 0

    def stop(self) -> None:
        self._stop.set()

    def backoff_seconds(self, attempt: int) -> float:
        """Exponential backoff with full jitter, capped."""
        cap = min(self.max_backoff, 2 ** min(attempt, 10))
        return random.uniform(cap / 2, cap)

    async def run_forever(self) -> None:
        while not self._stop.is_set():
            await self.on_state("CONNECTING", f"attempt {self.attempt + 1}")
            try:
                await self._session()
                reason = "connection closed"
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - every failure leads to degraded + reconnect
                reason = f"{type(exc).__name__}: {str(exc)[:200]}"
            await self.on_state("DISCONNECTED", reason)
            if self._stop.is_set():
                break
            self.attempt += 1
            await self._sleep(self.backoff_seconds(self.attempt))

    async def _session(self) -> None:
        ws = await self._connect(self.url)
        reader = None
        try:
            reader = asyncio.create_task(self._reader(ws))
            sub = await self._request(ws, "userDataStream.subscribe.signature", signed_ws_params({
                "apiKey": self._key, "timestamp": self.clock.now_ms(), "recvWindow": self.recv_window_ms,
            }, self._secret))
            if sub.get("status") != 200:
                raise SubscriptionFailed(f"status {sub.get('status')}: {sub.get('error', {}).get('msg', '')}")
            self.attempt = 0
            await self.on_state("SUBSCRIBED", f"subscriptionId={sub.get('result', {}).get('subscriptionId')}")
            while not self._stop.is_set():
                done, _ = await asyncio.wait({reader}, timeout=self.ping_interval)
                if done:
                    reader.result()        # re-raise the reader's exception, if any
                    return
                pong = await self._request(ws, "ping", {})
                if pong.get("status") != 200:
                    raise StaleConnection(f"ping status {pong.get('status')}")
        finally:
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(StaleConnection("connection closed"))
            self._pending.clear()
            if reader and not reader.done():
                reader.cancel()
            try:
                await ws.close()
            except Exception:  # noqa: BLE001
                pass

    async def _request(self, ws, method: str, params: dict) -> dict:
        rid = uuid.uuid4().hex
        fut = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        await ws.send(json.dumps({"id": rid, "method": method, "params": params}))
        try:
            return await asyncio.wait_for(fut, timeout=self.response_timeout)
        except asyncio.TimeoutError as exc:
            raise StaleConnection(f"no response to {method} within {self.response_timeout}s") from exc
        finally:
            self._pending.pop(rid, None)

    async def _reader(self, ws) -> None:
        async for frame in ws:
            msg = json.loads(frame)
            if "id" in msg and msg.get("id") in self._pending:
                fut = self._pending[msg["id"]]
                if not fut.done():
                    fut.set_result(msg)
                continue
            event = msg.get("event") if isinstance(msg.get("event"), dict) else None
            if event is None and msg.get("e"):
                event = msg
            if event is None:
                continue
            if event.get("e") == "serverShutdown":
                raise StaleConnection("serverShutdown received")
            await self.on_event(msg)
            if event.get("e") == "eventStreamTerminated":
                raise StaleConnection("eventStreamTerminated")


async def _default_connect(url: str):
    import websockets
    # websockets answers the server's 20s ping frames automatically.
    return await websockets.connect(url, max_size=2 ** 22, open_timeout=15, close_timeout=5)
