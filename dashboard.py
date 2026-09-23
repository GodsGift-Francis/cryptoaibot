"""
DEPRECATED transition dashboard (V1.1). The Laravel control plane is the
operator dashboard. This Streamlit view is READ-ONLY analysis + backtesting:
the V1 "run one trading cycle" button was removed because trading must only
happen inside the gated trading worker.

Run with:  streamlit run dashboard.py
Needs real internet access (your machine, not a sandbox) since it calls the
exchange, CoinGecko, and news RSS feeds live.
"""
import os

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

import config
import data_fetcher
import indicators
import sentiment
import strategy
from backtester import run_backtest

st.set_page_config(page_title="Crypto Bot Dashboard", layout="wide")
st.title("Crypto Trading Bot")
st.caption("Rules-based, fully explainable. Paper trading by default - see the sidebar.")

# --- Sidebar -----------------------------------------------------------------
with st.sidebar:
    st.header("Settings")
    symbol = st.text_input("Symbol", value=config.SYMBOL)
    timeframe = st.selectbox("Timeframe", ["15m", "1h", "4h", "1d"],
                              index=["15m", "1h", "4h", "1d"].index(config.TIMEFRAME)
                              if config.TIMEFRAME in ["15m", "1h", "4h", "1d"] else 1)
    st.divider()
    st.metric("Mode", config.TRADING_MODE)
    if config.TRADING_MODE == "LIVE":
        st.warning("Engine is configured for LIVE trading. Use the Laravel dashboard for controls.")
    st.caption("Read-only. Trading runs only in workers/trading_worker.py.")


@st.cache_data(ttl=60)
def load_market_snapshot(symbol: str, timeframe: str):
    exchange = data_fetcher.get_exchange(config)
    df = data_fetcher.fetch_ohlcv(exchange, symbol, timeframe, limit=300)
    df = indicators.add_all_indicators(df, config)
    market_data = data_fetcher.get_global_market_data()
    headlines = data_fetcher.get_news_headlines(config)
    sent = sentiment.score_headlines(headlines)
    return df, market_data, sent


tab_live, tab_backtest, tab_log = st.tabs(["Live Analysis", "Backtest", "Trade Log"])

# --- Live Analysis tab ---------------------------------------------------------
with tab_live:
    try:
        df, market_data, sent = load_market_snapshot(symbol, timeframe)
    except Exception as e:
        st.error(f"Could not load live data: {e}")
        st.stop()

    signal = strategy.analyze(df, symbol, config, market_data=market_data, sentiment=sent)

    col_chart, col_signal = st.columns([3, 1])

    with col_chart:
        fig = make_subplots(rows=3, cols=1, shared_xaxes=True, row_heights=[0.6, 0.2, 0.2],
                             vertical_spacing=0.03, subplot_titles=("Price", "RSI", "MACD"))
        fig.add_trace(go.Candlestick(
            x=df["timestamp"], open=df["open"], high=df["high"], low=df["low"], close=df["close"],
            name="Price"), row=1, col=1)
        for col, name in [("ema_fast", "EMA 20"), ("ema_mid", "EMA 50"), ("ema_slow", "EMA 200")]:
            fig.add_trace(go.Scatter(x=df["timestamp"], y=df[col], name=name, line=dict(width=1)), row=1, col=1)

        fig.add_trace(go.Scatter(x=df["timestamp"], y=df["rsi"], name="RSI", line=dict(color="purple")), row=2, col=1)
        fig.add_hline(y=config.RSI_OVERBOUGHT, line_dash="dot", row=2, col=1)
        fig.add_hline(y=config.RSI_OVERSOLD, line_dash="dot", row=2, col=1)

        fig.add_trace(go.Bar(x=df["timestamp"], y=df["macd_hist"], name="MACD hist"), row=3, col=1)

        fig.update_layout(height=700, xaxis_rangeslider_visible=False, showlegend=True)
        st.plotly_chart(fig, use_container_width=True)

    with col_signal:
        color = {"BUY": "green", "SELL": "red", "HOLD": "gray"}[signal.action]
        st.markdown(f"### Signal: :{color}[{signal.action}]")
        st.metric("Composite score", signal.score)
        st.write("**Why:**")
        for r in signal.reasons:
            st.write(f"- {r}")

        st.divider()
        st.write("**Market regime**")
        if market_data:
            st.write(f"BTC dominance: {market_data['btc_dominance']:.1f}%")
            st.write(f"Total market cap: ${market_data['total_market_cap_usd']:,.0f}")
        else:
            st.write("Unavailable")

        st.divider()
        st.write("**News sentiment**")
        st.write(f"{sent['label'].capitalize()} ({sent['score']:+.2f}, {sent['count']} headlines)")
        with st.expander("Recent headlines"):
            for h in sent["breakdown"][:10]:
                st.write(f"`{h['score']:+.2f}`  {h['title']}")

# --- Backtest tab ---------------------------------------------------------------
with tab_backtest:
    st.write("Technical-only backtest (no historical sentiment/dominance data available for free).")
    n_candles = st.slider("Candles to backtest over", 300, 2000, 1000, step=100)
    if st.button("Run Backtest"):
        with st.spinner("Backtesting..."):
            try:
                exchange = data_fetcher.get_exchange(config)
                hist_df = data_fetcher.fetch_ohlcv(exchange, symbol, timeframe, limit=n_candles)
                result = run_backtest(hist_df, symbol, config)
                st.session_state["backtest_result"] = result
            except Exception as e:
                st.error(f"Backtest failed: {e}")

    if "backtest_result" in st.session_state:
        result = st.session_state["backtest_result"]
        stats = result["stats"]
        cols = st.columns(5)
        cols[0].metric("Total return", f"{stats.get('total_return_pct', 0):+.2f}%")
        cols[1].metric("Max drawdown", f"{stats.get('max_drawdown_pct', 0):.2f}%")
        cols[2].metric("Trades", stats.get("num_trades", 0))
        cols[3].metric("Win rate", f"{stats.get('win_rate_pct', 0):.1f}%")
        cols[4].metric("Sharpe (approx)", stats.get("sharpe_approx", 0))

        eq = result["equity_curve"]
        if not eq.empty:
            fig2 = go.Figure()
            fig2.add_trace(go.Scatter(x=eq["timestamp"], y=eq["equity"], name="Equity"))
            fig2.update_layout(height=350, title="Equity curve")
            st.plotly_chart(fig2, use_container_width=True)

        st.dataframe(result["trades"], use_container_width=True)

# --- Trade Log tab ----------------------------------------------------------------
with tab_log:
    if os.path.exists(config.LOG_FILE):
        log_df = pd.read_csv(config.LOG_FILE)
        st.dataframe(log_df, use_container_width=True)
        if "balance" in log_df.columns:
            fig3 = go.Figure()
            fig3.add_trace(go.Scatter(y=log_df["balance"], mode="lines+markers", name="Balance"))
            fig3.update_layout(height=300, title="Paper balance over time")
            st.plotly_chart(fig3, use_container_width=True)
    else:
        st.info("V1 CSV log not found. V1.1 records orders/fills in the engine ledger and the Laravel dashboard.")
