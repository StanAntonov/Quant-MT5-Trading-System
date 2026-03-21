# quant_brain_v4_1.py

import MetaTrader5 as mt5
import numpy as np
from numba import njit
from fastapi import FastAPI
import uvicorn
from datetime import datetime, timedelta
import logging

#Configuration
SYMBOL = "US500"
TIMEFRAME = mt5.TIMEFRAME_M15

PROFIT_TARGET = 10000.0
DAILY_LOSS_PERC = 0.045
BASE_RISK_PERC = 0.01

NEWS_BUFFER_MINUTES = 15
VOL_CHOKE_PERCENTILE = 95

MAX_TRADES_PER_DAY = 3

#Global state
app = FastAPI()

day_start_equity = None
last_server_day = -1

daily_trades = 0
last_trade_day = -1

hurst_history = []

#Init
logging.basicConfig(level=logging.INFO)

if not mt5.initialize():
    raise Exception("MT5 initialization failed")

#Core math
@njit(cache=True)
def get_hurst(series):
    lags = np.arange(2, 60)
    tau = np.zeros(len(lags))
    for i, lag in enumerate(lags):
        tau[i] = np.std(series[lag:] - series[:-lag])
    return np.polyfit(np.log(lags), np.log(tau), 1)[0]

@njit(cache=True)
def get_parkinson_vol(highs, lows, window=20):
    sum_sq = 0.0
    start = max(0, len(highs) - window)
    for i in range(start, len(highs)):
        sum_sq += np.log(highs[i] / lows[i]) ** 2
    return np.sqrt((1 / (4 * window * np.log(2))) * sum_sq)

#Features
def get_session_vwap_z(rates):
    prices = np.array([r[4] for r in rates])
    volumes = np.array([r[5] for r in rates])
    times = [datetime.fromtimestamp(r[0]) for r in rates]

    last_date = times[-1].date()
    mask = np.array([t.date() == last_date for t in times])

    p_today = prices[mask]
    v_today = volumes[mask]

    if len(p_today) < 2:
        return 0.0

    vwap = np.sum(p_today * v_today) / np.sum(v_today)
    diff = p_today - vwap

    std = np.std(diff)
    if std == 0:
        return 0.0

    z = (prices[-1] - vwap) / std
    return float(np.clip(z, -3, 3))

def get_h1_trend():
    rates = mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_H1, 0, 50)
    if rates is None or len(rates) < 20:
        return 0

    closes = np.array([r[4] for r in rates])

    short = np.mean(closes[-5:])
    long = np.mean(closes[-20:])
    strength = short - long

    threshold = np.std(closes[-20:]) * 0.2

    if strength > threshold:
        return 1
    elif strength < -threshold:
        return -1
    return 0

def momentum_confirms(action, closes):
    momentum = closes[-1] - closes[-3]

    if action == "BUY" and momentum <= 0:
        return False
    if action == "SELL" and momentum >= 0:
        return False
    return True

def get_volatility_percentile(highs, lows, current_vol, window=100, vol_window=20):
    if len(highs) < window + vol_window:
        return False

    vols = []
    for i in range(len(highs) - window, len(highs)):
        if i - vol_window < 0:
            continue
        v = get_parkinson_vol(highs[i-vol_window:i], lows[i-vol_window:i], vol_window)
        vols.append(v)

    if len(vols) < 10:
        return False

    return current_vol > np.percentile(vols, VOL_CHOKE_PERCENTILE)

#Prop firm risk parameters - Aligned them with FTMO's.
def check_ftmo_rules():
    global day_start_equity, last_server_day

    acc = mt5.account_info()
    tick = mt5.symbol_info_tick(SYMBOL)

    if acc is None or tick is None:
        return False, "MT5_ERROR"

    server_time = datetime.fromtimestamp(tick.time)

    #Daily reset
    if server_time.day != last_server_day:
        day_start_equity = acc.equity
        last_server_day = server_time.day
        logging.info(f"New day equity: {day_start_equity}")

    #Daily loss limit
    if day_start_equity:
        dd = (day_start_equity - acc.equity) / day_start_equity
        if dd >= DAILY_LOSS_PERC:
            return False, "DAILY_LOSS_LIMIT"

    #Friday cutoff (CRITICAL) - I've applied this to prevent any weekend exposure. Gaps > daily loss = blown risk parameter and lost account.
    if server_time.weekday() == 4 and server_time.hour >= 20:
        return False, "FRIDAY_CUTOFF"

    #News filter
    now = datetime.utcnow()
    events = mt5.calendar_get(
        time_from=now - timedelta(minutes=NEWS_BUFFER_MINUTES),
        time_to=now + timedelta(minutes=NEWS_BUFFER_MINUTES)
    )

    if events:
        for e in events:
            if e.importance == 3 and e.currency == "USD":
                return False, "NEWS_BLOCK"

    #1 position only
    if mt5.positions_total() > 0:
        return False, "POSITION_OPEN"

    return True, "OK"

#Signal generation
@app.get("/signal")
def fetch_signal():
    global daily_trades, last_trade_day, hurst_history

    rates = mt5.copy_rates_from_pos(SYMBOL, TIMEFRAME, 0, 500)
    if rates is None or len(rates) < 100:
        return {"action": "WAIT"}

    closes = np.array([r[4] for r in rates])
    highs = np.array([r[2] for r in rates])
    lows = np.array([r[3] for r in rates])

    #Compliance with FTMO
    ok, msg = check_ftmo_rules()
    if not ok:
        return {"action": "WAIT", "reason": msg}

    #Trade limiter
    now = datetime.fromtimestamp(rates[-1][0])
    if now.day != last_trade_day:
        daily_trades = 0
        last_trade_day = now.day

    if daily_trades >= MAX_TRADES_PER_DAY:
        return {"action": "WAIT", "reason": "MAX_TRADES"}

    #Indicators
    hurst_raw = get_hurst(closes)
    hurst_history.append(hurst_raw)
    if len(hurst_history) > 10:
        hurst_history.pop(0)
    hurst = np.mean(hurst_history)

    z = get_session_vwap_z(rates)

    #No-trade zone
    if abs(z) < 0.5:
        return {"action": "WAIT", "reason": "NO_EDGE"}

    vol = get_parkinson_vol(highs, lows)

    if get_volatility_percentile(highs, lows, vol):
        return {"action": "WAIT", "reason": "VOL_EXTREME"}

    sma = np.mean(closes[-20:])
    h1 = get_h1_trend()

    if h1 == 0:
        return {"action": "WAIT", "reason": "H1_NEUTRAL"}

    #Logic
    action = "WAIT"

    if hurst > 0.55:
        if closes[-1] > sma and z < 1:
            action = "BUY"
        elif closes[-1] < sma and z > -1:
            action = "SELL"

    elif hurst < 0.45:
        if z < -2.2:
            action = "BUY"
        elif z > 2.2:
            action = "SELL"

    if action == "WAIT":
        return {"action": "WAIT"}

    #Filters
    if action == "BUY" and h1 == -1:
        return {"action": "WAIT", "reason": "HTF_CONFLICT"}

    if action == "SELL" and h1 == 1:
        return {"action": "WAIT", "reason": "HTF_CONFLICT"}

    if not momentum_confirms(action, closes):
        return {"action": "WAIT", "reason": "NO_MOMENTUM"}

    #Final output
    daily_trades += 1

    return {
        "action": action,
        "risk_perc": BASE_RISK_PERC,   # EA will calculate lots
        "sl_points": float(vol * 1.5), # volatility-based stop
        "tp_rr": 2.0,                  # risk-reward ratio
        "trail_start_rr": 1.0,
        "trail_step_rr": 0.25
    }

@app.get("/health")
def health():
    return {"status": "ok"}

#Run
if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
