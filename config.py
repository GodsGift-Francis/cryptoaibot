"""Canonical configuration (V1.1).

One configuration model. `TRADING_MODE` is the only mode switch
(PAPER | TESTNET | LIVE). The deprecated `USE_TESTNET` flag has been removed.

STRATEGY / RISK VALUES BELOW ARE UNCHANGED FROM V1. Do not edit them as part
of infrastructure work; see docs/ARCHITECTURE.md "Strategy preservation".
"""
import os

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


# ---------------------------------------------------------------- exchange / mode
EXCHANGE_ID = os.getenv("EXCHANGE_ID", "binance")
SYMBOL = os.getenv("TRADING_SYMBOL", "BTC/USDT")
TIMEFRAME = os.getenv("TRADING_TIMEFRAME", "1h")
API_KEY = os.getenv("EXCHANGE_API_KEY", "")
API_SECRET = os.getenv("EXCHANGE_API_SECRET", "")
BINANCE_ACCOUNT_LABEL = os.getenv("BINANCE_ACCOUNT_LABEL", "main")   # used in event keys; never the API key

TRADING_MODE = os.getenv("TRADING_MODE", "PAPER").strip().upper()
if TRADING_MODE not in {"PAPER", "TESTNET", "LIVE"}:
    raise ValueError("TRADING_MODE must be PAPER, TESTNET, or LIVE")
LIVE_TRADING_CONFIRMED = _bool("LIVE_TRADING_CONFIRMED", "false")
GLOBAL_TRADING_ENABLED = _bool("GLOBAL_TRADING_ENABLED", "true")

# ---------------------------------------------------------------- control plane
CONTROL_PLANE_URL = os.getenv("CONTROL_PLANE_URL", "http://127.0.0.1:8000")
CONTROL_PLANE_TOKEN = os.getenv("CONTROL_PLANE_TOKEN", "")
BOT_INSTANCE_ID = os.getenv("BOT_INSTANCE_ID", "default")
REQUEST_TIMEOUT_SECONDS = int(os.getenv("REQUEST_TIMEOUT_SECONDS", "10"))

# ---------------------------------------------------------------- notifications
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ---------------------------------------------------------------- risk (UNCHANGED V1 values)
STARTING_PAPER_BALANCE = float(os.getenv("STARTING_PAPER_BALANCE", "1000"))
RISK_PER_TRADE_PCT = float(os.getenv("RISK_PER_TRADE_PCT", "1.0"))
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "3.0"))
DAILY_LOSS_LIMIT_PCT = float(os.getenv("DAILY_LOSS_LIMIT_PCT", "5.0"))
MAX_OPEN_POSITIONS = int(os.getenv("MAX_OPEN_POSITIONS", "1"))

# ---------------------------------------------------------------- strategy (UNCHANGED V1 values)
STRATEGY_NAME = "multi_factor_ema_rsi_macd"
STRATEGY_VERSION = "1.0.0"
EMA_FAST = 20
EMA_MID = 50
EMA_SLOW = 200
RSI_PERIOD = 14
RSI_OVERSOLD = 35
RSI_OVERBOUGHT = 70
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
# Defaults are the V1 values. Tuned values come from research/out/REPORT.md (walk-forward), never by hand.
SCORE_BUY_THRESHOLD = float(os.getenv("SCORE_BUY_THRESHOLD", "2.5"))
SCORE_SELL_THRESHOLD = float(os.getenv("SCORE_SELL_THRESHOLD", "-2.5"))
# AI layer (research/ml.py). v1 = unchanged V1 strategy; veto / ml need a model that passed the ML gates + holdout.
STRATEGY_MODE = os.getenv("STRATEGY_MODE", "v1").strip().lower()
ML_MODEL_PATH = os.getenv("ML_MODEL_PATH", "")
ML_MODEL_SHA256 = os.getenv("ML_MODEL_SHA256", "").strip().lower()

if (SCORE_BUY_THRESHOLD, SCORE_SELL_THRESHOLD, STOP_LOSS_PCT) != (2.5, -2.5, 3.0):
    # Tag every signal/order made with non-V1 parameters so the dashboard can compare versions.
    STRATEGY_VERSION = f"{STRATEGY_VERSION}+b{SCORE_BUY_THRESHOLD:g}s{SCORE_SELL_THRESHOLD:g}st{STOP_LOSS_PCT:g}"

NEWS_FEEDS = [
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cointelegraph.com/rss",
]
NEWS_HEADLINE_LIMIT = 25

# Documented correctness fix (V1.1): evaluate the strategy on the last CLOSED
# candle, matching the backtester. V1 evaluated the still-forming candle, so
# live signals could repaint. Set to false to restore V1 behavior exactly.
SIGNAL_ON_CLOSED_CANDLES = _bool("SIGNAL_ON_CLOSED_CANDLES", "true")
CANDLE_HISTORY_LIMIT = int(os.getenv("CANDLE_HISTORY_LIMIT", "300"))
MARKET_DATA_MAX_AGE_CANDLES = float(os.getenv("MARKET_DATA_MAX_AGE_CANDLES", "2"))

# ---------------------------------------------------------------- execution
CLIENT_ORDER_ID_PREFIX = os.getenv("CLIENT_ORDER_ID_PREFIX", "cb1")
PAPER_FEE_RATE = float(os.getenv("PAPER_FEE_RATE", "0"))            # V1 paper mode charged no fees
# software = candle-low check submits a market exit (V1 behavior; PAPER/TESTNET only)
# exchange = resting STOP_LOSS_LIMIT on Binance + software check as secondary control
PROTECTIVE_STOP_MODE = os.getenv("PROTECTIVE_STOP_MODE", "software").strip().lower()
PROTECTIVE_STOP_LIMIT_OFFSET_PCT = float(os.getenv("PROTECTIVE_STOP_LIMIT_OFFSET_PCT", "0.5"))
AMBIGUOUS_ORDER_GRACE_SECONDS = int(os.getenv("AMBIGUOUS_ORDER_GRACE_SECONDS", "60"))

# ---------------------------------------------------------------- reconciliation / websocket
RECON_STATE_MAX_AGE_SECONDS = int(os.getenv("RECON_STATE_MAX_AGE_SECONDS", "90"))
RECONCILE_INTERVAL_SECONDS = int(os.getenv("RECONCILE_INTERVAL_SECONDS", "300"))
RECENT_FILLS_LOOKBACK_HOURS = int(os.getenv("RECENT_FILLS_LOOKBACK_HOURS", "48"))
WS_PING_INTERVAL_SECONDS = int(os.getenv("WS_PING_INTERVAL_SECONDS", "30"))
WS_RESPONSE_TIMEOUT_SECONDS = int(os.getenv("WS_RESPONSE_TIMEOUT_SECONDS", "10"))
WS_MAX_BACKOFF_SECONDS = int(os.getenv("WS_MAX_BACKOFF_SECONDS", "60"))
WS_RECV_WINDOW_MS = int(os.getenv("WS_RECV_WINDOW_MS", "5000"))
BINANCE_WS_API_URL = os.getenv(
    "BINANCE_WS_API_URL",
    "wss://ws-api.binance.com:443/ws-api/v3" if TRADING_MODE == "LIVE" else "wss://ws-api.testnet.binance.vision/ws-api/v3",
)

# ---------------------------------------------------------------- runtime
LOOP_INTERVAL_SECONDS = int(os.getenv("LOOP_INTERVAL_SECONDS", "900"))
HEARTBEAT_INTERVAL_SECONDS = int(os.getenv("HEARTBEAT_INTERVAL_SECONDS", "30"))
ENGINE_DB_PATH = os.getenv("ENGINE_DB_PATH", "var/engine.sqlite3")
OUTBOX_MAX_ATTEMPTS = int(os.getenv("OUTBOX_MAX_ATTEMPTS", "50"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FILE = os.getenv("LOG_FILE", "trades_log.csv")   # DEPRECATED V1 CSV log; only read by the Streamlit view


def validate_runtime(cfg=None) -> None:
    """Refuses unsafe configurations. Called by every worker at startup and every cycle."""
    import sys
    c = cfg if cfg is not None else sys.modules[__name__]
    if c.TRADING_MODE not in {"PAPER", "TESTNET", "LIVE"}:
        raise RuntimeError("TRADING_MODE must be PAPER, TESTNET, or LIVE")
    mode = getattr(c, "STRATEGY_MODE", "v1")
    if mode not in {"v1", "veto", "ml"}:
        raise RuntimeError("STRATEGY_MODE must be v1, veto, or ml")
    if mode != "v1":
        import re
        if not getattr(c, "ML_MODEL_PATH", "") or not os.path.isfile(c.ML_MODEL_PATH):
            raise RuntimeError(f"STRATEGY_MODE={mode} requires ML_MODEL_PATH pointing to the trained model file")
        if not re.fullmatch(r"[0-9a-f]{64}", getattr(c, "ML_MODEL_SHA256", "") or ""):
            raise RuntimeError(f"STRATEGY_MODE={mode} requires ML_MODEL_SHA256 (64 hex chars) from research/out/ML_REPORT.md")
    if c.TRADING_MODE == "LIVE" and not c.LIVE_TRADING_CONFIRMED:
        raise RuntimeError("LIVE mode requires LIVE_TRADING_CONFIRMED=true")
    if c.TRADING_MODE == "LIVE" and c.PROTECTIVE_STOP_MODE != "exchange":
        raise RuntimeError("LIVE mode requires PROTECTIVE_STOP_MODE=exchange (software stops alone are not a production control)")
    if c.TRADING_MODE != "LIVE" and "ws-api.binance.com" in c.BINANCE_WS_API_URL:
        raise RuntimeError("Non-LIVE mode must not point at the production WebSocket API")
    if c.TRADING_MODE in {"TESTNET", "LIVE"} and (not c.API_KEY or not c.API_SECRET):
        raise RuntimeError(f"{c.TRADING_MODE} mode requires exchange API credentials")
    if c.PROTECTIVE_STOP_MODE not in {"software", "exchange"}:
        raise RuntimeError("PROTECTIVE_STOP_MODE must be 'software' or 'exchange'")
    if c.PROTECTIVE_STOP_MODE == "exchange" and c.TRADING_MODE == "PAPER":
        raise RuntimeError("PROTECTIVE_STOP_MODE=exchange is not supported in PAPER mode")
    token = c.CONTROL_PLANE_TOKEN or ""
    if token == "CHANGE_THIS_LONG_RANDOM_SECRET" or len(token) < 32:
        raise RuntimeError("CONTROL_PLANE_TOKEN must be a random value of at least 32 characters")
