# Quant-MT5-Trading-System

A production-grade algorithmic trading system designed for FTMO-style risk management. It combines a Python-based signal generation engine with a MetaTrader 5 Expert Advisor (EA) that executes trades, manages positions, and enforces strict risk limits. The system uses statistical indicators and market microstructure features to generate high-confidence trade signals.

System Overview
QuantHermes consists of two independent components that communicate over HTTP:

Python Signal Server (FastAPI) – Fetches live market data from MetaTrader 5, computes features, applies filters, and returns a trading signal.

MQL5 Expert Advisor – Polls the server every few seconds, parses the signal, and executes trades with dynamic position sizing, trailing stops, and FTMO compliance checks.

The EA acts as a thin client: all decision logic resides in the Python server, making the system easy to extend, backtest, and monitor.

Data Flow

┌─────────────┐      ┌─────────────────────────────┐      ┌──────────────────┐
│  MetaTrader │      │      Python Signal Server   │      │  MQL5 EA Client  │
│   Terminal  │      │       (FastAPI + MT5 API)   │      │  (Trade Exec)    │
└─────────────┘      └─────────────────────────────┘      └──────────────────┘
      │                           │                               │
      │                           │                               │
      │  1. Request market data   │                               │
      │◄─────────────────────────►│                               │
      │                           │                               │
      │                           │  2. Compute features          │
      │                           │     (Hurst, VWAP, vol)        │
      │                           │                               │
      │                           │  3. Apply filters &           │
      │                           │     risk logic                │
      │                           │                               │
      │                           │  4. Return signal             │
      │                           │◄──────────────────────────────│
      │                           │     HTTP GET /signal          │
      │                           │                               │
      │                           │                               │
      │                           │  5. Parse signal              │
      │                           │     (action, stop, risk)      │
      │                           │                               │
      │                           │  6. Calculate lot size        │
      │                           │     based on risk %           │
      │                           │                               │
      │                           │  7. Execute trade             │
      │                           │     via MetaTrader            │
      │◄──────────────────────────│──────────────────────────────►│
      │                           │                               │
      │                           │  8. Manage trailing stops     │
      │                           │     (EA internal loop)        │
      │                           │                               │


Mathematical Models & Indicators
Hurst Exponent (H)
Used to classify market regimes:
H > 0.55 → trending regime → trend-following logic
H < 0.45 → mean-reverting regime → mean-reversion logic

Computed by estimating the scaling of the standard deviation of price differences over varying lags.
Implemented with Numba for speed.

VWAP Z‑Score
Tracks the deviation of current price from the volume-weighted average price of the current trading session.
Normalized by the standard deviation of the difference to produce a bounded score in [-3, 3].

z near zero → neutral zone (no trades)
z extreme (±2.2) → signals in mean-reverting regime

Parkinson Volatility
An efficient estimator of historical volatility based on high and low prices only:
σ_Parkinson = sqrt( (1 / (4 * n * ln(2))) * Σ (ln(high_i / low_i))^2 )
Used for:

Stop-loss sizing (SL = current volatility × 1.5)

Volatility choke (reject trades if current volatility exceeds 95th percentile of recent values)

Momentum Confirmation
Ensures that the signal aligns with short‑term momentum:

For BUY signals: last price change (3‑bar) must be positive

For SELL signals: last price change must be negative

H1 Trend Filter
Uses the difference between 5‑period and 20‑period simple moving averages on the 1‑hour timeframe.
Prevents trades against the higher‑timeframe trend.

Risk Management & FTMO Rules
The system implements a strict set of rules to comply with FTMO challenge/prop firm requirements:

Rule	Implementation:
Daily loss limit	Tracks equity at start of day; rejects trades if loss > 4.5% of that equity
Friday cutoff	No new trades after 20:00 server time on Friday; closes all positions
News filter	Blocks trading ±15 minutes around high-impact USD news events (importance = 3)
Max trades per day	Limited to 3 signals per day
Single position	Only one position allowed at any time (no pyramiding)
Position sizing	Risk‑based lot calculation using risk_perc (1% default) and volatility‑based stop
Trailing stop	Activates when profit reaches initial stop distance; trails by 0.25 of that distance
Signal Generation Logic
The server combines the above indicators into a state machine:

Pre‑filters: Check FTMO rules, trade limit, volatility choke, H1 trend, momentum, etc.

Regime classification via Hurst exponent.

Trend‑following (H > 0.55):
Price above 20‑SMA + VWAP z‑score below 1 → BUY
Price below 20‑SMA + VWAP z‑score above -1 → SELL

Mean‑reversion (H < 0.45):
VWAP z‑score < -2.2 → BUY
VWAP z‑score > 2.2 → SELL

Final checks: H1 trend alignment, momentum confirmation.

Return signal with risk_perc, sl_points (volatility‑based), tp_rr (2:1 risk‑reward), and trailing parameters.

Installation & Usage
Prerequisites
MetaTrader 5 terminal installed
Python 3.9+ with pip
MQL5 compiler (included with MT5)

Python Server Setup
bash
cd QuantHermes
pip install -r requirements.txt

Start the server:
bash
python QuantHermes.py
The server will run at http://127.0.0.1:8000.

MQL5 EA Installation
Compile QuantHermes.mq5 (or copy the provided .ex5) into your MetaTrader 5 MQL5/Experts folder.
Attach the EA to a chart of the desired symbol (e.g., US500).
Ensure the Python server is running before the EA starts polling.

Configuration
API_URL – change if server runs on a different IP/port.
TimerSeconds – polling interval (5 seconds recommended).
MaxRetries – number of HTTP retries on failure.

Dependencies
Python
fastapi – web framework for the signal endpoint
uvicorn – ASGI server
numpy – numerical computations
numba – JIT compilation for performance
MetaTrader5 – Python API for MetaTrader 5

MQL5
Standard library (Trade.mqh)

Project Structure
text
QuantHermes/
├── QuantHermes.py          # FastAPI server with signal logic
├── QuantHermes.mq5         # MQL5 Expert Advisor (EA)
├── README.md               # This file
└── requirements.txt        # Python dependencies

Future Enhancements (Planned):
Persist all signals and market data to a time‑series database (TimescaleDB)
Add a backtesting harness using historical data
Containerize the Python server with Docker

Add monitoring dashboards (Grafana + Prometheus) for trade performance

License
MIT (or as you choose)

QuantHermes is a demonstration of an end‑to‑end algorithmic trading system combining statistical modeling, real‑time data processing, and robust risk management. It is intended for educational and research purposes.
