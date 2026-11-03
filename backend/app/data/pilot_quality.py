"""Read-only quality reporting for the isolated historical-data pilot."""

import json
from collections import Counter
from datetime import datetime, timezone

from app.data.pilot_store import connect_pilot


def _utc(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.astimezone(timezone.utc)


def generate_pilot_quality_report(database_path, plan_id):
    connection = connect_pilot(database_path)

    try:
        plan_row = connection.execute(
            "SELECT plan_json FROM acquisition_plans WHERE plan_id = ?",
            (plan_id,),
        ).fetchone()

        if plan_row is None:
            raise ValueError("pilot acquisition plan was not found")

        plan = json.loads(plan_row["plan_json"])

        chunks = [
            dict(row)
            for row in connection.execute(
                """
                SELECT chunk_no, status, bar_slots, returned_rows,
                       duplicate_rows, error_type
                FROM acquisition_chunks
                WHERE plan_id = ?
                ORDER BY chunk_no
                """,
                (plan_id,),
            ).fetchall()
        ]

        if not chunks:
            raise ValueError("pilot acquisition plan has no registered chunks")

        # Use actual provider request boundaries. This matters for D1 plans,
        # whose chunks expand to complete exchange-local calendar days.
        request_start = min(_utc(chunk["start"]) for chunk in plan["chunks"])
        request_end = max(_utc(chunk["end"]) for chunk in plan["chunks"])

        rows = connection.execute(
            """
            SELECT timestamp, open, high, low, close, volume,
                   volume_available, source, source_version,
                   session_convention, price_component
            FROM pilot_candles
            WHERE plan_id = ?
              AND instrument = ? AND timeframe = ?
              AND timestamp >= ? AND timestamp <= ?
            ORDER BY timestamp, source
            """,
            (
                plan_id,
                plan["instrument"],
                plan["timeframe"],
                request_start.isoformat(),
                request_end.isoformat(),
            ),
        ).fetchall()

        invalid_ohlc = 0
        invalid_volume = 0

        for row in rows:
            if (
                row["high"] < max(row["open"], row["close"], row["low"])
                or row["low"] > min(row["open"], row["close"], row["high"])
            ):
                invalid_ohlc += 1

            if (
                row["volume"] < 0
                or row["volume_available"] not in (0, 1)
                or (not row["volume_available"] and row["volume"] != 0)
            ):
                invalid_volume += 1

        timestamp_counts = Counter(row["timestamp"] for row in rows)
        unique_timestamps = len(timestamp_counts)
        duplicate_timestamp_rows = sum(
            count - 1 for count in timestamp_counts.values()
        )

        sources = sorted({row["source"] for row in rows})
        source_versions = sorted({row["source_version"] for row in rows})
        price_components = sorted({row["price_component"] for row in rows})
        session_conventions = sorted(
            {row["session_convention"] for row in rows}
        )

        complete_chunks = sum(
            chunk["status"] == "COMPLETE" for chunk in chunks
        )
        failed_chunks = sum(
            chunk["status"] == "FAILED" for chunk in chunks
        )
        running_chunks = sum(
            chunk["status"] == "RUNNING" for chunk in chunks
        )

        total_slots = int(plan["bar_slots"])
        observed_rows = len(rows)

        return {
            "plan_id": plan_id,
            "instrument": plan["instrument"],
            "timeframe": plan["timeframe"],
            "status": (
                "ALL_CHUNKS_COMPLETE_REVIEW_REQUIRED"
                if complete_chunks == len(chunks)
                else "INCOMPLETE"
            ),
            "pilot_database_only": True,
            "production_database_read": False,
            "chunk_summary": {
                "total": len(chunks),
                "complete": complete_chunks,
                "failed": failed_chunks,
                "running": running_chunks,
                "provider_rows_returned": sum(
                    chunk["returned_rows"] for chunk in chunks
                ),
                "replayed_duplicate_rows": sum(
                    chunk["duplicate_rows"] for chunk in chunks
                ),
                "states": chunks,
            },
            "candle_summary": {
                "observed_rows_in_requested_range": observed_rows,
                "unique_observed_timestamps": unique_timestamps,
                "time_grid_slots": total_slots,
                "unverified_time_grid_slots": max(
                    0, total_slots - unique_timestamps
                ),
                "unverified_slots_are_not_confirmed_missing_candles": True,
                "duplicate_timestamp_rows": duplicate_timestamp_rows,
                "ohlc_invalid_rows": invalid_ohlc,
                "volume_invalid_rows": invalid_volume,
                "volume_available_rows": sum(
                    row["volume_available"] == 1 for row in rows
                ),
                "volume_unavailable_rows": sum(
                    row["volume_available"] == 0 for row in rows
                ),
            },
            "provenance": {
                "sources": sources,
                "source_versions": source_versions,
                "price_components": price_components,
                "session_conventions": session_conventions,
                "multiple_sources_present": len(sources) > 1,
            },
            "limitations": [
                "Only candles originally stored under this acquisition plan are counted.",
                "Replayed duplicates are reported separately from newly stored candles.",
                "Multiple source rows at one timestamp are counted as repeated timestamp rows.",
                "No session calendar was used to classify market closures.",
                "Unverified slots are not proof of missing candles.",
                "No interpolation, repair, or replacement was performed.",
                "Licensing and attribution remain subject to written approval.",
                "A complete import still requires independent review.",
            ],
        }
    finally:
        connection.close()
