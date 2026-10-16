"""Exercises the real S3 code path (pyarrow S3FileSystem) against an in-process S3 emulator."""

import json
import socket

import pyarrow.dataset as ds
import pytest

from ingestion.config import Settings
from ingestion.main import run_batch
from ingestion.writer import LakeWriter
from tests.conftest import AS_OF, REFERENCE_DATE

moto_server = pytest.importorskip("moto.server")


@pytest.fixture(scope="module")
def s3_endpoint():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = moto_server.ThreadedMotoServer(ip_address="127.0.0.1", port=port)
    server.start()
    yield f"http://127.0.0.1:{port}"
    server.stop()


def test_batch_lands_on_s3_compatible_storage(s3_endpoint):
    settings = Settings(
        _env_file=None,
        storage_backend="s3",
        s3_endpoint_url=s3_endpoint,
        aws_access_key_id="test",
        aws_secret_access_key="test",
        customers_per_batch=40,
    )
    writer = LakeWriter(settings)
    manifest = run_batch(REFERENCE_DATE, AS_OF, settings, writer)

    partition = f"{settings.lake_bucket}/raw/transactions/reference_date={REFERENCE_DATE}"
    dataset = ds.dataset(partition, format="parquet", filesystem=writer.fs)
    assert dataset.count_rows() == manifest["entities"]["transactions"]["rows_valid"]

    manifest_key = f"{settings.lake_bucket}/_manifests/reference_date={REFERENCE_DATE}/manifest.json"
    with writer.fs.open_input_stream(manifest_key) as f:
        assert json.loads(f.read())["batch_id"] == manifest["batch_id"]
