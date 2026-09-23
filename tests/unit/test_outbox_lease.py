
from engine.application.outbox import OutboxDispatcher
from tests.conftest import FakeControlPlane, NullNotifier


def dispatcher(h, owner, cp=None, notifier=None):
    return OutboxDispatcher(h.store, cp or FakeControlPlane(), notifier or NullNotifier(), 50, h.s.log, owner=owner)


def test_only_one_worker_delivers(paper):
    paper.cycle("BUY")
    n1, n2 = NullNotifier(), NullNotifier()
    a, b = dispatcher(paper, "trading", notifier=n1), dispatcher(paper, "recon", notifier=n2)
    paper.store.enqueue("telegram", "tg:x", {"text": "alert"})
    assert a.flush() > 0
    assert b.flush() == 0                         # lease held by the trading worker
    assert n1.sent == ["alert"] and n2.sent == []


def test_other_worker_takes_over_after_lease_expiry(paper):
    a, b = dispatcher(paper, "trading"), dispatcher(paper, "recon")
    a.flush()                                     # trading worker takes the lease (nothing queued yet)
    paper.store.enqueue("telegram", "tg:y", {"text": "later"})
    assert b.flush() == 0
    paper.clock.advance(OutboxDispatcher.LEASE_TTL_SECONDS + 1)   # trading worker died
    assert b.flush() == 1
    assert a.flush() == 0                         # original holder does not steal it back while b is live


def test_lease_lost_mid_flush_stops_delivery(paper):
    cp = FakeControlPlane()
    a = dispatcher(paper, "trading", cp=cp)
    for i in range(3):
        paper.store.enqueue("events", f"event:test:{i}", {"event_type": "X", "severity": "INFO", "message": "m",
                                                         "payload": {}, "occurred_at": paper.clock.now()})
    original = cp.post

    def slow_post(kind, payload, request_id):
        original(kind, payload, request_id)
        paper.clock.advance(OutboxDispatcher.LEASE_TTL_SECONDS + 1)
        paper.store.acquire_lease(OutboxDispatcher.LEASE_NAME, "recon", OutboxDispatcher.LEASE_TTL_SECONDS)

    cp.post = slow_post
    assert a.flush() == 1                        # delivered one, then noticed the lease moved
    assert paper.store.outbox_backlog() == 2
