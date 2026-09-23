"""
Turns indicator values + market regime + sentiment into a BUY / SELL / HOLD
signal, with a human-readable explanation of why. This is a rules-based
system, not machine learning - every decision can be traced back to a
specific, inspectable reason. That's deliberate: a bot you can't explain
is a bot you can't safely debug.
"""
from dataclasses import dataclass, field

import indicators


@dataclass
class Signal:
    action: str            # "BUY", "SELL", or "HOLD"
    score: float
    reasons: list = field(default_factory=list)


def _trend_score(row) -> tuple[float, str]:
    if row["ema_fast"] > row["ema_mid"] > row["ema_slow"]:
        return 2.0, "Bullish trend: EMA20 > EMA50 > EMA200"
    if row["ema_fast"] < row["ema_mid"] < row["ema_slow"]:
        return -2.0, "Bearish trend: EMA20 < EMA50 < EMA200"
    return 0.0, "No clear EMA trend stack"


def _momentum_score(row, cfg) -> tuple[float, str]:
    score = 0.0
    reason = f"RSI {row['rsi']:.1f}"
    if row["rsi"] < cfg.RSI_OVERSOLD:
        score += 1.0
        reason += " (oversold - potential bounce)"
    elif row["rsi"] > cfg.RSI_OVERBOUGHT:
        score -= 1.0
        reason += " (overbought - potential pullback)"

    if row["macd_hist"] > 0:
        score += 0.5
        reason += ", MACD histogram positive"
    else:
        score -= 0.5
        reason += ", MACD histogram negative"
    return score, reason


def _regime_score(symbol: str, market_data: dict | None) -> tuple[float, str]:
    if market_data is None or market_data.get("btc_dominance") is None:
        return 0.0, "Market-wide data unavailable"

    dom = market_data["btc_dominance"]
    is_btc = symbol.upper().startswith("BTC")

    if is_btc:
        return 0.0, f"Trading BTC directly - dominance ({dom:.1f}%) not used as a filter"

    # For altcoins: rising BTC dominance = risk-off for alts, falling = supportive of alts.
    if dom > 55:
        return -1.0, f"BTC dominance high ({dom:.1f}%) - unfavorable backdrop for altcoins"
    return 1.0, f"BTC dominance moderate/low ({dom:.1f}%) - more room for altcoins"


def _sentiment_score(sentiment: dict | None) -> tuple[float, str]:
    if sentiment is None or sentiment.get("count", 0) == 0:
        return 0.0, "No recent news sentiment available"
    s = sentiment["score"]
    reason = f"News sentiment {sentiment['label']} ({s:+.2f} avg over {sentiment['count']} headlines)"
    return s * 1.5, reason  # sentiment tilts confidence, doesn't dominate the decision


def analyze(df, symbol: str, cfg, market_data: dict | None = None, sentiment: dict | None = None) -> Signal:
    """
    df must already have indicators added (see indicators.add_all_indicators).
    Uses only the latest row - pass a truncated df when backtesting to avoid lookahead bias.
    """
    row = df.iloc[-1]
    reasons = []
    total = 0.0

    for score, reason in (
        _trend_score(row),
        _momentum_score(row, cfg),
        _regime_score(symbol, market_data),
        _sentiment_score(sentiment),
    ):
        total += score
        reasons.append(reason)

    if total >= cfg.SCORE_BUY_THRESHOLD:
        action = "BUY"
    elif total <= cfg.SCORE_SELL_THRESHOLD:
        action = "SELL"
    else:
        action = "HOLD"

    return Signal(action=action, score=round(total, 2), reasons=reasons)
