"""
All external data pulls live here, so if you ever swap exchanges or data
sources you only need to touch this one file.

Every function degrades gracefully (returns None / empty list) if its
optional dependency isn't installed or a network call fails, so the rest of
the bot doesn't crash because one data source hiccupped.
"""
import pandas as pd
import requests

try:
    import ccxt
except ImportError:
    ccxt = None

try:
    import feedparser
except ImportError:
    feedparser = None


def get_exchange(cfg):
    """Builds a ccxt exchange object, wired for testnet/sandbox unless explicitly disabled."""
    if ccxt is None:
        raise RuntimeError("ccxt is not installed. Run: pip install ccxt")

    exchange_class = getattr(ccxt, cfg.EXCHANGE_ID)
    exchange = exchange_class({
        "apiKey": cfg.API_KEY,
        "secret": cfg.API_SECRET,
        "enableRateLimit": True,
    })
    if getattr(cfg, "TRADING_MODE", "PAPER") == "TESTNET" and hasattr(exchange, "set_sandbox_mode"):
        exchange.set_sandbox_mode(True)
    return exchange


def fetch_ohlcv(exchange, symbol: str, timeframe: str, limit: int = 300) -> pd.DataFrame:
    """Returns candles as a DataFrame: timestamp, open, high, low, close, volume.

    On Binance it uses the raw /klines endpoint, which also carries order-flow columns
    (quote_volume, trades, taker_base, taker_quote) that ccxt's fetch_ohlcv discards. Those extra
    columns are what the AI layer's order-flow features need; indicators ignore them.
    Any exchange or failure falls back to the plain 6-column fetch_ohlcv.
    """
    try:
        raw = exchange.publicGetKlines({"symbol": exchange.market_id(symbol),
                                        "interval": exchange.timeframes[timeframe], "limit": limit})
        df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume", "close_time",
                                        "quote_volume", "trades", "taker_base", "taker_quote", "ignore"])
        df = df[["timestamp", "open", "high", "low", "close", "volume", "quote_volume", "trades", "taker_base"]]
        for c in df.columns[1:]:
            df[c] = df[c].astype(float)
        df["timestamp"] = pd.to_datetime(df["timestamp"].astype("int64"), unit="ms")
        return df
    except Exception:  # noqa: BLE001 - not Binance, or the raw endpoint is unavailable
        raw = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        return df


def get_global_market_data() -> dict | None:
    """
    Free CoinGecko endpoint, no API key needed.
    Returns btc dominance % and total market cap, used as a market-regime filter.
    """
    try:
        resp = requests.get("https://api.coingecko.com/api/v3/global", timeout=10)
        resp.raise_for_status()
        data = resp.json()["data"]
        return {
            "btc_dominance": data["market_cap_percentage"].get("btc"),
            "eth_dominance": data["market_cap_percentage"].get("eth"),
            "total_market_cap_usd": data["total_market_cap"].get("usd"),
            "market_cap_change_24h_pct": data.get("market_cap_change_percentage_24h_usd"),
        }
    except Exception:
        return None


def get_news_headlines(cfg) -> list[dict]:
    """
    Pulls recent headlines from free public RSS feeds (no API key required).
    Returns a list of {"title": str, "published": str, "source": str}.
    """
    if feedparser is None:
        return []

    headlines = []
    for feed_url in cfg.NEWS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[: cfg.NEWS_HEADLINE_LIMIT]:
                headlines.append({
                    "title": entry.get("title", ""),
                    "published": entry.get("published", ""),
                    "source": feed.feed.get("title", feed_url),
                })
        except Exception:
            continue
    return headlines[: cfg.NEWS_HEADLINE_LIMIT]
