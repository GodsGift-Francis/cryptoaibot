# News, social and sentiment signals — what is possible and what is not

The research so far says price and order-flow data alone contain no exploitable edge in BTC at 1h/4h/1d.
The natural conclusion is the one Stephen drew: real trading decisions also depend on news, deals, and what
circulates in forums and social channels. That is very likely true. This document is about the difference
between *believing* it and *proving* it, and what can be done about each.

## Why news cannot simply be backtested

1. **Point-in-time data barely exists for free.** To test a news signal you need to know the exact moment a
   headline became visible. RSS feeds hand you only the most recent items, news sites silently edit and
   re-date articles, and social platforms restrict historical access. Reconstructing "what did the market
   know at 14:00 on 12 March 2024" is the hard part, and it is the part that decides whether a backtest is
   honest. Vendors sell this (RavenPack, Kaiko, Amberdata) at prices that only make sense for funds.
2. **An LLM cannot be used as the historian.** Asking a language model to judge 2024 headlines is
   contaminated: the model already knows how 2024 turned out. Any backtest built that way will look
   brilliant and mean nothing. This is why the AI layer here uses a tree model over numeric features,
   and why LLM-based judgement is restricted to forward testing.
3. **Speed.** By the time an ETF approval or an exchange hack reaches an RSS feed, market makers have
   already repriced. Retail news flow is usually a *confirmation* of a move, not a prediction of it.
4. **Sentiment scoring is weak.** The V1 sentiment factor uses VADER, a general-purpose English lexicon
   that knows nothing about crypto. "Bitcoin plunges to support, whales accumulate" is not obviously
   positive or negative to it, and it is not symbol-specific either.

None of this means news is useless. It means the honest order of operations is: **collect first, test later,
and never trust a backtest built from data you could not have had at the time.**

## What is already built for this

`Store.record_news()` archives every headline the engine sees, at the moment it sees it, with:

| column | meaning |
|---|---|
| `seen_at` | when the ENGINE saw it — the only timestamp a live bot could actually have acted on |
| `published_at` | what the feed claimed, which is not always truthful |
| `source`, `title` | the item itself |
| `sentiment` | VADER score, kept as a baseline to beat, not as the answer |
| `item_key` | dedupe key, so repeated cycles do not inflate the archive |

The trading worker writes to this every cycle, in PAPER mode too, and failure to archive never blocks
trading. Running the bot in PAPER therefore builds the dataset that this research needs, for free, starting
today. A few months of it is worth more than any amount of reconstructed history.

## Where news is most likely to pay off

Ordered by how plausible the edge is, not by how exciting it sounds:

1. **Risk-off filter (most plausible).** Not "buy on good news" but "stop holding during a credible
   systemic event" — an exchange insolvency, a stablecoin depeg, a major protocol exploit. These are rare,
   slow to fully price in, and asymmetric: the cost of sitting out a few false alarms is small, the benefit
   of avoiding one real collapse is large. This fits the drawdown-control product (`research/overlay.py`)
   far better than it fits a trade-timing signal.
2. **Regime / attention measures.** Aggregate levels — how much is being written, how unusual that volume
   is, how one-sided the tone — behave more like a slow indicator than a headline reaction, and can be
   measured from the archive above once enough of it exists.
3. **Scheduled events.** FOMC dates, options expiries, ETF decision deadlines, unlock schedules. These are
   known in advance, so there is no point-in-time problem at all. This is the cheapest genuinely testable
   news-like feature and the obvious first candidate.
4. **Per-headline trade signals (least plausible).** Fastest to be arbitraged, hardest to prove, most
   vulnerable to leakage in testing.

## How it would be validated

The same pipeline and the same gates, with two additions:

* Any news feature must be computed from `seen_at`, never from `published_at`, and never from a source that
  cannot prove when it was visible.
* An LLM layer would be judged only by forward test: run it in PAPER alongside the rules strategy, log its
  decisions, and compare after a fixed period agreed in advance. No backtest of an LLM is trustworthy.

## Honest summary

News is probably part of a real edge. It is also the single easiest place to fool yourself, because
contaminated news backtests look spectacular. The plan is therefore: archive from today, start with
scheduled events and a risk-off filter rather than per-headline signals, and treat any LLM judgement as a
forward-test-only question.
