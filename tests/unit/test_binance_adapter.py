from decimal import Decimal as D

from engine.domain.enums import OrderStatus
from engine.exchanges.binance import normalizer as nz
from engine.exchanges.binance.signer import sign_ws_params

SYM = nz.SymbolMap(["BTC/USDT"])


def test_ws_signature_matches_binance_documented_vector():
    params = {"symbol": "BTCUSDT", "side": "SELL", "type": "LIMIT", "timeInForce": "GTC", "quantity": "0.01000000",
              "price": "52000.00", "recvWindow": 100, "timestamp": 1645423376532,
              "apiKey": "vmPUZE6mv9SD5VNHk4HlWFsOr6aKE2zvsw0MuIgwCIPy6utIco14y7Ju91duEh8A"}
    sig = sign_ws_params(params, "NhqPtmdSJYdKjVHjA7PZj4Mge3R5YNiP1e3UZjInClVN65XAbvqqM6A7H5fATj0j")
    assert sig == "aa1b5712c094bc4e57c05a1a5c1fd8d88dcd628338ea863fec7b88e59fe2db24"


def _er(**kw):
    ev = {"e": "executionReport", "E": 1, "s": "BTCUSDT", "c": "cb1-abc", "S": "BUY", "o": "MARKET", "q": "1",
          "p": "0", "P": "0", "C": "", "x": "TRADE", "X": "PARTIALLY_FILLED", "r": "NONE", "i": 42, "z": "0.4",
          "Z": "40", "T": 2, "I": 9, "t": 77, "l": "0.4", "L": "100", "n": "0.0004", "N": "BTC", "Y": "40"}
    ev.update(kw)
    return {"subscriptionId": 0, "event": ev}


def test_execution_report_trade_yields_report_and_fill():
    _, ev = nz.unwrap(_er())
    n = nz.stream_event(ev, "main", SYM)
    assert n.kind == "ORDER" and n.report.status == OrderStatus.PARTIALLY_FILLED
    assert n.report.symbol == "BTC/USDT" and n.fill.quantity == D("0.4") and n.fill.commission_asset == "BTC"
    assert n.fill.event_key == "binance:main:BTCUSDT:42:77:TRADE"


def test_cancel_event_uses_original_client_order_id():
    _, ev = nz.unwrap(_er(x="CANCELED", X="CANCELED", c="cancel-req", C="cb1-abc", t=-1))
    n = nz.stream_event(ev, "main", SYM)
    assert n.report.client_order_id == "cb1-abc" and n.fill is None


def test_rest_and_ws_fill_keys_are_identical_for_deduplication():
    rest = nz.rest_trade_fill({"symbol": "BTCUSDT", "id": 77, "orderId": 42, "price": "100", "qty": "0.4",
                               "quoteQty": "40", "commission": "0.0004", "commissionAsset": "BTC", "time": 2,
                               "isBuyer": True}, "main", SYM)
    _, ev = nz.unwrap(_er())
    assert rest.event_key == nz.stream_event(ev, "main", SYM).fill.event_key


def test_expired_in_match_maps_to_expired_and_unknown_status_raises():
    assert nz.map_status("EXPIRED_IN_MATCH") == OrderStatus.EXPIRED
    import pytest
    with pytest.raises(nz.UnknownExchangeStatus):
        nz.map_status("SOMETHING_NEW")
