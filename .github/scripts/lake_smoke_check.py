"""Post-backfill assertions on the lake, run inside the ingestion image (it already has pyarrow + S3 env).

    docker compose run --rm -T --entrypoint python ingestion - 2026-01-01 2026-01-03 \\
        < .github/scripts/lake_smoke_check.py
"""

import json
import os
import sys
from datetime import date, timedelta
from urllib.parse import urlparse

import pyarrow.parquet as pq
from pyarrow import fs

ENTITIES = ("customers", "credit_operations", "installments", "transactions")

start, end = (date.fromisoformat(a) for a in sys.argv[1:3])
expected_dates = [start + timedelta(days=i) for i in range((end - start).days + 1)]

endpoint = urlparse(os.environ["S3_ENDPOINT_URL"])
s3 = fs.S3FileSystem(
    endpoint_override=endpoint.netloc,
    scheme=endpoint.scheme,
    access_key=os.environ["AWS_ACCESS_KEY_ID"],
    secret_key=os.environ["AWS_SECRET_ACCESS_KEY"],
    region=os.environ.get("AWS_REGION", "us-east-1"),
)
bucket = os.environ["LAKE_BUCKET"]

errors = []
committed = sorted(
    info.base_name.removeprefix("reference_date=")
    for info in s3.get_file_info(fs.FileSelector(f"{bucket}/_manifests"))
)
if committed != [d.isoformat() for d in expected_dates]:
    errors.append(f"committed dates {committed} != expected {[d.isoformat() for d in expected_dates]}")

for d in expected_dates:
    manifest_path = f"{bucket}/_manifests/reference_date={d}/manifest.json"
    try:
        with s3.open_input_stream(manifest_path) as fh:
            manifest = json.loads(fh.read())
    except FileNotFoundError:
        errors.append(f"{d}: manifest missing")
        continue
    for entity in ENTITIES:
        stats = manifest["entities"][entity]
        part = f"{bucket}/raw/{entity}/reference_date={d}/part-0.parquet"
        rows = pq.read_metadata(part, filesystem=s3).num_rows
        if not 0 < rows == stats["rows_valid"]:
            errors.append(f"{d} {entity}: parquet rows={rows}, manifest rows_valid={stats['rows_valid']}")
        # Deterministic generator: every quarantined row must be one we injected, and vice versa.
        quarantined, injected = stats["rows_quarantined"], stats["anomalies_injected"]
        if quarantined != injected:
            errors.append(f"{d} {entity}: quarantined={quarantined} != injected={injected}")
    counts = " ".join(f"{e}={manifest['entities'][e]['rows_valid']}" for e in ENTITIES)
    print(f"OK {d} batch={manifest['batch_id']} {counts}")

if errors:
    print("SMOKE CHECK FAILED:\n  " + "\n  ".join(errors))
    sys.exit(1)
print(f"smoke check passed: {len(expected_dates)} committed date(s), {len(ENTITIES)} entities each")
