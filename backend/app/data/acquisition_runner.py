"""Dry-run-first, approval-gated historical acquisition runner."""

from datetime import datetime, timezone

from app.data.provider_approval import load_approval, validate_approval
from app.data.provider_contract import ProviderRequest
from app.data.provider_normalizer import normalise_batch
from app.data.providers.twelve_data import parse_request_datetime
from app.data.pilot_store import (
    commit_chunk,
    get_chunk_statuses,
    initialise_pilot_database,
    mark_chunk_failed,
    mark_chunk_running,
    register_plan,
)


def _validate_plan(plan):
    if not isinstance(plan, dict):
        raise ValueError("plan must be an object")

    if not plan.get("plan_id") or not isinstance(plan.get("chunks"), list):
        raise ValueError("plan ID and chunks are required")

    if plan.get("execution_enabled") is not False:
        raise ValueError("plan execution flag must remain disabled")

    if plan.get("market_data_written") is not False:
        raise ValueError("plan must not claim data has been written")

    if not plan["chunks"]:
        raise ValueError("plan has no chunks")

    if plan.get("chunk_count") != len(plan["chunks"]):
        raise ValueError("plan chunk count does not match")

    previous_number = 0

    for chunk in plan["chunks"]:
        number = chunk.get("chunk")
        slots = chunk.get("bar_slots")
        max_rows = chunk.get("max_rows")

        if (
            not isinstance(number, int)
            or isinstance(number, bool)
            or number != previous_number + 1
        ):
            raise ValueError("chunk numbers must be sequential")

        if (
            not isinstance(slots, int)
            or isinstance(slots, bool)
            or not 1 <= slots <= 5000
        ):
            raise ValueError("invalid chunk bar_slots")

        if (
            not isinstance(max_rows, int)
            or isinstance(max_rows, bool)
            or not 1 <= max_rows <= 5000
            or slots > max_rows
        ):
            raise ValueError("chunk exceeds its row limit")

        start = parse_request_datetime(chunk["start"])
        end = parse_request_datetime(chunk["end"])

        if start > end:
            raise ValueError("chunk start must not be after end")

        previous_number = number
def run_acquisition_plan(
    provider,
    plan,
    database_path,
    approval_path=None,
    *,
    execute=False,
    resume=False,
    max_chunks=None,
):
    """Register a plan or execute a bounded pilot run."""
    _validate_plan(plan)

    if not isinstance(execute, bool) or not isinstance(resume, bool):
        raise ValueError("execute and resume must be boolean")

    if max_chunks is not None and (
        not isinstance(max_chunks, int)
        or isinstance(max_chunks, bool)
        or max_chunks < 1
    ):
        raise ValueError("max_chunks must be positive")

    initialise_pilot_database(database_path)
    register_plan(database_path, plan)

    statuses = get_chunk_statuses(database_path, plan["plan_id"])

    if not execute:
        return {
            "status": "DRY_RUN",
            "plan_id": plan["plan_id"],
            "chunks_registered": len(statuses),
            "completed_chunks": sum(
                row["status"] == "COMPLETE" for row in statuses
            ),
            "network_requests_made": 0,
            "market_data_written": False,
        }

    if not approval_path:
        raise ValueError("explicit provider approval file is required")

    if not hasattr(provider, "metadata") or not hasattr(
        provider, "fetch_candles"
    ):
        raise ValueError("invalid historical data provider")

    attempted = 0
    inserted_total = 0
    duplicate_total = 0
    completed_total = 0
    skipped_total = 0

    for chunk in plan["chunks"]:
        chunk_no = chunk["chunk"]

        current = next(
            row for row in get_chunk_statuses(
                database_path, plan["plan_id"]
            )
            if row["chunk_no"] == chunk_no
        )

        if current["status"] == "COMPLETE":
            skipped_total += 1
            continue

        if current["status"] in {"RUNNING", "FAILED"} and not resume:
            raise ValueError(
                "unfinished chunk exists; rerun with resume=True"
            )

        if max_chunks is not None and attempted >= max_chunks:
            break

        try:
            approval = load_approval(approval_path)
            validate_approval(
                approval,
                plan["instrument"],
                plan["timeframe"],
            )

            provider.metadata.validate(
                plan["instrument"],
                plan["timeframe"],
            )

            if not mark_chunk_running(
                database_path, plan["plan_id"], chunk_no
            ):
                skipped_total += 1
                continue

            request = ProviderRequest(
                instrument=plan["instrument"],
                timeframe=plan["timeframe"],
                start=chunk["start"],
                end=chunk["end"],
            )

            attempted += 1
            observations = provider.fetch_candles(request)

            candles = normalise_batch(
                observations,
                plan["instrument"],
                plan["timeframe"],
                max_rows=chunk["max_rows"],
            )
            chunk_start = parse_request_datetime(chunk["start"])
            chunk_end = parse_request_datetime(chunk["end"])

            for candle in candles:
                candle_time = datetime.fromisoformat(
                    candle["timestamp"].replace("Z", "+00:00")
                ).astimezone(timezone.utc)

                if not chunk_start <= candle_time <= chunk_end:
                    raise ValueError(
                        "provider returned a candle outside the requested chunk"
                    )

            result = commit_chunk(
                database_path,
                plan,
                chunk_no,
                candles,
                provider.metadata,
            )

            inserted_total += result.get("inserted", 0)
            duplicate_total += result.get("duplicates", 0)
            completed_total += 1

        except Exception as exc:
            mark_chunk_failed(
                database_path,
                plan["plan_id"],
                chunk_no,
                exc,
            )
            raise

    final_statuses = get_chunk_statuses(
        database_path, plan["plan_id"]
    )
    all_complete = all(
        row["status"] == "COMPLETE" for row in final_statuses
    )

    return {
        "status": "COMPLETE" if all_complete else "PARTIAL",
        "plan_id": plan["plan_id"],
        "chunks_registered": len(final_statuses),
        "completed_this_run": completed_total,
        "skipped_completed": skipped_total,
        "network_requests_made": attempted,
        "candles_inserted": inserted_total,
        "duplicate_candles": duplicate_total,
        "market_data_written": inserted_total > 0,
        "execution_enabled": True,
    }
