# Phase 11 — Macro calendar and event-risk intelligence

## Delivered in this overlay
- Separate append-only SQLite macro-event ledger; existing forecast and historical-candle stores remain untouched.
- Timezone-aware import of calendar observations with currency exposure, impact, source details and actual/forecast/previous values.
- Idempotent event IDs: repeated imports are ignored; existing rows cannot be updated or deleted.
- Event listing/filtering and deterministic instrument exposure/window assessment.
- Arithmetic actual-minus-forecast is reported only when both values exist. Its sign is not interpreted as bullish or bearish.

## API
- `POST /v1/macro/events/import` — import 1–200 calendar events.
- `GET /v1/macro/events` — filter by UTC-aware time range, currency, impact and limit.
- `POST /v1/macro/assess` — assess an instrument at a timezone-aware timestamp; default window is 60 minutes before and 30 minutes after an event.

## Important limitations
- Calendar entries are always `UNVERIFIED`; a URL or source name is not proof of authenticity, licensing, accuracy or completeness.
- No live calendar provider, automatic discovery, refresh scheduler or verified event coverage is connected.
- `event_risk_gate_passed` is always false. No matching imported event does not prove that the market is clear.
- No directional bias, probability, confirmed signal, alert delivery, broker connection or execution is produced.
- No market-data provider is contacted. This phase does not modify HAT.Ai or ORBIT.
