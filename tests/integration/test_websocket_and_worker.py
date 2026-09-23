"""WebSocket user-data stream + reconciliation worker against a scripted fake Binance WS API."""
import asyncio
import json
from decimal import Decimal as D

from engine.domain.enums import OrderStatus as S, ReconciliationStatus as R
from engine.exchanges.binance.signer import sign_ws_params
from engine.exchanges.binance.websocket_client import BinanceUserDataStream
from workers.reconciliation_worker import ReconciliationWorker


class FakeWS:
    """Answers subscribe/ping like the Binance WS API; tests push events or kill the socket."""

    def __init__(self, secret, answer_pings=True, sub_status=200):
        self.secret, self.answer_pings, self.sub_status = secret, answer_pings, sub_status
        self.inbox: asyncio.Queue = asyncio.Queue()
        self.sent: list[dict] = []
        self.closed = False

    async def send(self, raw):
        msg = json.loads(raw)
        self.sent.append(msg)
        if msg["method"] == "userDataStream.subscribe.signature":
            p = msg["params"]
            assert p["signature"] == sign_ws_params(p, self.secret), "bad signature"
            assert set(p) == {"apiKey", "timestamp", "recvWindow", "signature"}
            body = {"id": msg["id"], "status": self.sub_status, "result": {"subscriptionId": 0}}
            if self.sub_status != 200:
                body = {"id": msg["id"], "status": self.sub_status, "error": {"code": -2015, "msg": "Invalid API-key"}}
            await self.inbox.put(json.dumps(body))
        elif msg["method"] == "ping" and self.answer_pings:
            await self.inbox.put(json.dumps({"id": msg["id"], "status": 200, "result": {}}))

    async def push(self, message: dict):
        await self.inbox.put(json.dumps(message))

    async def kill(self):
        await self.inbox.put(None)

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self.inbox.get()
        if item is None or self.closed:
            raise StopAsyncIteration
        return item

    async def close(self):
        self.closed = True


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


async def until(predicate, timeout=10.0, interval=0.01):
    """Wait for a condition instead of guessing a sleep: slow machines (Windows CI, laptops) must not flake."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        value = predicate()
        if value:
            return value
        await asyncio.sleep(interval)
    raise AssertionError(f"condition not met within {timeout}s")


def make_stream(clock, sockets, events, states, **kw):
    it = iter(sockets)

    async def connect(url):
        return next(it)

    async def on_event(m):
        events.append(m)

    async def on_state(s, d):
        states.append((s, d))

    async def no_sleep(_):
        await asyncio.sleep(0)

    return BinanceUserDataStream("wss://ws-api.testnet.binance.vision/ws-api/v3", "k" * 64, "s" * 64, clock,
                                 on_event, on_state, connect=connect, ping_interval=0.05, response_timeout=0.2,
                                 sleep=no_sleep, **kw)


def test_subscribes_with_valid_signature_and_delivers_wrapped_events(clock):
    events, states = [], []
    sock = FakeWS("s" * 64)

    async def scenario():
        stream = make_stream(clock, [sock], events, states)
        task = asyncio.create_task(stream.run_forever())
        await until(lambda: [s for s, _ in states] == ["CONNECTING", "SUBSCRIBED"])
        await sock.push({"subscriptionId": 0, "event": {"e": "outboundAccountPosition", "E": 1, "u": 1, "B": []}})
        await until(lambda: events)
        stream.stop()
        await sock.kill()
        await asyncio.wait_for(task, 1)

    run(scenario())
    assert [s for s, _ in states][:2] == ["CONNECTING", "SUBSCRIBED"]
    assert events and events[0]["event"]["e"] == "outboundAccountPosition"


def test_missing_ping_response_is_stale_and_triggers_reconnect(clock):
    events, states = [], []
    dead, alive = FakeWS("s" * 64, answer_pings=False), FakeWS("s" * 64)

    async def scenario():
        stream = make_stream(clock, [dead, alive], events, states)
        task = asyncio.create_task(stream.run_forever())
        await until(lambda: [s for s, _ in states][:4] == ["CONNECTING", "SUBSCRIBED", "DISCONNECTED", "CONNECTING"])
        stream.stop()
        await alive.kill()
        await asyncio.wait_for(task, 1)

    run(scenario())
    names = [s for s, _ in states]
    assert names[:4] == ["CONNECTING", "SUBSCRIBED", "DISCONNECTED", "CONNECTING"]
    assert "StaleConnection" in states[2][1]


def test_server_shutdown_forces_reconnect_and_subscription_failure_is_reported(clock):
    events, states = [], []
    first, rejected, good = FakeWS("s" * 64), FakeWS("s" * 64, sub_status=401), FakeWS("s" * 64)

    async def scenario():
        stream = make_stream(clock, [first, rejected, good], events, states)
        task = asyncio.create_task(stream.run_forever())
        await until(lambda: [s for s, _ in states] == ["CONNECTING", "SUBSCRIBED"])
        await first.push({"subscriptionId": 0, "event": {"e": "serverShutdown", "E": 1}})
        await until(lambda: [s for s, _ in states].count("SUBSCRIBED") == 2)
        stream.stop()
        await good.kill()
        await asyncio.wait_for(task, 1)

    run(scenario())
    details = [d for s, d in states if s == "DISCONNECTED"]
    assert "serverShutdown" in details[0] and "SubscriptionFailed" in details[1]
    assert [s for s, _ in states].count("SUBSCRIBED") == 2


def test_backoff_is_exponential_with_jitter_and_capped(clock):
    stream = make_stream(clock, [], [], [], max_backoff=60)
    for attempt, cap in ((1, 2), (3, 8), (10, 60)):
        for _ in range(20):
            assert cap / 2 <= stream.backoff_seconds(attempt) <= cap


def test_worker_state_machine_disconnect_reconnect_reconcile(testnet):
    """CONNECTING -> SUBSCRIBED -> RECONCILING -> HEALTHY; drop -> DEGRADED; fills while down are recovered."""
    testnet.healthy()
    testnet.rest.next_submit = "new"
    testnet.cycle("BUY")
    [o] = testnet.store.orders("test-bot")
    first, second = FakeWS("s" * 64), FakeWS("s" * 64)
    sockets = iter([first, second])

    def factory(on_event, on_state):
        async def connect(url):
            return next(sockets)

        async def no_sleep(_):
            await asyncio.sleep(0)
        return BinanceUserDataStream("wss://ws-api.testnet.binance.vision/ws-api/v3", "k" * 64, "s" * 64,
                                     testnet.clock, on_event, on_state, connect=connect, ping_interval=1.0,
                                     response_timeout=2.0, sleep=no_sleep)

    worker = ReconciliationWorker(testnet.s, stream_factory=factory)
    worker.TOUCH_SECONDS = 3600
    observed = {}

    async def scenario():
        task = asyncio.create_task(worker.run())
        observed["after_subscribe"] = await until(
            lambda: testnet.store.get_recon_state("test-bot").status is R.HEALTHY or None)
        await first.push(testnet.rest.execute(o.client_order_id, D("10")))       # live partial fill via WS
        observed["after_event"] = await until(
            lambda: testnet.store.get_order(o.client_order_id).status is S.PARTIALLY_FILLED or None)
        testnet.s.reconciliation.set_state(R.DEGRADED, "about to drop")          # marker we must see overwritten
        await first.kill()                                                       # disconnect ...
        testnet.rest.execute(o.client_order_id, D("23.33333"))                   # ... order completes while blind
        observed["after_reconnect"] = await until(
            lambda: testnet.store.get_recon_state("test-bot").status is R.HEALTHY or None)
        worker.stream.stop()
        await second.kill()
        await asyncio.wait_for(task, 2)

    run(scenario())
    assert observed["after_subscribe"] is True                                   # subscribe -> reconcile -> HEALTHY
    assert observed["after_event"] is True                                       # WS fill applied live
    assert observed["after_reconnect"] is True                                   # reconnect re-reconciled to HEALTHY
    order = testnet.store.get_order(o.client_order_id)
    assert order.status == S.FILLED and testnet.s.orders.fill_quantity(order) == D("33.33333")
    st = testnet.store.get_recon_state("test-bot")
    assert st.ws_connected is False                                               # stopped at the end


def test_worker_marks_degraded_on_disconnect(testnet):
    worker = ReconciliationWorker(testnet.s, stream_factory=lambda e, s: None)
    testnet.healthy()
    run(worker.on_state("DISCONNECTED", "network"))
    st = testnet.store.get_recon_state("test-bot")
    assert st.status == R.DEGRADED and not st.allows_entries(testnet.clock.now(), 90)
    assert st.allows_exits(testnet.clock.now(), 90)


def test_worker_disconnect_does_not_clear_halt(testnet):
    worker = ReconciliationWorker(testnet.s, stream_factory=lambda e, s: None)
    testnet.s.reconciliation.set_state(R.HALTED, "BALANCE_BELOW_POSITION")
    run(worker.on_state("DISCONNECTED", "network"))
    run(worker.on_state("CONNECTING", "retry"))
    assert testnet.store.get_recon_state("test-bot").status == R.HALTED
