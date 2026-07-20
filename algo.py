"""
================================================================================
GROWW INTRADAY INDEX OPTIONS ALGO  (NIFTY / BANKNIFTY)
================================================================================
Strategy   : Multi-indicator confirmation (RSI + MACD + Moving Average crossover)
Instrument : NIFTY / BANKNIFTY ATM weekly options (CE/PE), intraday only
Broker     : Groww (growwapi Python SDK)

--------------------------------------------------------------------------------
READ THIS BEFORE YOU TOUCH LIVE MONEY
--------------------------------------------------------------------------------
1. This script is a STARTING FRAMEWORK, not a proven profitable strategy.
   No combination of RSI/MACD/MA has a guaranteed edge. Options intraday
   trading can lose your entire capital quickly (theta decay + leverage).
2. DRY_RUN = True by default. In dry-run mode the script prints what it
   WOULD have done but places NO real orders. Only flip to False once you
   have paper-traded/backtested this for weeks and understand every line.
3. India (SEBI/NSE, effective Apr 2026) requires retail algo API users to:
     - Register a static IP with Groww for API access
     - Register a generic Algo ID with the exchange via your broker
   Do this on the Groww API dashboard (groww.in/trade-api/api-keys) before
   going live. This script does not handle that registration for you.
4. I am not a financial advisor. This is not investment advice. You are
   solely responsible for every trade this script places on your behalf.
5. Start with the SMALLEST possible lot size and a hard daily loss cap.
================================================================================
"""

import time
import logging
import datetime as dt
from dataclasses import dataclass, field
from typing import Optional, Literal

import pandas as pd
import pyotp
from growwapi import GrowwAPI


# ==============================================================================
# 1. CONFIGURATION  -- edit everything in this block
# ==============================================================================

@dataclass
class Config:
    # --- Auth (TOTP flow: no daily re-approval needed) ---
    API_KEY: str = "eyJraWQiOiJaTUtjVXciLCJhbGciOiJFUzI1NiJ9.eyJleHAiOjI1NzI5MjYwMjksImlhdCI6MTc4NDUyNjAyOSwibmJmIjoxNzg0NTI2MDI5LCJzdWIiOiJ7XCJ0b2tlblJlZklkXCI6XCIyNTA4YTVjMS0yYzYwLTRkNzItODI4Mi01NzQzYmM3NDE5ZWFcIixcInZlbmRvckludGVncmF0aW9uS2V5XCI6XCJlMzFmZjIzYjA4NmI0MDZjODg3NGIyZjZkODQ5NTMxM1wiLFwidXNlckFjY291bnRJZFwiOlwiMTY1MTI5NDEtZTRmZS00Y2IyLWEzZTUtOGNmNDc1NjE4ODNhXCIsXCJkZXZpY2VJZFwiOlwiYjA5ZWQ4ZTUtYTMxZC01ODVmLTg4ZmQtMjQ3NDQ1NjcxYTEzXCIsXCJzZXNzaW9uSWRcIjpcIjMzYmRhMmRiLTI5OTUtNDdkMS1iYzhhLWJkYjBjYmE3YTEwZFwiLFwiYWRkaXRpb25hbERhdGFcIjpcIno1NC9NZzltdjE2WXdmb0gvS0EwYlB1cEg2NlJ2M05jL1JGVk5lZnZPOVJSTkczdTlLa2pWZDNoWjU1ZStNZERhWXBOVi9UOUxIRmtQejFFQisybTdRPT1cIixcInJvbGVcIjpcImF1dGgtdG90cFwiLFwic291cmNlSXBBZGRyZXNzXCI6XCIyNDAxOjQ5MDA6ODk0NjozY2ZjOmU1YTY6MTljYjpkZmEzOmFiYmIsMTcyLjY4LjEyNy4xNDAsMzUuMjQxLjIzLjEyM1wiLFwidHdvRmFFeHBpcnlUc1wiOjI1NzI5MjYwMjk5MjgsXCJ2ZW5kb3JOYW1lXCI6XCJncm93d0FwaVwifSIsImlzcyI6ImFwZXgtYXV0aC1wcm9kLWFwcCJ9.vjSNE6P4QxaC8S1FvtdGKaGW8I9nshH7k8Nh0fbp_QufMKJvVJEdxqBHaVfMzlYxfYM8LLXGNq_u2yitdk_VaQ"          # from groww.in/trade-api/api-keys
    API_SECRET: str = "oB#K&H9JkFzdF(pWqZ^HTHx6wLf-Hy4U"      # from groww.in/trade-api/api-keys

    # --- Safety switch ---
    DRY_RUN: bool = True                      # <-- keep True until you trust this

    # --- Instruments to trade (index options only, per your scope) ---
    UNDERLYINGS: tuple = ("NIFTY", "BANKNIFTY")

    # --- Indicator params ---
    RSI_PERIOD: int = 14
    RSI_OVERBOUGHT: float = 60.0
    RSI_OVERSOLD: float = 40.0
    MACD_FAST: int = 12
    MACD_SLOW: int = 26
    MACD_SIGNAL: int = 9
    MA_FAST: int = 9
    MA_SLOW: int = 21
    CANDLE_INTERVAL_MIN: int = 5              # 5-min candles on the index
    LOOKBACK_MINUTES: int = 60 * 6            # history to pull each poll

    # --- Risk management ---
    CAPITAL: float = 100000.0                 # total capital allocated to this algo
    RISK_PER_TRADE_PCT: float = 1.0           # % of capital risked per trade
    STOPLOSS_PCT: float = 20.0                # SL = 20% below option entry premium
    TARGET_PCT: float = 40.0                  # Target = 40% above entry premium
    MAX_TRADES_PER_DAY: int = 4               # per underlying
    MAX_DAILY_LOSS_PCT: float = 3.0           # circuit breaker: stop algo for the day
    LOTS_PER_TRADE: int = 1                   # number of lots per signal (start at 1)

    # --- Timing (IST, market hours) ---
    MARKET_OPEN: dt.time = dt.time(9, 20)     # skip first few min of noise
    MARKET_CLOSE: dt.time = dt.time(15, 10)
    SQUARE_OFF_TIME: dt.time = dt.time(15, 15) # force-exit all open positions
    POLL_SECONDS: int = 60                    # how often to check for signals

    LOG_FILE: str = "algo_trading_log.txt"

CFG = Config()


# ==============================================================================
# 2. LOGGING
# ==============================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(CFG.LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("algo")


# ==============================================================================
# 3. AUTH
# ==============================================================================
def get_groww_client(cfg: Config) -> GrowwAPI:
    # Use the approval secret flow directly instead of TOTP
    access_token = GrowwAPI.get_access_token(api_key=cfg.API_KEY, secret=cfg.API_SECRET)
    client = GrowwAPI(access_token)
    log.info("Authenticated with Groww API.")
    return client


# ==============================================================================
# 4. INDICATORS
# ==============================================================================
def compute_rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-9)
    return 100 - (100 / (1 + rs))

def compute_macd(close: pd.Series, fast: int, slow: int, signal: int):
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return macd_line, signal_line

def compute_ma_cross(close: pd.Series, fast: int, slow: int):
    ma_fast = close.rolling(fast).mean()
    ma_slow = close.rolling(slow).mean()
    return ma_fast, ma_slow


Signal = Literal["BUY_CE", "BUY_PE", "NONE"]

def generate_signal(df: pd.DataFrame, cfg: Config) -> Signal:
    """
    Confluence rule (all three must agree to reduce false signals):
      BUY_CE (bullish) if: RSI crosses above oversold recovering AND
                           MACD line > signal line AND
                           fast MA > slow MA (and just crossed up)
      BUY_PE (bearish) mirror conditions.
    """
    if len(df) < max(cfg.MA_SLOW, cfg.MACD_SLOW) + 5:
        return "NONE"

    close = df["close"]
    rsi = compute_rsi(close, cfg.RSI_PERIOD)
    macd_line, signal_line = compute_macd(close, cfg.MACD_FAST, cfg.MACD_SLOW, cfg.MACD_SIGNAL)
    ma_fast, ma_slow = compute_ma_cross(close, cfg.MA_FAST, cfg.MA_SLOW)

    r, r_prev = rsi.iloc[-1], rsi.iloc[-2]
    m, s = macd_line.iloc[-1], signal_line.iloc[-1]
    mf, ms = ma_fast.iloc[-1], ma_slow.iloc[-1]
    mf_prev, ms_prev = ma_fast.iloc[-2], ma_slow.iloc[-2]

    ma_bull_cross = mf_prev <= ms_prev and mf > ms
    ma_bear_cross = mf_prev >= ms_prev and mf < ms

    bullish = (r_prev < cfg.RSI_OVERSOLD <= r) and (m > s) and (mf > ms) and ma_bull_cross
    bearish = (r_prev > cfg.RSI_OVERBOUGHT >= r) and (m < s) and (mf < ms) and ma_bear_cross

    if bullish:
        return "BUY_CE"
    if bearish:
        return "BUY_PE"

    return "NONE"


# ==============================================================================
# 5. INSTRUMENT / STRIKE SELECTION
# ==============================================================================
def get_index_ltp(client: GrowwAPI, underlying: str) -> float:
    resp = client.get_ltp(
        exchange_trading_symbols=f"NSE_{underlying}",
        segment=client.SEGMENT_CASH,
    )
    return float(list(resp.values())[0]) if isinstance(resp, dict) else float(resp["ltp"])

def get_index_candles(client: GrowwAPI, underlying: str, cfg: Config) -> pd.DataFrame:
    end = dt.datetime.now()
    start = end - dt.timedelta(minutes=cfg.LOOKBACK_MINUTES)

    resp = client.get_historical_candle_data(
        trading_symbol=underlying,
        exchange=client.EXCHANGE_NSE,
        segment=client.SEGMENT_CASH,
        start_time=start.strftime("%Y-%m-%d %H:%M:%S"),
        end_time=end.strftime("%Y-%m-%d %H:%M:%S"),
        interval_in_minutes=cfg.CANDLE_INTERVAL_MIN,
    )

    candles = resp["candles"]
    df = pd.DataFrame(candles, columns=["ts", "open", "high", "low", "close", "volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="s")
    return df

def find_atm_option_symbol(client: GrowwAPI, underlying: str, spot: float,
                           option_type: Literal["CE", "PE"]) -> Optional[dict]:
    """
    Pulls the full instrument list and finds the nearest-expiry, ATM strike
    for the given underlying/option type. Caches the instrument dataframe
    on the client object to avoid re-downloading every poll.
    """
    if not hasattr(client, "_instrument_cache"):
        client._instrument_cache = client.get_all_instruments()

    df = client._instrument_cache
    strike_step = 100 if underlying == "BANKNIFTY" else 50
    atm_strike = round(spot / strike_step) * strike_step

    candidates = df[
        (df["underlying_symbol"] == underlying)
        & (df["segment"] == "FNO")
        & (df["instrument_type"] == option_type)
        & (df["strike_price"] == atm_strike)
    ].copy()

    if candidates.empty:
        log.warning(f"No ATM {option_type} contract found for {underlying} @ {atm_strike}")
        return None

    candidates["expiry_date"] = pd.to_datetime(candidates["expiry_date"])
    candidates = candidates.sort_values("expiry_date")
    nearest = candidates.iloc[0]

    return {
        "trading_symbol": nearest["trading_symbol"],
        "lot_size": int(nearest["lot_size"]),
        "strike": atm_strike,
        "expiry": nearest["expiry_date"],
    }


# ==============================================================================
# 6. RISK / STATE TRACKING
# ==============================================================================
@dataclass
class DayState:
    trades_today: dict = field(default_factory=lambda: {u: 0 for u in CFG.UNDERLYINGS})
    realized_pnl: float = 0.0
    open_positions: dict = field(default_factory=dict)  # underlying -> position dict
    halted: bool = False

STATE = DayState()

def daily_loss_limit_hit(cfg: Config, state: DayState) -> bool:
    max_loss = cfg.CAPITAL * cfg.MAX_DAILY_LOSS_PCT / 100
    if state.realized_pnl <= -max_loss:
        if not state.halted:
            log.warning(f"Daily loss limit hit (₹{state.realized_pnl:.0f}). Halting new trades.")
        state.halted = True
        return True
    return False

def position_size(cfg: Config, lot_size: int, premium: float) -> int:
    """Quantity = lots * lot_size, capped by risk-per-trade budget."""
    risk_budget = cfg.CAPITAL * cfg.RISK_PER_TRADE_PCT / 100
    max_loss_per_unit = premium * cfg.STOPLOSS_PCT / 100

    if max_loss_per_unit <= 0:
        return cfg.LOTS_PER_TRADE * lot_size

    max_qty_by_risk = int(risk_budget / max_loss_per_unit)
    max_qty_by_risk -= max_qty_by_risk % lot_size  # round down to whole lots

    planned_qty = cfg.LOTS_PER_TRADE * lot_size
    return max(min(planned_qty, max_qty_by_risk), 0)


# ==============================================================================
# 7. ORDER EXECUTION
# ==============================================================================
def place_entry_order(client: GrowwAPI, cfg: Config, symbol: str, qty: int) -> Optional[dict]:
    if qty <= 0:
        log.warning(f"Computed qty=0 for {symbol}, skipping (risk budget too small).")
        return None

    if cfg.DRY_RUN:
        log.info(f"[DRY_RUN] Would BUY {qty} of {symbol} (MARKET, INTRADAY/MIS)")
        return {"groww_order_id": "DRYRUN", "trading_symbol": symbol, "quantity": qty}

    resp = client.place_order(
        trading_symbol=symbol,
        quantity=qty,
        validity=client.VALIDITY_DAY,
        exchange=client.EXCHANGE_NSE,
        segment=client.SEGMENT_FNO,
        product=client.PRODUCT_MIS,         # intraday margin product
        order_type=client.ORDER_TYPE_MARKET,
        transaction_type=client.TRANSACTION_TYPE_BUY,
    )
    log.info(f"Entry order placed: {resp}")
    return resp

def place_exit_order(client: GrowwAPI, cfg: Config, symbol: str, qty: int, reason: str) -> Optional[dict]:
    if cfg.DRY_RUN:
        log.info(f"[DRY_RUN] Would SELL {qty} of {symbol} (MARKET) -- reason: {reason}")
        return {"groww_order_id": "DRYRUN", "trading_symbol": symbol, "quantity": qty}

    resp = client.place_order(
        trading_symbol=symbol,
        quantity=qty,
        validity=client.VALIDITY_DAY,
        exchange=client.EXCHANGE_NSE,
        segment=client.SEGMENT_FNO,
        product=client.PRODUCT_MIS,
        order_type=client.ORDER_TYPE_MARKET,
        transaction_type=client.TRANSACTION_TYPE_SELL,
    )
    log.info(f"Exit order placed ({reason}): {resp}")
    return resp

def get_option_ltp(client: GrowwAPI, symbol: str) -> float:
    resp = client.get_ltp(exchange_trading_symbols=f"NSE_{symbol}", segment=client.SEGMENT_FNO)
    return float(list(resp.values())[0]) if isinstance(resp, dict) else float(resp["ltp"])


# ==============================================================================
# 8. MAIN LOOP
# ==============================================================================
def manage_open_position(client: GrowwAPI, cfg: Config, underlying: str):
    """Check SL/target on any open position for this underlying."""
    pos = STATE.open_positions.get(underlying)
    if not pos:
        return

    ltp = get_option_ltp(client, pos["symbol"])
    entry = pos["entry_price"]
    change_pct = (ltp - entry) / entry * 100

    hit_sl = change_pct <= -cfg.STOPLOSS_PCT
    hit_target = change_pct >= cfg.TARGET_PCT
    force_exit = dt.datetime.now().time() >= cfg.SQUARE_OFF_TIME

    if hit_sl or hit_target or force_exit:
        reason = "STOPLOSS" if hit_sl else "TARGET" if hit_target else "SQUARE_OFF"
        place_exit_order(client, cfg, pos["symbol"], pos["qty"], reason)

        pnl = (ltp - entry) * pos["qty"]
        STATE.realized_pnl += pnl
        log.info(f"Closed {underlying} position | reason={reason} | pnl=₹{pnl:.0f} | day_pnl=₹{STATE.realized_pnl:.0f}")

        STATE.open_positions.pop(underlying, None)


def try_enter_trade(client: GrowwAPI, cfg: Config, underlying: str):
    if underlying in STATE.open_positions:
        return  # already in a trade for this underlying
    if STATE.trades_today[underlying] >= cfg.MAX_TRADES_PER_DAY:
        return
    if daily_loss_limit_hit(cfg, STATE):
        return

    df = get_index_candles(client, underlying, cfg)
    signal = generate_signal(df, cfg)

    if signal == "NONE":
        return

    spot = df["close"].iloc[-1]
    opt_type = "CE" if signal == "BUY_CE" else "PE"

    instrument = find_atm_option_symbol(client, underlying, spot, opt_type)
    if not instrument:
        return

    premium = get_option_ltp(client, instrument["trading_symbol"])
    qty = position_size(cfg, instrument["lot_size"], premium)

    order = place_entry_order(client, cfg, instrument["trading_symbol"], qty)
    if order is None:
        return

    STATE.open_positions[underlying] = {
        "symbol": instrument["trading_symbol"],
        "qty": qty,
        "entry_price": premium,
        "entry_time": dt.datetime.now(),
    }
    STATE.trades_today[underlying] += 1
    log.info(f"ENTERED {signal} on {underlying}: {instrument['trading_symbol']} qty={qty} @ ₹{premium:.2f}")


def within_market_hours(cfg: Config) -> bool:
    now = dt.datetime.now().time()
    return cfg.MARKET_OPEN <= now <= cfg.MARKET_CLOSE


def run():
    log.info(f"Starting algo | DRY_RUN={CFG.DRY_RUN} | underlyings={CFG.UNDERLYINGS}")
    if CFG.DRY_RUN:
        log.info("Running in DRY_RUN (paper) mode -- no real orders will be placed.")

    client = get_groww_client(CFG)

    while True:
        try:
            if not within_market_hours(CFG):
                log.info("Outside market hours. Sleeping...")
                time.sleep(CFG.POLL_SECONDS)
                continue

            for underlying in CFG.UNDERLYINGS:
                manage_open_position(client, CFG, underlying)
                try_enter_trade(client, CFG, underlying)

            if dt.datetime.now().time() >= CFG.SQUARE_OFF_TIME:
                log.info(f"Square-off time reached. Day PnL: ₹{STATE.realized_pnl:.0f}")

        except Exception as e:
            log.exception(f"Error in main loop: {e}")

        time.sleep(CFG.POLL_SECONDS)


if __name__ == "__main__":
    run()
