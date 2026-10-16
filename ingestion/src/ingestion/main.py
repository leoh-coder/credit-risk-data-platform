"""CLI entry point.

    python -m ingestion.main run                                  # today's batch
    python -m ingestion.main run --date 2026-10-01
    python -m ingestion.main backfill --start 2025-10-01 --end 2026-10-14

`--as-of` freezes the "observation clock" (default: today). Fixing it makes
reruns byte-for-byte reproducible, which is what you want in CI.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import UTC, date, datetime, timedelta

from ingestion.config import Settings, get_settings
from ingestion.contracts import CONTRACT_VERSION, ContractViolationError, validate_entity
from ingestion.generator import GENERATOR_VERSION, BatchContext, generate_batch, inject_anomalies
from ingestion.writer import LakeWriter

logger = logging.getLogger("ingestion")


def run_batch(reference_date: date, as_of: date, settings: Settings, writer: LakeWriter) -> dict:
    started = time.perf_counter()
    batch = generate_batch(
        reference_date,
        as_of,
        seed=settings.seed,
        customers_per_batch=settings.customers_per_batch,
        history_days=settings.history_days,
    )
    batch, injected = inject_anomalies(batch, settings.dirty_rate, settings.seed, reference_date)

    # Validate everything BEFORE writing anything: a batch is all-or-nothing.
    results = {entity: validate_entity(entity, df) for entity, df in batch.items()}

    # Only after validation passed: a rejected rerun must leave the previous commit untouched.
    writer.invalidate_manifest(reference_date)
    entities = {}
    for entity, result in results.items():
        raw_path = writer.write_partition("raw", entity, reference_date, result.valid)
        writer.write_partition("quarantine", entity, reference_date, result.quarantine)
        entities[entity] = {
            "rows_received": len(result.valid) + len(result.quarantine),
            "rows_valid": len(result.valid),
            "rows_quarantined": len(result.quarantine),
            "anomalies_injected": injected[entity],
            "path": raw_path,
        }

    manifest = {
        "batch_id": BatchContext(reference_date, as_of, settings.seed).batch_id,
        "reference_date": reference_date.isoformat(),
        "as_of": as_of.isoformat(),
        "contract_version": CONTRACT_VERSION,
        "generator_version": GENERATOR_VERSION,
        "written_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "duration_seconds": round(time.perf_counter() - started, 2),
        "entities": entities,
    }
    writer.write_manifest(reference_date, manifest)  # commit marker, always last

    summary = " ".join(f"{e}={m['rows_valid']}/{m['rows_received']}" for e, m in entities.items())
    logger.info("batch=%s committed valid/received: %s", manifest["batch_id"], summary)
    return manifest


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ingestion", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="generate, validate and land one daily batch")
    run_p.add_argument("--date", type=_parse_date, default=date.today())
    run_p.add_argument("--as-of", type=_parse_date, default=None)

    bf_p = sub.add_parser("backfill", help="land one batch per day in [start, end]")
    bf_p.add_argument("--start", type=_parse_date, required=True)
    bf_p.add_argument("--end", type=_parse_date, required=True)
    bf_p.add_argument("--as-of", type=_parse_date, default=None)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")

    settings = get_settings()
    writer = LakeWriter(settings)

    if args.command == "run":
        dates = [args.date]
    else:
        if args.end < args.start:
            parser.error("--end must be on or after --start")
        dates = [args.start + timedelta(days=i) for i in range((args.end - args.start).days + 1)]

    as_of = args.as_of or max(date.today(), dates[-1])
    logger.info(
        "landing %d batch(es) into %s (backend=%s, as_of=%s)",
        len(dates),
        writer.root,
        settings.storage_backend,
        as_of,
    )

    failed = []
    for d in dates:
        try:
            run_batch(d, as_of, settings, writer)
        except ContractViolationError as exc:
            logger.error("batch for %s REJECTED: %s", d, exc)
            failed.append(d)

    if failed:
        logger.error("%d batch(es) rejected: %s", len(failed), [d.isoformat() for d in failed])
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
