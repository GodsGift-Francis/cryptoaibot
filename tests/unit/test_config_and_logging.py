import pytest

from engine.infrastructure import logging as slog
from tests.conftest import make_cfg


def test_paper_is_default_and_valid():
    import config
    assert config.TRADING_MODE in {"PAPER", "TESTNET", "LIVE"}
    assert not hasattr(config, "USE_TESTNET")
    make_cfg().validate_runtime()


def test_live_requires_explicit_confirmation():
    cfg = make_cfg(TRADING_MODE="LIVE", API_KEY="k", API_SECRET="s", LIVE_TRADING_CONFIRMED=False,
                   PROTECTIVE_STOP_MODE="exchange", BINANCE_WS_API_URL="wss://ws-api.binance.com:443/ws-api/v3")
    with pytest.raises(RuntimeError, match="LIVE_TRADING_CONFIRMED"):
        cfg.validate_runtime()


def test_live_requires_exchange_side_stops():
    cfg = make_cfg(TRADING_MODE="LIVE", API_KEY="k", API_SECRET="s", LIVE_TRADING_CONFIRMED=True,
                   BINANCE_WS_API_URL="wss://ws-api.binance.com:443/ws-api/v3")
    with pytest.raises(RuntimeError, match="PROTECTIVE_STOP_MODE"):
        cfg.validate_runtime()


def test_testnet_cannot_point_at_production_websocket():
    cfg = make_cfg(TRADING_MODE="TESTNET", API_KEY="k", API_SECRET="s",
                   BINANCE_WS_API_URL="wss://ws-api.binance.com:443/ws-api/v3")
    with pytest.raises(RuntimeError, match="production"):
        cfg.validate_runtime()


def test_weak_control_plane_token_rejected():
    with pytest.raises(RuntimeError, match="CONTROL_PLANE_TOKEN"):
        make_cfg(CONTROL_PLANE_TOKEN="CHANGE_THIS_LONG_RANDOM_SECRET").validate_runtime()


def test_secrets_are_redacted_from_logs():
    slog.register_secret("A_VERY_SECRET_VALUE_123")
    out = slog.redact({"api_key": "x", "nested": {"secret": "y"}, "msg": "url?signature=abc&k=A_VERY_SECRET_VALUE_123"})
    assert out["api_key"] == "***" and out["nested"]["secret"] == "***"
    assert "abc" not in out["msg"] and "A_VERY_SECRET_VALUE_123" not in out["msg"]
