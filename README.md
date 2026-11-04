# ORACLE FX — Global Market Intelligence

ORACLE FX is an independent market-research and intelligence platform
for global FX, precious metals, selected indices, energy and crypto.

Its objective is to explain verified market conditions, maintain
bullish and bearish scenarios, evaluate evidence and identify
confirmed opportunities only when every mandatory condition passes.

## Product boundaries

- Global, provider-neutral market universe.
- Technical, macroeconomic, intermarket and statistical research.
- Historical research and forecast accountability.
- Confirmed-only research alerts as a future capability.
- No broker connection, account access or trade execution.
- No invented market prices, events, probabilities or provider coverage.
- Missing, stale, unlicensed or conflicting evidence means WAIT.

## Current implementation

The repository currently includes:

- FastAPI backend and SQLite research ledger.
- Canonical 30-instrument universe.
- D1, H4, H1 and M15 timeframe definitions.
- Market structure, liquidity, displacement and FVG candidates.
- Multi-timeframe and event-sequence analysis.
- Bullish/bearish scenario orchestration.
- Manual macro-event import and event-risk assessment.
- User-supplied cross-asset analysis.
- Historical candle storage and quality reports.
- Provider approval, ingestion and readiness infrastructure.
- Fail-closed confirmed-signal evidence gate.

These capabilities do not establish live global market coverage.
Provider registration is not proof of licensing, connectivity or
signal eligibility.

## Current operating restrictions

- No verified live market feed is connected.
- Provider credentials and written rights must be configured separately.
- Historical rights do not imply live-data rights.
- No automated macro-calendar coverage is claimed.
- Forecast probabilities and predictive accuracy are not validated.
- No broker or trade execution functionality exists.

## Technology

- Python
- FastAPI
- Pydantic
- SQLite
- Pytest

## Local development

Create and activate a virtual environment:

    python -m venv .venv
    source .venv/bin/activate

Install development dependencies:

    pip install -r requirements-dev.txt

Run tests:

    PYTHONPATH=backend pytest -q

Compile backend:

    python -m compileall -q backend

Start the API:

    PYTHONPATH=backend uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8000

Health endpoint:

    GET /health

API documentation:

    /docs

## Development roadmap

See `docs/ORACLE_FX_COMPLETION_ROADMAP.md`.

All implementation stages must preserve licensing controls, data
provenance, reproducibility and the confirmed-only signal policy.
