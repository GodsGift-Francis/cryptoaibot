"""Cross-language contract: every payload the engine emits must satisfy the Laravel Form Requests.

Laravel cannot run in every environment, so this parses the PHP validation rules and
checks real engine output (PAPER + TESTNET runs) against them.
"""
import os
import re
from decimal import Decimal as D

from engine import bootstrap
from tests.conftest import Harness

REQ_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "laravel", "app", "Http", "Requests", "Internal")
KIND_TO_REQUEST = {"signals": "SignalRequest", "orders": "OrderRequest", "fills": "FillRequest",
                   "positions": "PositionRequest", "reconciliation": "ReconciliationRequest", "events": "EventRequest",
                   "heartbeat": "HeartbeatRequest"}
MIDDLEWARE = open(os.path.join(REQ_DIR, "..", "..", "Middleware", "BotInternal.php")).read()
REQUEST_ID_RE = re.compile(re.search(r"preg_match\('/(.+?)/'", MIDDLEWARE).group(1))


def php_rules(name):
    src = open(os.path.join(REQ_DIR, f"{name}.php")).read()
    rules = {}
    for key, body in re.findall(r"'([a-z_.*]+)'\s*=>\s*(\[[^\]]*\]|self::[A-Z_]+)", src):
        rules[key] = body
    consts = dict(re.findall(r"const (\w+) = (\[[^\]]*\]|'[^']*')", open(os.path.join(REQ_DIR, "InternalRequest.php")).read()))
    for k, v in list(rules.items()):
        if v.startswith("self::"):
            rules[k] = consts[v[6:]]
        rules[k] = rules[k].replace("self::STATUS", consts["STATUS"])
    return rules


def check(kind, payload):
    rules = php_rules(KIND_TO_REQUEST[kind])
    body = {"bot_instance": "test-bot", **payload}
    errors = []
    for key, rule in rules.items():
        if "." in key:
            continue
        if ("'required'" in rule and body.get(key) in (None, "")) or ("'present'" in rule and key not in body):
            errors.append(f"{kind}: missing {key}")
        m = re.search(r"'in:([^']+)'", rule)
        if m and body.get(key) is not None and key != "bot_instance" and str(body[key]) not in m.group(1).split(","):
            errors.append(f"{kind}: {key}={body[key]!r} not in {m.group(1)}")
        if "'boolean'" in rule and key in body and not isinstance(body[key], bool):
            errors.append(f"{kind}: {key} must be boolean")
    unknown = set(payload) - set(rules)
    if unknown:
        errors.append(f"{kind}: engine sends fields Laravel does not validate: {sorted(unknown)}")
    return errors


def collect(h):
    h.s.outbox.flush()
    return list(h.cp.posts)


def exercise(tmp_path):
    p = Harness(tmp_path / "p", "PAPER")
    p.cycle("BUY"); p.market.low = D("90"); p.cycle("HOLD")
    t = Harness(tmp_path / "t", "TESTNET", PROTECTIVE_STOP_MODE="exchange")
    t.healthy(); t.rest.next_submit = "fill"; t.cycle("BUY")
    t.rest.next_submit = "reject"; t.market.price = D("120"); t.cycle("SELL")
    t.rest.orders[next(iter(t.rest.orders))]["status"] = "CANCELED"; t.healthy()     # HALTED path + critical events
    return p, t


def test_every_engine_payload_satisfies_laravel_rules(tmp_path):
    (tmp_path / "p").mkdir(); (tmp_path / "t").mkdir()
    p, t = exercise(tmp_path)
    posts = collect(p) + collect(t)
    kinds = {k for k, _, _ in posts}
    assert {"signals", "orders", "fills", "positions", "reconciliation", "events"} <= kinds
    import json
    from engine.infrastructure.store import DecimalEncoder
    errors = []
    for kind, payload, rid in posts:
        payload = json.loads(json.dumps(payload, cls=DecimalEncoder))
        errors += check(kind, payload)
        if not REQUEST_ID_RE.fullmatch(rid):
            errors.append(f"{kind}: request id {rid!r} rejected by BotInternal regex")
    for h in (p, t):
        hb = json.loads(json.dumps(bootstrap.health_snapshot(h.s, "trading"), cls=DecimalEncoder))
        errors += check("heartbeat", hb)
    assert not errors, "\n".join(sorted(set(errors)))
