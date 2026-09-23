"""
Scores news headlines from -1 (very negative) to +1 (very positive).

Uses VADER (a free, offline sentiment model - no API key, no internet call)
when it's installed. Falls back to a small hand-built crypto keyword lexicon
so the bot still runs without the extra dependency, just less precisely.
"""
try:
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
    _analyzer = SentimentIntensityAnalyzer()
except ImportError:
    _analyzer = None

_POSITIVE_WORDS = {
    "surge", "rally", "bullish", "soar", "gain", "gains", "breakout", "record",
    "inflow", "inflows", "adoption", "approval", "approved", "upgrade", "partnership",
    "recover", "recovery", "rebound", "buy", "accumulate",
}
_NEGATIVE_WORDS = {
    "crash", "plunge", "bearish", "selloff", "sell-off", "hack", "hacked", "exploit",
    "ban", "banned", "lawsuit", "fraud", "collapse", "outflow", "outflows",
    "liquidation", "liquidated", "warning", "decline", "fear", "regulation", "crackdown",
}


def _fallback_score(text: str) -> float:
    words = text.lower().replace(",", " ").replace(".", " ").split()
    pos = sum(1 for w in words if w in _POSITIVE_WORDS)
    neg = sum(1 for w in words if w in _NEGATIVE_WORDS)
    if pos + neg == 0:
        return 0.0
    return (pos - neg) / (pos + neg)


def score_text(text: str) -> float:
    """Returns a single headline's sentiment score in [-1, 1]."""
    if _analyzer is not None:
        return _analyzer.polarity_scores(text)["compound"]
    return _fallback_score(text)


def score_headlines(headlines: list[dict]) -> dict:
    """
    Aggregates sentiment across a list of headline dicts (see data_fetcher.get_news_headlines).
    Returns overall score, label, and the per-headline breakdown for display in the UI.
    """
    if not headlines:
        return {"score": 0.0, "label": "neutral", "count": 0, "breakdown": []}

    breakdown = []
    total = 0.0
    for h in headlines:
        s = score_text(h["title"])
        total += s
        breakdown.append({**h, "score": round(s, 3)})

    avg = total / len(headlines)
    if avg > 0.15:
        label = "positive"
    elif avg < -0.15:
        label = "negative"
    else:
        label = "neutral"

    breakdown.sort(key=lambda x: x["score"])
    return {"score": round(avg, 3), "label": label, "count": len(headlines), "breakdown": breakdown}
