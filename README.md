# ORACLE FX — Global Market Intelligence

Independent AI-powered XAUUSD market intelligence and forecasting platform.

## Mission
Research global markets, understand price behaviour and develop independently validated forecasts.

## Initial instrument
XAUUSD (Gold).

## Architecture
- Python FastAPI backend
- SQLite research and forecast ledger
- Market structure analysis
- Liquidity sweep detection
- Displacement and FVG candidates
- Automated tests

## Safety
- No broker connections.
- No demo or live trading accounts.
- No trade execution.
- No account management.
- Forecast probabilities require independent validation.

## Run locally
Activate the virtual environment and install requirements:

pip install -r requirements.txt

Start the API:

PYTHONPATH=backend uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
