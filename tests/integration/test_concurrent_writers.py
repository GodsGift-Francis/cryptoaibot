"""Trading worker (REST response) and reconciliation worker (WS events) write the same order concurrently.
Writers must serialize (BEGIN IMMEDIATE + read inside the transaction): no ConcurrentModification,
no lost update, no double-counted fill."""
import threading
from decimal import Decimal as D

from engine.domain.enums import OrderStatus as S
from engine.exchanges.binance import normalizer as nz
from tests.integration.test_binance_lifecycle import entry, only_order

SYM = nz.SymbolMap(["BTC/USDT"])


def test_concurrent_report_and_fill_ingestion_serializes(testnet):
    entry(testnet, "new")
    o = only_order(testnet)
    msgs = [testnet.rest.execute(o.client_order_id, D("1")) for _ in range(33)]
    msgs.append(testnet.rest.execute(o.client_order_id, D("0.33333")))
    events = [nz.stream_event(nz.unwrap(m)[1], "test", SYM) for m in msgs]
    errors, barrier = [], threading.Barrier(4)

    def ws_worker(batch):          # reconciliation worker path: StreamIngestionService
        barrier.wait()
        try:
            for ev in batch:
                testnet.s.ingestion.handle(ev)          # each thread = its own SQLite connection
        except Exception as exc:  # noqa: BLE001
            errors.append(repr(exc))

    def rest_worker(batch):        # trading worker path: OrderService.apply_report + ingest_fill, no outer tx
        barrier.wait()
        try:
            for ev in batch:
                testnet.s.orders.apply_report(ev.report, source="rest_submit")
                testnet.s.orders.ingest_fill(ev.fill, source="rest_submit")
        except Exception as exc:  # noqa: BLE001
            errors.append(repr(exc))

    # both real code paths, each seeing every event, in forward and reverse order
    threads = [threading.Thread(target=fn, args=(events[::-1] if i % 2 else events,))
               for i, fn in enumerate((ws_worker, rest_worker, ws_worker, rest_worker))]
    [t.start() for t in threads]
    [t.join(30) for t in threads]
    assert not errors, errors
    o = only_order(testnet)
    assert o.status == S.FILLED and o.executed_quantity == D("33.33333")
    assert len(testnet.store.fills_for_order(o.id)) == 34
    assert testnet.s.positions.get("BTC/USDT").quantity == D("33.33333") * D("0.999")


def test_two_processes_style_stores_serialize_via_sqlite_lock(tmp_path):
    """Separate Store instances (no shared Python lock) = the real two-process setup."""
    from tests.conftest import Harness
    trading = Harness(tmp_path, "TESTNET")
    recon = Harness(tmp_path, "TESTNET", clock=trading.clock, rest=trading.rest)   # same DB file, own Store
    entry(trading, "new")
    o = only_order(trading)
    msgs = [trading.rest.execute(o.client_order_id, D("1")) for _ in range(33)]
    msgs.append(trading.rest.execute(o.client_order_id, D("0.33333")))
    events = [nz.stream_event(nz.unwrap(m)[1], "test", SYM) for m in msgs]
    errors, barrier = [], threading.Barrier(2)

    def run(fn):
        barrier.wait()
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            errors.append(repr(exc))

    def ws():
        for ev in events:
            recon.s.ingestion.handle(ev)

    def rest():
        for ev in reversed(events):
            trading.s.orders.apply_report(ev.report, source="rest_submit")
            trading.s.orders.ingest_fill(ev.fill, source="rest_submit")

    threads = [threading.Thread(target=run, args=(f,)) for f in (ws, rest)]
    [t.start() for t in threads]
    [t.join(60) for t in threads]
    assert not errors, errors
    o = only_order(trading)
    assert o.status == S.FILLED and o.executed_quantity == D("33.33333")
    assert len(trading.store.fills_for_order(o.id)) == 34
    assert recon.s.positions.get("BTC/USDT").quantity == D("33.33333") * D("0.999")
