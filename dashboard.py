import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd
import yfinance as yf
from streamlit_autorefresh import st_autorefresh

from algo import (
    CFG,
    compute_rsi,
    compute_macd,
    compute_ma_cross,
    generate_signal,
)

# --- Page Config ---
st.set_page_config(page_title="Groww Algo Dashboard", layout="wide")
st.title("Groww Intraday Index Options Algo")

# Auto-refresh every 1 seconds (1000 milliseconds)
st_autorefresh(interval=1000, limit=None, key="data_refresh")

# --- Sidebar ---
st.sidebar.header("Settings")

SYMBOL_MAP = {
    "NIFTY": "^NSEI",
    "BANKNIFTY": "^NSEBANK",
}

selected_underlying = st.sidebar.selectbox("Underlying", list(SYMBOL_MAP.keys()))
yf_symbol = SYMBOL_MAP[selected_underlying]

# --- Fetch data from Yahoo Finance (free, no API key needed) ---
st.subheader(f"Latest Signals & Chart for {selected_underlying}")

with st.spinner("Fetching data..."):
    try:
        ticker = yf.Ticker(yf_symbol)
        df = ticker.history(period="5d", interval="5m")

        if df.empty:
            st.error("No data returned. Market may be closed.")
            st.stop()

        # Normalize column names to lowercase
        df = df.reset_index()
        df.columns = [c.lower() for c in df.columns]
        # Yahoo Finance uses 'datetime' for intraday, 'date' for daily
        if "datetime" in df.columns:
            df = df.rename(columns={"datetime": "ts"})
        elif "date" in df.columns:
            df = df.rename(columns={"date": "ts"})
        else:
            # Fallback: use the first column as timestamp
            df = df.rename(columns={df.columns[0]: "ts"})

        df["ts"] = pd.to_datetime(df["ts"])

    except Exception as e:
        st.error(f"Failed to fetch data: {e}")
        st.stop()

# --- Compute Indicators ---
rsi = compute_rsi(df["close"], CFG.RSI_PERIOD)
macd_line, signal_line = compute_macd(df["close"], CFG.MACD_FAST, CFG.MACD_SLOW, CFG.MACD_SIGNAL)
ma_fast, ma_slow = compute_ma_cross(df["close"], CFG.MA_FAST, CFG.MA_SLOW)

df["rsi"] = rsi
df["macd_line"] = macd_line
df["signal_line"] = signal_line
df["macd_hist"] = macd_line - signal_line
df["ma_fast"] = ma_fast
df["ma_slow"] = ma_slow

# Vectorized signal computation for plotting
ma_bull_cross = (df["ma_fast"].shift(1) <= df["ma_slow"].shift(1)) & (df["ma_fast"] > df["ma_slow"])
ma_bear_cross = (df["ma_fast"].shift(1) >= df["ma_slow"].shift(1)) & (df["ma_fast"] < df["ma_slow"])

bullish = (df["rsi"].shift(1) < CFG.RSI_OVERSOLD) & (df["rsi"] >= CFG.RSI_OVERSOLD) & (df["macd_line"] > df["signal_line"]) & (df["ma_fast"] > df["ma_slow"]) & ma_bull_cross
bearish = (df["rsi"].shift(1) > CFG.RSI_OVERBOUGHT) & (df["rsi"] <= CFG.RSI_OVERBOUGHT) & (df["macd_line"] < df["signal_line"]) & (df["ma_fast"] < df["ma_slow"]) & ma_bear_cross

df["signal_bull"] = df["close"].where(bullish, None)
df["signal_bear"] = df["close"].where(bearish, None)

signal = generate_signal(df, CFG)

# --- Signal Badge ---
signal_colors = {"BUY_CE": "#00c853", "BUY_PE": "#ff1744", "NONE": "#78909c"}
signal_labels = {"BUY_CE": "🟢 BUY CE (Bullish)", "BUY_PE": "🔴 BUY PE (Bearish)", "NONE": "⚪ No Signal"}

col1, col2, col3 = st.columns(3)
with col1:
    st.metric("Last Price", f"₹{df['close'].iloc[-1]:,.2f}")
with col2:
    change = df['close'].iloc[-1] - df['close'].iloc[-2]
    change_pct = (change / df['close'].iloc[-2]) * 100
    st.metric("Change", f"₹{change:,.2f}", f"{change_pct:+.2f}%")
with col3:
    st.markdown(
        f"<div style='padding:12px;border-radius:8px;background:{signal_colors[signal]};color:white;text-align:center;font-size:18px;font-weight:bold;margin-top:8px'>"
        f"{signal_labels[signal]}</div>",
        unsafe_allow_html=True,
    )

st.divider()

# --- Charting ---
fig = make_subplots(
    rows=3, cols=1,
    shared_xaxes=True,
    vertical_spacing=0.04,
    row_heights=[0.55, 0.22, 0.23],
    subplot_titles=(
        f"{selected_underlying} — Price & Moving Averages",
        "MACD",
        "RSI",
    ),
)

# Row 1: Candlestick + MAs + Signals
fig.add_trace(
    go.Candlestick(
        x=df["ts"], open=df["open"], high=df["high"], low=df["low"], close=df["close"],
        name="Price",
        increasing_line_color="#26a69a", decreasing_line_color="#ef5350",
    ),
    row=1, col=1,
)
fig.add_trace(
    go.Scatter(x=df["ts"], y=df["ma_fast"], mode="lines", name=f"MA {CFG.MA_FAST}",
               line=dict(color="#42a5f5", width=1.5)),
    row=1, col=1,
)
fig.add_trace(
    go.Scatter(x=df["ts"], y=df["ma_slow"], mode="lines", name=f"MA {CFG.MA_SLOW}",
               line=dict(color="#ffa726", width=1.5)),
    row=1, col=1,
)

# Signal Markers
fig.add_trace(
    go.Scatter(x=df["ts"], y=df["signal_bull"], mode="markers", name="Buy CE",
               marker=dict(symbol="triangle-up", size=14, color="#00c853", line=dict(width=2, color="white"))),
    row=1, col=1,
)
fig.add_trace(
    go.Scatter(x=df["ts"], y=df["signal_bear"], mode="markers", name="Buy PE",
               marker=dict(symbol="triangle-down", size=14, color="#ff1744", line=dict(width=2, color="white"))),
    row=1, col=1,
)

# Row 2: MACD
fig.add_trace(
    go.Scatter(x=df["ts"], y=df["macd_line"], mode="lines", name="MACD",
               line=dict(color="#42a5f5", width=1.5)),
    row=2, col=1,
)
fig.add_trace(
    go.Scatter(x=df["ts"], y=df["signal_line"], mode="lines", name="Signal",
               line=dict(color="#ffa726", width=1.5)),
    row=2, col=1,
)
macd_hist_colors = ["#26a69a" if v >= 0 else "#ef5350" for v in df["macd_hist"]]
fig.add_trace(
    go.Bar(x=df["ts"], y=df["macd_hist"], name="Histogram",
           marker_color=macd_hist_colors, opacity=0.6),
    row=2, col=1,
)

# Row 3: RSI
fig.add_trace(
    go.Scatter(x=df["ts"], y=df["rsi"], mode="lines", name="RSI",
               line=dict(color="#ab47bc", width=1.5)),
    row=3, col=1,
)
fig.add_hline(y=CFG.RSI_OVERBOUGHT, line_dash="dash", line_color="#ef5350", annotation_text="Overbought", row=3, col=1)
fig.add_hline(y=CFG.RSI_OVERSOLD, line_dash="dash", line_color="#26a69a", annotation_text="Oversold", row=3, col=1)
fig.add_hrect(y0=CFG.RSI_OVERSOLD, y1=CFG.RSI_OVERBOUGHT, fillcolor="rgba(171,71,188,0.08)", line_width=0, row=3, col=1)

# Layout
fig.update_layout(
    height=850,
    template="plotly_dark",
    xaxis_rangeslider_visible=False,
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    margin=dict(l=60, r=20, t=60, b=40),
)
fig.update_xaxes(
    showgrid=False,
    rangebreaks=[
        dict(bounds=["sat", "mon"]),  # hide weekends
        dict(bounds=[15.5, 9.25], pattern="hour"),  # hide non-trading hours (15:30 to 09:15)
    ]
)
fig.update_yaxes(showgrid=True, gridcolor="rgba(255,255,255,0.06)")

st.plotly_chart(fig, use_container_width=True)

# --- Data Table ---
with st.expander("📊 View Raw Data"):
    st.dataframe(
        df[["ts", "open", "high", "low", "close", "volume", "rsi", "macd_line", "signal_line", "ma_fast", "ma_slow"]]
        .tail(50)
        .style.format({"open": "₹{:.2f}", "high": "₹{:.2f}", "low": "₹{:.2f}", "close": "₹{:.2f}",
                        "rsi": "{:.2f}", "macd_line": "{:.2f}", "signal_line": "{:.2f}",
                        "ma_fast": "₹{:.2f}", "ma_slow": "₹{:.2f}"}),
        use_container_width=True,
    )
